"""采集效率实测：不看"跑了几轮"，看"每小时产出多少条、哪些源在白跑"。

通宵任务每小时都跑，但如果 145 个源里大半产不出东西，那"跑了一夜"是假的。
要量的是：
  1. 源产出分布——多少源一条都没产出（纯浪费时间）
  2. 失败率——多少源一直在失败
  3. 命中率——抓到多少条里有多少是新的（其余是已入库的重复）
  4. 时间账单——从日志里算每个源花了多少秒，换算成"每小时产出"
"""
from __future__ import annotations

import re
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
con = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)

print("=" * 72)
print(f"采集效率实测  {datetime.now():%Y-%m-%d %H:%M}")
print("=" * 72)

total = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
print(f"\n库内总量 {total} 条")

# ---- 1. 源产出分布 -------------------------------------------------------
cfg = {}
try:
    sys.path.insert(0, str(ROOT))
    from main import load_config
    for s in load_config(str(ROOT / "config" / "sources.yaml"))["sources"]:
        cfg[str(s.get("id"))] = s
except Exception as exc:
    print(f"  （读配置失败 {type(exc).__name__}，只统计库内数据）")

enabled = [sid for sid, s in cfg.items() if str(s.get("status")) == "enabled"]
produced = Counter()
for sid, n in con.execute(
        "SELECT source_id, COUNT(*) FROM announcements GROUP BY source_id"):
    produced[sid] = n

zero = [s for s in enabled if produced.get(s, 0) == 0]
nonzero = [s for s in enabled if produced.get(s, 0) > 0]
counts = sorted((produced[s] for s in nonzero), reverse=True)

print(f"\n--- 1. 源产出分布（启用源 {len(enabled)} 个）---")
print(f"  产出一条以上的: {len(nonzero)} 个")
print(f"  一条都没产出:   {len(zero)} 个  ← 每次跑都在白跑")
if counts:
    print(f"  单源产出 中位 {statistics.median(counts):.0f} 条 / "
          f"最高 {counts[0]} / 最低 {counts[-1]}")
    top = Counter({s: produced[s] for s in nonzero}).most_common(10)
    print("  产出 top10:")
    for sid, n in top:
        print(f"    {sid:<22} {n:>4}")

# ---- 2. 失败率 -----------------------------------------------------------
print(f"\n--- 2. 源健康 ---")
rows = con.execute("""
    SELECT status, COUNT(*) FROM source_sync_state GROUP BY status
""").fetchall()
print(f"  状态分布: {dict(rows)}")
errs = con.execute("""
    SELECT substr(COALESCE(last_error,'(空)'),1,44) e, COUNT(*)
    FROM source_sync_state WHERE consecutive_failures>0
    GROUP BY e ORDER BY 2 DESC LIMIT 6
""").fetchall()
for text, n in errs:
    print(f"    {n:>3} x {text}")
top_fail = con.execute("""
    SELECT source_id, consecutive_failures FROM source_sync_state
    WHERE consecutive_failures>0 ORDER BY consecutive_failures DESC LIMIT 8
""").fetchall()
if top_fail:
    print("  连败最多的:", ", ".join(f"{s}(x{n})" for s, n in top_fail))

# ---- 3. 命中率 -----------------------------------------------------------
print(f"\n--- 3. 命中率（抓到 vs 新增）---")
for hours in (6, 14, 30):
    got = con.execute(
        "SELECT COUNT(*) FROM announcements WHERE fetched_at >= datetime('now', ?)",
        (f"-{hours} hours",)).fetchone()[0]
    new = con.execute(
        "SELECT COUNT(*) FROM announcements WHERE created_at >= datetime('now', ?)",
        (f"-{hours} hours",)).fetchone()[0]
    rate = f"{new/got:.0%}" if got else "—"
    print(f"  近 {hours:>2} 小时  抓到 {got:>4}  新增 {new:>4}  命中率 {rate}")

