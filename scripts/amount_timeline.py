"""金额时间轴：按标签跨年对比。

用户要的是「时间轴会看到不同时间的各种数据来对比，金额是其中重要数据」。
amounts 表已经能按 (year, label) 查了，这个脚本就是把它显示出来，并且把两件
容易误导的事摆在最前面：

1. 不做跨文档求和
   第一版这里按年 SUM，输出「预算金额 2026 合计 1,563,337 亿元」= 156 万亿。
   这个数字没有任何意义：同一个标签下混着「中央预算支出51948亿元」这种全国
   总量和某个项目的采购预算，加起来既不是谁的预算，也不是任何真实数。同理，
   基于求和的"同比"也是假的。
   时间轴要看的是**各个数据点本身**随年份的分布，所以这里列条目、给中位数和
   极值，不给合计。同比只在同一标签、同一年、样本量足够时才提示。

2. 目标年 vs 实际年
   「到2030年实现增加值60万亿」里的 60万亿 属于 2030，是**规划目标**；
   「2024年预算支出51948亿元」里的数字属于 2024，是**已发生**。它们都存在
   amounts.year 里，直接按年混在一起会把目标和实际连成一条线。所以按 year 与
   该公告发布年份的关系分开：year <= 发布年算"已发生"，> 发布年算"规划目标"。
   这个区分不需要改表——join 一次 announcements 就能算出来。
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]

# 每年最多列几条明细，剩下只给统计量
SHOW = 3


def _yi(v: float) -> str:
    """按量级选单位，免得 156 万亿那种数字占满屏幕。"""
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.2f} 万亿元"
    if a >= 1e8:
        return f"{v / 1e8:,.2f} 亿元"
    if a >= 1e4:
        return f"{v / 1e4:,.2f} 万元"
    return f"{v:,.0f} 元"


def main() -> int:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # 目标年标记：year 晚于该公告发布年 = 规划目标
    con.execute("""
        CREATE TEMP VIEW amt AS
        SELECT a.label, a.raw, a.value_yuan, a.year, a.confidence, a.source,
               a.announcement_id,
               CAST(substr(p.publish_date, 1, 4) AS INTEGER) AS pub_year,
               CASE WHEN a.year IS NOT NULL
                         AND p.publish_date IS NOT NULL
                         AND a.year > CAST(substr(p.publish_date,1,4) AS INTEGER)
                    THEN 1 ELSE 0 END AS is_target
        FROM amounts a JOIN announcements p ON p.id = a.announcement_id
    """)

    total = con.execute("SELECT COUNT(*) FROM amt").fetchone()[0]
    targets = con.execute("SELECT COUNT(*) FROM amt WHERE is_target=1").fetchone()[0]
    print("=" * 74)
    print(f"金额时间轴  共 {total} 条   规划目标 {targets} 条 / 已发生 {total - targets} 条")
    print("说明：按数据点列出，不做跨文档求和（同标签下混着全国总量和单项预算，")
    print("      加起来不是任何真实数，同比也会是假的）。")
    print("=" * 74)

    labels = con.execute("""
        SELECT label, COUNT(*) n FROM amt GROUP BY label HAVING n >= 2
        ORDER BY n DESC
    """).fetchall()

    for row in labels:
        label = row["label"]
        years = con.execute(
            "SELECT COUNT(DISTINCT year) FROM amt WHERE label=? AND year IS NOT NULL",
            (label,)).fetchone()[0]
        print(f"\n■ {label}  {row['n']} 条 / {years} 个年份")
        rows = con.execute("""
            SELECT year, COUNT(*) n FROM amt
            WHERE label=? AND year IS NOT NULL GROUP BY year ORDER BY year
        """, (label,)).fetchall()
        for y in rows:
            year, cnt = y["year"], y["n"]
            vals = [r[0] for r in con.execute(
                "SELECT value_yuan FROM amt WHERE label=? AND year=?", (label, year))]
            med = statistics.median(vals)
            mx = max(vals)
            tgt = con.execute(
                "SELECT COUNT(*) FROM amt WHERE label=? AND year=? AND is_target=1",
                (label, year)).fetchone()[0]
            flag = f"  规划目标 {tgt} 条" if tgt else ""
            thin = "  ← 仅 1 条" if cnt == 1 and years > 1 else ""
            print(f"   {year}  {cnt:>3} 条  中位 {_yi(med):>16}  最大 {_yi(mx):>16}{flag}{thin}")
            for r in con.execute("""
                SELECT raw, value_yuan, confidence, announcement_id
                FROM amt WHERE label=? AND year=?
                ORDER BY value_yuan DESC LIMIT ?
            """, (label, year, SHOW)):
                print(f"        · {r['raw'][:26]:<28} {_yi(r['value_yuan']):>16}"
                      f"  置信{r['confidence']}  #{r['announcement_id']}")

    print("\n" + "=" * 74)
    print("提示：置信度 <= 62 的是无标签金额，只是没被误杀，不适合直接做趋势。")
    print("      python scripts/extract_amounts.py     # 预览，含置信度分布")
    print("      python scripts/overnight_health.py     # 通宵体检")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
