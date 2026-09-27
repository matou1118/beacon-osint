"""Recompute stored summaries with the current summariser.

Needed because the summary engine improves: 167 of 388 stored rows had an empty
`extra["summary"]` even though re-running `summarize()` on their stored body
produced 300+ chars -- some collection path stored the body without summarising
it. Without this pass, better cleaning never reaches the data.

Reads `content` only; never rewrites it. `extra` is merged, not replaced, so
source_type/issuing_body and friends survive.

Run: python scripts/resummarize.py            # dry run, prints the plan
     python scripts/resummarize.py --apply    # writes, after backing up
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "beacon.db"
BACKUP = ROOT / "data" / "resummarize_backup.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.clean_summarize import NAV_RUN_THRESHOLD, nav_label_hits  # noqa: E402
# The pipeline's own entry point (extractors/regex_fallback.py): build_summary
# runs strip_chrome + dedupe_sentences and then the self-correcting refine pass.
# Calling clean_summarize.summarize() here instead produced unrefined summaries
# and pushed summary_breadcrumb_rate from 0.00 to 0.35.
from extractors.summary_refine import build_summary  # noqa: E402

CHROME = re.compile(r"来源[:：]|索引号|文号[:：]|\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}")
BUSY_TIMEOUT_MS = 60000


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    # The schedulers write concurrently; wait for the writer instead of failing.
    con.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return con


def backup(con: sqlite3.Connection) -> None:
    """Online backup, not shutil.copy2.

    The schedulers write while this runs, and a file copy of a live SQLite
    database can tear into an unreadable backup. The backup API takes a
    consistent snapshot page by page.
    """
    dest = sqlite3.connect(BACKUP)
    try:
        con.backup(dest)
    finally:
        dest.close()


def plan(con: sqlite3.Connection) -> tuple[list[dict], Counter]:
    updates: list[dict] = []
    stats: Counter = Counter()
    for row in con.execute("SELECT id, source_id, title, content, extra, key_points, "
                           "amount, deadline, target_group, institution_name, publish_date "
                           "FROM announcements"):
        try:
            extra = json.loads(row["extra"] or "{}")
        except (TypeError, ValueError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        old = str(extra.get("summary") or "").strip()
        # 结构化字段（金额/截止/申报对象/责任机构）传给摘要，由它拼成头部事实行。
        # 这些列只有 10%/6% 有值（policy_fields.BODY_PATTERNS 的线索词原先太窄），
        # 但有值时比在句子堆里碰运气可靠得多，所以直接用。
        facts = {key: row[key] for key in
                 ("amount", "deadline", "target_group", "institution_name", "publish_date")
                 if row[key] not in (None, "")}
        result = build_summary(str(row["content"] or ""), str(row["title"] or ""), facts=facts)
        new = str(result.get("summary") or "").strip()

        # The pipeline stores the cleaned body too (regex_fallback: content =
        # clean_text), so the body has to be refreshed or chrome_residue_rate
        # stays high. An empty clean_text never overwrites the stored body:
        # losing text is worse than keeping chrome.
        old_content = str(row["content"] or "")
        new_content = str(result.get("clean_text") or "").strip()
        content = new_content if new_content and new_content != old_content else None
        if content is not None:
            stats["content_cleaned"] += 1

        if not old and not new:
            # 摘要两侧都空，但 key_points 列可能还留着上一轮的旧值。
            # 实测 id=1601（新疆政府网站一览表，content 只剩"访问量：标签"11 字）
            # extra.summary 已是空、extra.drop_reason=chrome_only，而
            # key_points 列里仍有 1814 字的旧导航摘要——因为这个分支无条件
            # continue，列永远没机会被清掉。摘要为空却还挂着 1814 字要点，
            # 下游读列就以为有内容。
            try:
                stale_points = json.loads(row["key_points"] or "[]")
            except (TypeError, ValueError):
                stale_points = []
            if stale_points:
                updates.append({
                    "id": row["id"],
                    "extra": json.dumps(dict(extra, summary="", key_points=[]),
                                         ensure_ascii=False),
                    "content": None, "old_len": 0, "new_len": 0,
                    "key_points": "[]",
                })
                stats["stale_key_points_cleared"] += 1
                continue
            stats["both_empty"] += 1
            if content is None:
                continue
        elif not old and new:
            stats["filled"] += 1
        elif old and not new:
            # A nav-bar body: the old summary was garbage, an empty one is honest.
            stats["emptied"] += 1
        elif old != new:
            stats["changed"] += 1
        else:
            # "摘要没变"不等于"不用写"：key_points 列和 extra["summary"] 可能已经
            # 不同步。这个脚本原来只更新 extra，产品读的是 key_points 那一列，
            # 于是摘要改进全部落在没人消费的地方。而一旦 extra 先被修好，
            # 下一轮就会因为 old == new 而跳过，key_points 永远补不上。
            # 所以跳过条件必须同时看列。
            try:
                stored_points = json.loads(row["key_points"] or "[]")
            except (TypeError, ValueError):
                stored_points = []
            if stored_points == (result.get("key_points") or []):
                stats["unchanged"] += 1
                if content is None:
                    continue
            else:
                stats["key_points_synced"] += 1

        if new and (CHROME.search(new) or nav_label_hits(new) >= NAV_RUN_THRESHOLD):
            stats["still_dirty"] += 1
        merged = dict(extra)
        merged["summary"] = new
        merged["key_points"] = result.get("key_points") or []
        merged["extraction_tags"] = result.get("tags") or []
        merged["summary_repairs"] = result.get("repairs") or []
        if result.get("drop_reason"):
            merged["summary_drop_reason"] = result["drop_reason"]
        else:
            merged.pop("summary_drop_reason", None)
        updates.append({"id": row["id"], "extra": json.dumps(merged, ensure_ascii=False),
                        "content": content, "old_len": len(old), "new_len": len(new),
                        # 关键：key_points 必须写进真正的列。
                        # 这个脚本原来只更新 extra["key_points"]，而 app.py 与
                        # api/services/announcement_service.py 读的是
                        # announcements.key_points 那一列——于是摘要质量的所有改进
                        # 都落在产品不消费的地方，界面上看不出任何变化。
                        "key_points": json.dumps(result.get("key_points") or [],
                                                  ensure_ascii=False)})
    return updates, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (backs up first)")
    args = parser.parse_args()

    con = connect()
    try:
        updates, stats = plan(con)
    finally:
        con.close()

    gained = sum(u["new_len"] - u["old_len"] for u in updates)
    report = {
        "rows_scanned": sum(stats.values()),
        "to_update": len(updates),
        **dict(stats),
        "summary_chars_delta": gained,
        "db": str(DB),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("\ndry run: nothing written. re-run with --apply")
        return 0

    # Backup on its own connection: sqlite3_backup deadlocks if the source is
    # inside a write transaction.
    snapshot = connect()
    try:
        backup(snapshot)
    finally:
        snapshot.close()
    print(f"backup -> {BACKUP}")

    # Reading and writing are separate connections on purpose. A scheduler that
    # rewrites `extra` between the two loses nothing: the summary is a pure
    # function of `content`, so recomputing it is idempotent.
    # content_hash is deliberately left alone: it is computed from the freshly
    # scraped body, not the cleaned one, so rewriting `content` without it keeps
    # deduplication working on the next collection run.
    now = datetime.now(timezone.utc).isoformat()
    con = connect()
    try:
        con.executemany(
            "UPDATE announcements SET extra = ?, key_points = ?, "
            "content = COALESCE(?, content), updated_at = ? WHERE id = ?",
            [(u["extra"], u["key_points"], u["content"], now, u["id"]) for u in updates],
        )
        con.commit()
    finally:
        con.close()
    print(f"updated {len(updates)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