# ---- 4. 时间账单（从通宵日志算）-----------------------------------------
print(f"\n--- 4. 时间账单（从 overnight 日志算源耗时）---")
LOG_HEAD = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+)\s+\((.+?)\)\s+(OK|SKIP|FAIL)\b(.*)$")
# 三种尾巴格式不统一：OK 是「28条 (184s)」，SKIP/FAIL 是「(27s)」，
# FAIL 还可能再跟「: 400 Proxy connect error…」。分开抓，别指望一个正则全吃。
TAIL_N = re.compile(r"(\d+)\s*条")
TAIL_S = re.compile(r"\((\d+)\s*s\)")
logs = sorted((ROOT / "data").glob("overnight_2026*.log"))
spent: list[int] = []
gained: list[int] = []
per_source: dict[str, list[tuple[int, int]]] = {}
by_status: Counter = Counter()
for lg in logs[-3:]:
    try:
        text = lg.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for line in text.splitlines():
        m = LOG_HEAD.search(line)
        if not m:
            continue
        sid, status, tail = m.group(3), m.group(5), m.group(6)
        sm = TAIL_S.search(tail)
        if not sm:
            continue
        sec = int(sm.group(1))
        nm = TAIL_N.search(tail)
        cnt = int(nm.group(1)) if nm else 0
        by_status[status] += 1
        spent.append(sec)
        gained.append(cnt)
        per_source.setdefault(sid, []).append((sec, cnt))
