"""新旧摘要 A/B 实测：拿全库真实记录跑一遍新判据和新摘要，看差在哪。

这是回答「到底有没有提高」的唯一办法——不看演示，只看全库数字。

    python lighthouse/bench.py                 # 默认全库
    python lighthouse/bench.py --limit 50      # 抽样，附带逐条新旧对照

只读，不写库。跑完给三组数：
  1. 新门禁会放行 / 拦下多少，以及拦下的理由分布
  2. 句中硬切率、表格倒灌率的新旧对比（原来分别是 26.4% / 9.8%）
  3. 逐条新旧摘要对照（--limit 时打印）
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

from lighthouse.quality import build_summary, extract_fields, judge, load_config  # noqa: E402

DB = _ROOT / "data" / "beacon.db"
TERMINATORS = "。！？；"


def old_summary_stats(rows: list[tuple]) -> Counter:
    """旧摘要的硬指标，与 bench 新算的放在一起比。"""
    stats = Counter()
    for row in rows:
        extra = row[3]
        try:
            summary = str((json.loads(extra or "{}")).get("summary") or "").strip()
        except (TypeError, ValueError):
            summary = ""
        if not summary:
            stats["empty"] += 1
            continue
        stats["total"] += 1
        if summary[-1] not in TERMINATORS:
            stats["mid_sentence_cut"] += 1
        if summary.count("|") >= 3:
            stats["table_rows"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lighthouse 新旧摘要 A/B 实测")
    parser.add_argument("--limit", type=int, default=0, help=">0 时打印逐条对照")
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--apply", action="store_true",
                        help="把新摘要写回 announcements 表（先自动备份）")
    args = parser.parse_args(argv)

    cfg = load_config()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, title, content, extra, source_id FROM announcements ORDER BY id"
    ).fetchall()
    _row_source = {r[0]: r[4] for r in rows}
    con.close()

    # 源的人工登记名。正则抽机构名不可靠（会把「四川省农业农村厅 四川省财政厅」
    # 抽成「财政局」），config/sources.yaml 的 name 是逐源核过的，优先用它。
    source_names: dict[str, str] = {}
    try:
        import yaml

        with open(_ROOT / "config" / "sources.yaml", "r", encoding="utf-8") as handle:
            for item in (yaml.safe_load(handle) or {}).get("sources") or []:
                if item.get("id") and item.get("name"):
                    source_names[str(item["id"])] = str(item["name"])
    except Exception:                                       # noqa: BLE001
        pass

    if not rows:
        print("库里没有记录")
        return 1

    old = old_summary_stats(rows)
    reasons: Counter = Counter()
    new_mid = new_table = new_empty = new_ok = 0
    samples: list[tuple] = []
    new_summaries: dict[int, str] = {}

    for _id, title, content, _extra, _sid in rows:
        verdict = judge(content or "", cfg)
        if not verdict.passed:
            reasons[verdict.reason] += 1
            continue
        fields = extract_fields(content or "", cfg)
        # 发布机构以 sources.yaml 的人工登记值为准，正则只作兜底。
        # 正则抽不完整：实测把「四川省农业农村厅 四川省财政厅」抽成「财政局」。
        if source_names.get(str(_row_source.get(_id, ""))):
            fields["issuing_body"] = source_names[str(_row_source.get(_id, ""))]
        summary, _reason = build_summary(title or "", content or "", cfg, fields)
        if not summary:
            new_empty += 1
            continue
        new_ok += 1
        new_summaries[int(_id)] = summary
        if summary[-1] not in TERMINATORS:
            new_mid += 1
        if summary.count("|") >= 3:
            new_table += 1
        if len(samples) < args.limit:
            try:
                old_text = str((json.loads(_extra or "{}")).get("summary") or "")
            except (TypeError, ValueError):
                old_text = ""
            samples.append((_id, title or "", old_text, summary))

    total = len(rows)
    gate_pass = new_ok + new_mid + new_table
    print("=" * 78)
    print(f"全库 {total} 条　新门禁放行 {gate_pass}（{100*gate_pass/total:.1f}%）　"
          f"拦下 {total-gate_pass}（{100*(total-gate_pass)/total:.1f}%）")
    print(f"放行理由分布：{dict(reasons.most_common())}")
    print()
    print(f"{'指标':<16}{'旧':>10}{'新':>10}")
    print("-" * 78)
    print(f"{'句中硬切率':<14}{100*old['mid_sentence_cut']/max(1,old['total']):>9.1f}%"
          f"{100*new_mid/max(1,new_ok):>9.1f}%")
    print(f"{'表格倒灌率':<14}{100*old['table_rows']/max(1,old['total']):>9.1f}%"
          f"{100*new_table/max(1,new_ok):>9.1f}%")
    print(f"{'空摘要':<16}{old['empty']:>10}{new_empty:>10}")
    print()

    for _id, title, old_text, new_text in samples:
        print("=" * 78)
        print(f"id={_id}  {title[:60]}")
        print("【旧】", (old_text or "(空)")[:280])
        print("【新】", new_text[:280])

    if args.apply:
        return _apply(args.db, new_summaries, new_empty)
    return 0


def _apply(db_path: str, summaries: dict[int, str], empty: int) -> int:
    """把新摘要写回 announcements。

    两条规矩，都是照着 scripts/resummarize.py 的做法来的：

    1. 备份用 sqlite **在线备份 API**，不是 shutil.copy2。调度器可能正在写，
       直接复制一个活跃的 SQLite 文件会撕出坏库（resummarize.py:50 记着这个坑）。
    2. **不碰 content**。正文归采集层，摘要层动正文会造成「摘要与正文对不上」，
       resummarize.py 的 docstring 明确记着这个教训。只改 extra 和 key_points。
    """
    import sqlite3
    from datetime import datetime

    if not summaries:
        print("没有可写入的摘要")
        return 0

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = DB.parent / f"resummarize_backup-{stamp}.db"
    src = sqlite3.connect(db_path, timeout=60)
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print(f"已备份 {backup_path.name}")

    con = sqlite3.connect(db_path, timeout=60)
    con.execute("PRAGMA busy_timeout = 60000")
    now = datetime.now().isoformat(timespec="seconds")
    payload: list[tuple] = []
    for row_id, summary in summaries.items():
        row = con.execute("SELECT extra FROM announcements WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            continue
        try:
            extra = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        extra["summary"] = summary
        extra["summary_status"] = "ok"
        extra["summary_engine"] = "lighthouse-field-assembly"
        payload.append((json.dumps(extra, ensure_ascii=False),
                        json.dumps([summary], ensure_ascii=False), now, row_id))

    con.executemany(
        "UPDATE announcements SET extra = ?, key_points = ?, updated_at = ? WHERE id = ?",
        payload,
    )
    con.commit()
    con.close()
    print(f"写入 {len(payload)} 条 | 保持空摘要 {empty} 条（正文确实没有可摘要的内容）")
    print(f"备份 {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
