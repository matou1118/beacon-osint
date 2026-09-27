"""用真实分类器重跑全库，报告效果。

为什么要写脚本而不是靠 scripts/resummarize.py 顺带做
  分类和摘要是两件事：摘要是逐条重算，分类是重新打分后需要写回 category_l1/l2。
  混在一起会让"摘要修好了但分类没修"这类问题被一个总数掩盖。

判据
  - 兜底率（category_l1=其他）必须下降，且不能靠把东西塞进别的类达成
  - 抽检分对分错，不看总体数字
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", type=int, default=25)
    args = parser.parse_args()

    from extractors.category_rules import classify

    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title, content, category_l1, category_l2 FROM announcements"
    ).fetchall()
    total = len(rows)
    before = collections.Counter((r[4], r[5]) for r in rows)
    before_other = sum(n for (c, _s), n in before.items() if c == "其他")
    print(f"总量 {total} 条\n")

    results = []
    for rid, source_id, title, content, old1, old2 in rows:
        got = classify({"title": title or "", "content": content or "",
                        "source": {"id": source_id}})
        results.append((rid, source_id, title, old1, old2,
                        got["category_l1"], got["category_l2"]))
        if not args.apply:
            continue

    after = collections.Counter((r[5], r[6]) for r in results)
    after_other = sum(n for (c, _s), n in after.items() if c == "其他")
    print(f"=== 分类分布（重跑后，{len(after)} 个组合）===")
    for (cat, sub), n in after.most_common(28):
        mark = "   <- 兜底" if cat == "其他" else ""
        print(f"   {cat:<14} {sub:<14} {n:>5}  {n/total:>5.0%}{mark}")

    print(f"\n落「其他」: {before_other}/{total} = {before_other/total:.0%}"
          f"  ->  {after_other}/{total} = {after_other/total:.0%}")
    if args.apply:
        print(f"   变化 {after_other - before_other:+d} 条")
    else:
        print("\n（预览模式，未写入。加 --apply 落盘）")
        return 0

    # 只写真正变了的行。流水线里这一步原本 129 秒，而实际只有新增/规则改动的
    # 那些行需要落库——每轮把 2000 行全 UPDATE 一遍既慢又会在 WAL 里留下
    # 无谓的写入。分类本身是全量重算的（规则可能整体改动），但落库是增量的。
    changed = [(r[5], r[6], r[0]) for r in results
               if r[3] != r[5] or r[4] != r[6]]
    write = sqlite3.connect(ROOT / "data" / "beacon.db")
    try:
        write.executemany(
            "UPDATE announcements SET category_l1 = ?, category_l2 = ? WHERE id = ?",
            changed)
        write.commit()
    finally:
        write.close()
    print(f"重算 {len(results)} 条，实际写回 {len(changed)} 条"
          f"（其余 {len(results) - len(changed)} 条分类未变）")

    print(f"\n=== 抽 {args.show} 条看分得对不对 ===")
    for r in results[:args.show]:
        flag = "   ★仍是兜底" if r[5] == "其他" else ""
        print(f"   [{r[5]}/{r[6]:<12}] {r[2][:46]}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
