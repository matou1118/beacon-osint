"""给已知脏数据打标：栏目页、重复记录、404 页。**只 UPDATE，不 DELETE。**

为什么只打标不删
----------------
删除不可逆。``scripts/dedup.py`` 是会 ``DELETE`` 的，但打标（``needs_review`` +
``archived_at``）可逆：复核完把标记清掉，记录原样回来。而下游已经有地方认这两个
标记——``lighthouse/publish.py`` 的成刊会跳过它们，``scripts/mark_portal_pages.py``
的门户页标记也是同一套。

要物理删除请显式跑 ``python scripts/dedup.py``（它会备份 + 报告）。

三类
----
1. **栏目页**：``lighthouse.quality.judge`` 判 ``listing_page``。这类页面在翻页时
   被反复当成详情页抓——全库曾出现同一个「山东省人社厅」栏目页 64 条重复。
2. **正文重复**：``content_fingerprint`` 相同。同一个 URL 的 http/https 两个协议
   地址、或者同一文件被两个源转载时，``content_hash``（含 URL）认不出来。
3. **404/死页**：正文里就写着「404」「找不到文件」。

只用 ``needs_review`` 标记，**不碰 ``archived_at``**：那一列是 ``DATETIME``，
往里写字符串会让 SQLAlchemy 解析 ISO 日期失败，整个工作台 15 个视图一起崩
（2026-10-01 实测）。复核完把 ``needs_review`` 清回 0 就恢复了。

指纹那份的根因已经修在 ``lighthouse/run.py``（以前 lighthouse 从不设指纹，
全库 6980 条是空的，所以去重几乎失效）。本脚本只是把存量补上。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lighthouse.quality import judge, load_config  # noqa: E402

DB = _ROOT / "data" / "beacon.db"
DEAD_RE = ("404", "找不到文件", "页面不存在", "文件或目录", "抱歉，您访问")


def log(message: str) -> None:
    print(message, flush=True)


def find_listing(cfg: dict) -> list[int]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, content FROM announcements").fetchall()
    finally:
        con.close()
    return [rid for rid, body in rows
            if judge(body or "", cfg).reason == "listing_page"]


def find_duplicates() -> list[int]:
    """每组正文指纹相同，保留 id 最小的，其余打标。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        groups = con.execute(
            "SELECT content_fingerprint, GROUP_CONCAT(id) FROM announcements "
            "WHERE content_fingerprint IS NOT NULL AND content_fingerprint <> '' "
            "GROUP BY content_fingerprint HAVING COUNT(*) > 1"
        ).fetchall()
    finally:
        con.close()
    dup: list[int] = []
    for _fp, id_list in groups:
        ids = sorted(int(x) for x in id_list.split(","))
        dup.extend(ids[1:])          # 保留最早入库的那条
    return dup


def find_dead() -> list[int]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, title, content FROM announcements "
            "WHERE archived_at IS NULL").fetchall()
    finally:
        con.close()
    out = []
    for rid, title, body in rows:
        head = ((title or "") + (body or ""))[:200]
        if any(mark in head for mark in DEAD_RE):
            out.append(rid)
    return out


def apply(ids: list[int], label: str) -> int:
    if not ids:
        return 0
    con = sqlite3.connect(DB)
    try:
        con.executemany("UPDATE announcements SET needs_review=1 WHERE id=?",
                        [(rid,) for rid in ids])
        con.commit()
    finally:
        con.close()
    log(f"  {label}：标记 {len(ids)} 条")
    return len(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给脏数据打标（不删除）")
    parser.add_argument("--dry-run", action="store_true", help="只报不写")
    args = parser.parse_args(argv)

    cfg = load_config()
    log("统计中（全库 9473 条左右，要跑一会儿）…")
    listing = find_listing(cfg)
    dups = find_duplicates()
    dead = find_dead()
    total = len(set(listing) | set(dups) | set(dead))
    log(f"栏目页 {len(listing)} | 正文重复 {len(dups)} | 死页 {len(dead)}"
        f" | 合计待标 {total}")

    if args.dry_run:
        log("--dry-run，未写库")
        return 0

    from collectors.lock import CollectLockBusy, collect_lock

    try:
        with collect_lock("flag_dirty_records"):
            apply(listing, "栏目页")
            apply(dups, "正文重复")
            apply(dead, "死页")
    except CollectLockBusy as exc:
        log(f"采集器正在运行，跳过（不打断采集）：{exc}")
        return 0

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        left = con.execute(
            "SELECT COUNT(*) FROM announcements "
            "WHERE archived_at IS NULL AND COALESCE(needs_review,0)=0").fetchone()[0]
        flagged = con.execute(
            "SELECT COUNT(*) FROM announcements "
            "WHERE COALESCE(needs_review,0)=1").fetchone()[0]
    finally:
        con.close()
    log(f"完成。干净 {left} 条 | 已标待复核 {flagged} 条")
    log("物理删除请显式跑 python scripts/dedup.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