if spent:
    total_min = sum(spent) / 60
    total_new = sum(gained)
    print(f"  近 3 个日志文件共 {len(spent):>4} 次源请求  {dict(by_status)}")
    print(f"  源耗时合计 {total_min:,.0f} 分钟（并发 6 -> 墙钟约 {total_min/6:,.0f} 分钟）")
    print(f"  这些请求抓到 {total_new:,} 条新记录")
    if total_min:
        print(f"  每小时产出 {total_new / (total_min / 60):,.1f} 条（按源耗时折算）")
    # 白跑榜：花时间多、产出 0
    waste = sorted(((sum(s for s, _ in v), sum(c for _, c in v), sid)
                    for sid, v in per_source.items() if sum(c for _, c in v) == 0),
                   reverse=True)
    if waste:
        print(f"\n  白跑榜（花时间却 0 产出，前 10）:")
        for sec, _c, sid in waste[:10]:
            print(f"    {sid:<22} {sec:>5}s")
        waste_sec = sum(w[0] for w in waste)
        share = waste_sec / sum(spent) if spent else 0
        print(f"  白跑合计 {waste_sec:,}s，占全部源耗时 {share:.0%}，"
              f"涉及 {len(waste)} 个源")
    # 单源效率：秒/条，找性价比最差的
    eff = []
    for sid, v in per_source.items():
        c = sum(c for _, c in v)
        if c:
            eff.append((sum(s for s, _ in v) / c, sid, c))
    if eff:
        eff.sort(reverse=True)
        print(f"\n  秒/条 最差的 8 个源（每条花的秒数）:")
        for ratio, sid, c in eff[:8]:
            print(f"    {sid:<22} {ratio:>6.0f}s/条  共 {c} 条")
        print(f"  最好的 5 个:")
        for ratio, sid, c in eff[-5:]:
            print(f"    {sid:<22} {ratio:>6.1f}s/条  共 {c} 条")

    # ---- 真实吞吐：只能用"每轮库总量变化 ÷ 轮耗时" -------------------------
    # 不能用日志里的「N条」求和。那个数是**抓取数**，排空模式会把同一批条目在
    # 40 轮里反复计入——我先按它算出"每小时 794 条"，而库里总共才 2438 条，
    # 一眼就知道不对。抓取数除以耗时会系统性地虚高。
    print(f"\n--- 5. 真实吞吐（每轮库总量变化 ÷ 轮耗时）---")
    # 日志行是 "=== overnight start 2026/09/28 星期二  3:30:01.64 ==="，
    # 日期和星期之间还有一段（中文星期，控制台常显示成乱码），所以时间部分
    # 用宽松匹配抓行尾那组 时:分:秒，不去对齐字段个数。
    START = re.compile(r"overnight start\b.*?(\d+):(\d+):(\d+)")
    END = re.compile(r"overnight done\b.*?(\d+):(\d+):(\d+)")
    TOT = re.compile(r"announcements\s*=\s*(\d+)")
    runs: list[tuple[str, int, int]] = []
    for lg in logs:
        try:
            text = lg.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        t0 = t1 = None
        base = None
        for line in text.splitlines():
            if t0 is None and (m := START.search(line)):
                t0 = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if t0 is not None and (m := TOT.search(line)):
                base = int(m.group(1))
            if (m := END.search(line)) and t0 is not None:
                t1 = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                if base is not None:
                    runs.append((lg.name, t1 - t0, base))
                t0 = t1 = base = None
    if runs:
        print(f"  找到 {len(runs)} 轮（按 overnight 日志的起止与流水线结束时的库总量）")
        for name, dur, base in runs[-6:]:
            print(f"    {name}  耗时 {dur//60:>3} 分 {dur%60:>2} 秒  轮末库内 {base}")
        deltas = []
        prev = None
        for name, dur, base in runs:
            if prev is not None and base >= prev[1]:
                deltas.append((name, dur, base - prev[1]))
            prev = (name, base)
        if deltas:
            d_min = sum(d[1] for d in deltas) / 60
            d_new = sum(d[2] for d in deltas)
            print(f"\n  可比轮次 {len(deltas)} 个：")
            for name, dur, new in deltas:
                rate = new / (dur / 3600) if dur else 0
                print(f"    {name}  {dur//60:>3}分  新增 {new:>4} 条  -> {rate:>6.1f} 条/小时")
            if d_min:
                print(f"\n  合计：{d_min:,.0f} 分钟新增 {d_new:,} 条 "
                      f"= {d_new/(d_min/60):,.1f} 条/小时")
    else:
        print("  （没解析到完整的轮次边界）")

    # 集中度：产出与耗时的分布是否匹配。这是决定"该不该砍源"的唯一依据。
    by_sid_time: dict[str, int] = {}
    by_sid_gain: dict[str, int] = {}
    for sid, v in per_source.items():
        by_sid_time[sid] = sum(s for s, _ in v)
        by_sid_gain[sid] = sum(c for _, c in v)
    ranked = sorted(by_sid_gain, key=lambda s: -by_sid_gain[s])
    n = len(ranked)
    top20 = ranked[:20]
    t20 = sum(by_sid_time[s] for s in top20)
    g20 = sum(by_sid_gain[s] for s in top20)
    print(f"\n  集中度：产出前 20 的源（占 {20/n:.0%} 的源）")
    print(f"    贡献了 {g20:,} 条 = 总产出的 {g20/max(total_new,1):.0%}")
    print(f"    只花了 {t20:,}s = 总耗时的 {t20/max(sum(spent),1):.0%}")
    # 反过来：耗时的后 20 名
    costly = sorted(by_sid_time, key=lambda s: -by_sid_time[s])[:20]
    tc = sum(by_sid_time[s] for s in costly)
    gc = sum(by_sid_gain[s] for s in costly)
    print(f"  耗时后 20 的源：")
    print(f"    花了 {tc:,}s = 总耗时的 {tc/max(sum(spent),1):.0%}")
    print(f"    只产出 {gc:,} 条 = 总产出的 {gc/max(total_new,1):.0%}")
    if tc:
        print(f"    把它们砍掉，剩余源的时间产出比可提升约 "
              f"{(sum(spent)/max(sum(spent)-tc,1)) / (total_new/max(total_new-gc,1)):.1f}x")
con.close()
