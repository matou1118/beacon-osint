"""采集链路的真实构成：到底谁在干活，时间花在哪。

用户质疑"效率这么低是不是没用 crawl4ai + cloakbrowser"。先查清楚实际走的是
哪条路，再回答——不能靠印象。
"""
from __future__ import annotations

import collections
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import load_config  # noqa: E402

cfg = load_config(str(ROOT / "config" / "sources.yaml"))
srcs = cfg["sources"]
enabled = [s for s in srcs if str(s.get("status")) == "enabled"]

print("=" * 70)
print("1. 101 个启用源实际走哪条取数路径")
print("=" * 70)
print("  access_method:", dict(collections.Counter(
    str(s.get("access_method")) for s in enabled)))
print("  type         :", dict(collections.Counter(
    str(s.get("type")) for s in enabled)))
print()
for key in ("playwright_fallback", "render_mode", "crawl4ai"):
    vals = collections.Counter(str(s.get(key)) for s in enabled)
    if len(vals) > 1 or list(vals)[0] not in ("None",):
        print(f"  {key:<20}", dict(vals))

print()
print("=" * 70)
print("2. crawl4ai / cloakbrowser 的代码位置与触发条件")
print("=" * 70)
hl = (ROOT / "collectors" / "html_list.py").read_text(encoding="utf-8")
import re
for pat, label in ((r"crawl4ai_enabled", "crawl4ai 启用判据"),
                   (r"playwright_fallback", "渲染兜底开关"),
                   (r"static_then_crawl4ai", "crawl4ai 渲染模式")):
    hits = [i + 1 for i, l in enumerate(hl.splitlines()) if re.search(pat, l)]
    print(f"  {label:<22} html_list.py 第 {hits} 行")

pw = (ROOT / "collectors" / "playwright_worker.py").read_text(encoding="utf-8")
print(f"  cloakbrowser 引用       playwright_worker.py 共 "
      f"{len(re.findall('cloakbrowser', pw))} 处")

print()
print("=" * 70)
print("3. 真正的时间账单：抓取 / 抽取 / 入库 / 摘要 各占多少")
print("=" * 70)
import json
rounds = []
p = ROOT / "data" / "rounds.jsonl"
if p.exists():
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rounds.append(json.loads(line))
        except ValueError:
            pass
if rounds:
    print(f"  rounds.jsonl 共 {len(rounds)} 轮")
    tot_wall = sum(r.get("wall_seconds") or 0 for r in rounds)
    tot_ins = sum(r.get("inserted") or 0 for r in rounds)
    tot_unch = sum(r.get("unchanged") or 0 for r in rounds)
    tot_upd = sum(r.get("updated") or 0 for r in rounds)
    tot_rec = sum(r.get("records") or 0 for r in rounds)
    print(f"  抓取记录总数 {tot_rec:,}   新增 {tot_ins:,}   更新 {tot_upd:,}   "
          f"未变 {tot_unch:,}")
    if tot_rec:
        print(f"  ** 重复处理率 {(tot_unch + tot_upd)/tot_rec:.0%} "
              f"—— 抓回来的 {tot_rec:,} 条里只有 {tot_ins:,} 条是新的 **")
    if tot_wall:
        print(f"  总墙钟 {tot_wall/3600:,.1f} 小时")
        if tot_ins:
            print(f"  每条新增的摊到时间: {tot_wall/tot_ins:,.1f} 秒/条（串行折算）")
else:
    print("  （rounds.jsonl 为空）")

print()
print("=" * 70)
print("4. 库里 content_source 分布：正文到底是谁抽的")
print("=" * 70)
con = sqlite3.connect(f"file:{ROOT/'data'/'beacon.db'}?mode=ro", uri=True)
for cs, n in con.execute(
        "SELECT COALESCE(content_source,'(空)'), COUNT(*) FROM announcements "
        "GROUP BY content_source ORDER BY 2 DESC LIMIT 8"):
    print(f"  {str(cs):<34} {n:>5}")
print()
print("  needs_review 标记:", con.execute(
    "SELECT COUNT(*) FROM announcements WHERE needs_review=1").fetchone()[0], "条")
print("  有 key_points    :", con.execute(
    "SELECT COUNT(*) FROM announcements WHERE key_points IS NOT NULL "
    "AND key_points != '[]'").fetchone()[0], "条")
