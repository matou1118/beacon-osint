"""删掉被栏目页守卫判定为栏目页的记录。

为什么单独一个脚本
  dedup.py 只处理 chrome 与重复，不碰"标题是栏目名"这一类——判定要靠
  collectors 的 _is_section_page，而删除动作必须留痕、可复核，所以不塞进 dedup。

安全约束
  - 只删 titles 被判为栏目页的行，不碰 content
  - 连带清 field_extractions / match_results，避免留下悬空外键
  - 默认 dry-run，加 --apply 才落盘
  - 调用前应已有 data/backups/*-pre-section-prune.db 备份

用法
  python scripts/prune_section_pages.py            # 预览
  python scripts/prune_section_pages.py --apply    # 落盘
"""
from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "beacon.db"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", type=int, default=20)
    args = parser.parse_args()

    from collectors.html_list import HtmlListCollector

    probe = HtmlListCollector.__new__(HtmlListCollector)  # 只借方法，不跑 __init__
    con = sqlite3.connect(args.db)
    before = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    rows = con.execute("SELECT id, source_id, title FROM announcements "
                       "WHERE title IS NOT NULL").fetchall()
    targets = [(rid, source_id, title) for rid, source_id, title in rows
               if probe._is_section_page(title)]

    print(f"库内 {before} 条，其中被判为栏目页 {len(targets)} 条")
    for rid, source_id, title in targets[:args.show]:
        print(f"  {source_id:<16} {title[:56]}")
    if len(targets) > args.show:
        print(f"  ... 还有 {len(targets) - args.show} 条")
    print("按源:", dict(collections.Counter(s for _r, s, _t in targets).most_common(12)))

    if not targets:
        return 0
    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 落盘）")
        return 0

    ids = [rid for rid, _s, _t in targets]
    placeholders = ",".join("?" * len(ids))
    con.execute(f"DELETE FROM field_extractions WHERE announcement_id IN ({placeholders})", ids)
    con.execute(f"DELETE FROM match_results WHERE announcement_id IN ({placeholders})", ids)
    deleted = con.execute(
        f"DELETE FROM announcements WHERE id IN ({placeholders})", ids).rowcount
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    print(f"\n已删除 {deleted} 条：{before} -> {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
