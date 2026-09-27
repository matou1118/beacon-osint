"""看 amounts 表的年份到底靠不靠谱——跨年对比全靠它。

三个疑点要先量化：
  1. year 取的是"命中处附近第一个年份"，会不会取到无关年份
  2. 出现 2027（规划目标年）和 2014，与当年实际拨款混在一起
  3. 语料是不是几乎只有一年，跨年对比根本无从谈起

正则必须带分隔符：`\d{4}` 单独用会把金额里的数字读成年份——最早这脚本写成
`(19|20)\d{2}`，于是「1923610万元」被读成 1923 年，50 条全是这种假警报。
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]

con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)

print("=== 年份分布 ===")
for year, n in con.execute(
        "SELECT year, COUNT(*) FROM amounts GROUP BY year ORDER BY year"):
    print(f"  {year}  {n:>4}")

# 年份必须带分隔符（年/-/.），否则会从金额数字里读出来
YEAR = re.compile(r"((?:19|20)\d{2})\s*(?:年|[-/.]|$)")


def years_in(text: str) -> set[int]:
    return {int(m.group(1)) for m in YEAR.finditer(text or "")}


print("\n=== 年份来源：正文年份 vs 发布日期回退 ===")
rows = con.execute("""
    SELECT a.id, a.year, a.raw, a.evidence, p.publish_date, a.label
    FROM amounts a JOIN announcements p ON p.id = a.announcement_id
    ORDER BY a.id
""").fetchall()
from_body = sum(1 for r in rows if years_in(r[3]))
print(f"  evidence 里带年份（可能来自正文）: {from_body} / {len(rows)}")
print(f"  靠 publish_date 回退:              {len(rows) - from_body}")

print("\n=== 抽样：year 与 evidence 里的年份是否一致 ===")
mismatch = []
for rid, year, raw, ev, pub, label in rows:
    evy = years_in(ev)
    if evy and year not in evy:
        mismatch.append((rid, year, sorted(evy), raw, label, pub))
print(f"  year 不在 evidence 年份里的: {len(mismatch)}")
for rid, year, evy, raw, label, pub in mismatch[:10]:
    print(f"    [{rid}] year={year} evidence里={evy} {raw[:16]} {label} 发布={str(pub)[:10]}")

print("\n=== 规划目标年（发布年之外的未来年）===")
future = con.execute("""
    SELECT COUNT(*) FROM amounts a JOIN announcements p ON p.id=a.announcement_id
    WHERE a.year IS NOT NULL AND p.publish_date IS NOT NULL
      AND a.year > CAST(substr(p.publish_date,1,4) AS INTEGER)
""").fetchone()[0]
print(f"  year 晚于发布年的: {future}  （如「到2030年实现…」，属规划目标）")

print("\n=== 按标签看年份跨度 ===")
for label, n, ymin, ymax in con.execute(
        "SELECT label, COUNT(*), MIN(year), MAX(year) FROM amounts "
        "GROUP BY label HAVING COUNT(*) >= 3 ORDER BY 2 DESC LIMIT 8"):
    span = f"{ymin}-{ymax}" if ymin and ymax else "-"
    print(f"  {label:<10} {n:>4} 条  {span}")
