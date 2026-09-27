"""通宵采集的健康体检：早上看这一份就知道夜里干得怎么样。

为什么单独写，而不是直接看 pipeline 的日志
  流水线日志是过程流水（每个源抓了几条、哪步失败），要看懂它得读完几十屏。
  这里只回答四个问题，并且把"该报警的"排在最前面：
    1. 夜里到底新增了多少（没新增 = 白跑一宿）
    2. 哪一步失败了（失败就意味着数据停在半路，字段/摘要/金额都是旧的）
    3. 数据质量有没有掉（摘要超长、空摘要、金额覆盖率）
    4. 锁和僵尸状态（残留锁会让第二天所有手动运行直接失败）

退出码刻意恒为 0：这是体检报告，不是门禁。健康检查失败不该让计划任务显示
"采集失败"，那是误导——真正的失败在 pipeline 那一步的退出码里。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "beacon.db"
LOCK = ROOT / "data" / "runtime" / "collect.lock"
WINDOW = timedelta(hours=14)


def main() -> int:
    if not DB.exists():
        print(f"[!] 数据库不存在：{DB}")
        return 0
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    total = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    since = (datetime.now() - WINDOW).isoformat(sep=" ", timespec="seconds")
    fresh = con.execute(
        "SELECT COUNT(*) FROM announcements WHERE created_at >= ?", (since,)).fetchone()[0]
    fetched = con.execute(
        "SELECT COUNT(*) FROM announcements WHERE fetched_at >= ?", (since,)).fetchone()[0]

    problems: list[str] = []

    print("=" * 62)
    print(f"通宵采集体检  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 62)
    print(f"公告总量        {total}")
    print(f"近 {int(WINDOW.total_seconds() // 3600)} 小时新增   {fresh}")
    print(f"近 {int(WINDOW.total_seconds() // 3600)} 小时抓到   {fetched}")

    if fresh == 0:
        problems.append("夜里一条没新增：源可能全被限流/改版，或 next-due 时间没到")

    # 金额覆盖：时间轴对比的数据基础
    try:
        amt = con.execute("SELECT COUNT(*) FROM amounts").fetchone()[0]
        amt_docs = con.execute("SELECT COUNT(DISTINCT announcement_id) FROM amounts").fetchone()[0]
        years = con.execute(
            "SELECT COUNT(DISTINCT year) FROM amounts WHERE year IS NOT NULL").fetchone()[0]
        print(f"金额            {amt} 条 / {amt_docs} 篇，覆盖 {amt_docs / max(total,1):.0%}")
        print(f"金额年份跨度    {years} 个年份")
        if amt_docs / max(total, 1) < 0.08:
            problems.append(f"金额覆盖率 {amt_docs/max(total,1):.0%} 偏低，抽取规则可能需要放宽")
        if years <= 2:
            problems.append(f"金额只有 {years} 个年份，跨年对比还做不出来——需要补往年数据")
    except sqlite3.OperationalError:
        problems.append("amounts 表不存在：金额抽取没跑成功")

    # 摘要质量。announcements 没有 summary 列——摘要由 key_points（JSON 数组）
    # 拼成，与 scripts/audit_summary_quality.py 同一套算法。口径必须一致，
    # 否则两处报的数字对不上，比没有更麻烦。
    import json
    empty = over = with_points = 0
    for (kp,) in con.execute("SELECT key_points FROM announcements"):
        points = json.loads(kp) if kp else []
        if not points:
            empty += 1
            continue
        with_points += 1
        if len("".join(str(p) for p in points)) > 600:
            over += 1
    print(f"空摘要          {empty} ({empty/max(total,1):.1%})")
    print(f"超 600 字摘要   {over} ({over/max(total,1):.1%})")
    if over / max(total, 1) > 0.02:
        problems.append(f"超长摘要 {over} 条（>2%），硬上限可能被绕过")

    # 未分类比例：分类步骤失败最容易留下的痕迹
    other = con.execute(
        "SELECT COUNT(*) FROM announcements WHERE category_l1 IS NULL OR category_l1=''"
    ).fetchone()[0]
    print(f"未分类          {other} ({other/max(total,1):.1%})")
    if other / max(total, 1) > 0.1:
        problems.append(f"未分类 {other/max(total,1):.0%}，classify 步骤可能没跑成功")

    # 锁状态
    if LOCK.exists():
        age_h = (datetime.now() - datetime.fromtimestamp(LOCK.stat().st_mtime)).total_seconds() / 3600
        print(f"[!] 残留采集锁    {LOCK}（{age_h:.1f} 小时前）")
        if age_h > 3:
            problems.append(f"残留锁已 {age_h:.1f} 小时，下次运行会直接被挡住")
    else:
        print("采集锁          无残留")

    print("-" * 62)
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  [!] {p}")
    else:
        print("无异常。")
    print(f"\n明细见 data/overnight_report.md 与 data/collect4.log")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
