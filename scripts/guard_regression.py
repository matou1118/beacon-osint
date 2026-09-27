"""栏目页守卫改动的回归：拿库里全部真实标题跑新旧两套规则对比。

为什么必须有这一步
  新守卫改成"先剥站点名后缀、再判左侧长度"。这个改法理论上对，但它是一把双刃剑：
  阈值定松了漏放栏目页，定紧了会把真公告一起杀掉。而"哪些标题是真公告"只有
  库里的真实数据知道，靠想是想不到的。

输出三部分
  1. 新增拦下的（期望是栏目页）
  2. 依旧漏放的（需要人工看，可能还有别的形态）
  3. 危险信号：新旧都放行、但左侧短到可疑的（可能误杀的真公告）

不写库，纯读。
"""
from __future__ import annotations

import argparse
import collections
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def old_guard(title: str, exact: frozenset[str], markers: tuple[str, ...],
              tail: re.Pattern[str]) -> bool:
    """改动前的判定，保留一份用于对比。"""
    value = re.sub(r"[\s　_|-]+", "", title or "")
    if not value:
        return False
    if value in exact:
        return True
    if any(re.sub(r"[\s　_|-]+", "", m) in value for m in markers):
        return True
    if tail.search(re.sub(r"[\s　|]+", "", title or "")):
        return True
    return len(value) <= 24 and value.endswith(("厅", "局", "政府", "学院", "中心"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(ROOT / "data" / "beacon.db"))
    parser.add_argument("--show", type=int, default=40, help="每类最多列出多少条")
    parser.add_argument("--out", default=str(ROOT / "data" / "guard_regression.txt"))
    args = parser.parse_args()

    from collectors.html_list import HtmlListCollector  # noqa: PLC0415

    probe = HtmlListCollector.__new__(HtmlListCollector)  # 只借方法，不跑 __init__

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, source_id, title FROM announcements WHERE title IS NOT NULL"
    ).fetchall()
    print(f"库内标题 {len(rows)} 条\n", flush=True)

    old_tail = re.compile(
        r"[一-龥]{2,10}_[一-龥]{4,20}(?:门户网站|人民政府|政府网)$")

    newly: list[tuple] = []
    still: list[tuple] = []
    risky: list[tuple] = []
    for rid, source_id, title in rows:
        new_hit = probe._is_section_page(title)
        old_hit = old_guard(title, probe.SECTION_TITLE_EXACT,
                            probe.SECTION_TITLE_MARKERS, old_tail)
        if new_hit and not old_hit:
            newly.append((rid, source_id, title))
        elif not new_hit:
            core = re.sub(r"[\s　_|-]+", "", probe._strip_site_suffix(title))
            still.append((rid, source_id, title, core))

    # 危险信号：没被拦、但剥完只剩很短的左侧，且标题本身不算长。
    # 这种最像是"真文档被误判成栏目页"或反过来"栏目页混进来了"。
    for rid, source_id, title, core in still:
        if 0 < len(core) <= 14 and len(title) <= 30 and "-" in title or "－" in title or "_" in title:
            risky.append((rid, source_id, title, core))

    lines: list[str] = []
    add = lines.append
    add("=" * 72)
    add(f"新增拦下（被判定为栏目页）：{len(newly)} 条")
    add("=" * 72)
    for rid, source_id, title in newly[:args.show]:
        core = re.sub(r"[\s　_|-]+", "", probe._strip_site_suffix(title))
        add(f"  {source_id:<18} 剥后={core[:24]:<26} 原题={title[:52]}")
    if len(newly) > args.show:
        add(f"  ... 还有 {len(newly) - args.show} 条")
    add("")
    add("新增拦下的按源分布：")
    for source_id, count in collections.Counter(s for _r, s, _t in newly).most_common(20):
        add(f"  {count:>4}  {source_id}")

    add("")
    add("=" * 72)
    add(f"危险信号（未拦、但剥完左侧很短且带分隔符，需人工确认是栏目页还是真公告）："
        f"{len(risky)} 条")
    add("=" * 72)
    for rid, source_id, title, core in risky[:args.show]:
        add(f"  {source_id:<18} 剥后={core[:20]:<22} 原题={title[:56]}")
    if len(risky) > args.show:
        add(f"  ... 还有 {len(risky) - args.show} 条")

    add("")
    add("=" * 72)
    add(f"依旧放行：{len(still)} 条（这是正常主体，真正的公告都在这里）")
    add("=" * 72)
    by_source = collections.Counter(s for _r, s, _t, _c in still)
    add(f"  放行最多的源：{by_source.most_common(8)}")

    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:60]))
    print(f"\n完整报告写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
