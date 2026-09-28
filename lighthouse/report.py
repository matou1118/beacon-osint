"""多轮采集统计总结。通宵跑完之后一条命令出结论。

    python lighthouse/report.py                 # 汇总所有影子报告
    python lighthouse/report.py --since 20:00   # 只看某个时间点之后
    python lighthouse/report.py --top 15        # 问题源排前 15

读三处，都只读：
    data/runtime/lighthouse/shadow-*.json   每轮的逐源结果
    data/runtime/lighthouse/overnight.log    轮次边界、退出码
    data/beacon.db                          最终入库数

回答四个问题：
    1. 一共采了多少、每轮多少、是不是越跑越少（源采空了）
    2. 哪些源在反复产数据，哪些一直没产出
    3. 哪些源反复失败，为什么（下一轮优化的输入）
    4. 钱花在哪了——路由分布决定渲染开销
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SHADOW_GLOB = str(_ROOT / "data" / "runtime" / "lighthouse" / "shadow-*.json")
# 自迭代的复采探针只覆盖几个源，通宵循环里的「最新报告」经常是它。
# 混进来会让「逐轮」表格里出现一堆只有 3~6 个源的假轮次。
PROBE_PREFIX = "shadow-probe-"
LOG = _ROOT / "data" / "runtime" / "lighthouse" / "overnight.log"
DB = _ROOT / "data" / "beacon.db"

# 源名的归类：判断「这个源值不值得继续投时间」
_GOVY = ("gov", "fgw", "kjt", "gxt", "swt", "rst", "ny", "kjt", "samt", "gat")


def out(text: str = "") -> None:
    print(text, flush=True)


def load_reports(since: str | None, include_probes: bool = False) -> list[dict]:
    files = sorted(glob.glob(SHADOW_GLOB), key=os.path.getmtime)
    if not include_probes:
        files = [f for f in files
                 if not os.path.basename(f).startswith(PROBE_PREFIX)]
    if since:
        cut = datetime.strptime(since, "%H:%M").time()
        picked = []
        for path in files:
            stamp = datetime.strptime(
                os.path.basename(path)[len("shadow-"):-len(".json")], "%Y%m%dT%H%M%S")
            if stamp.time() >= cut:
                picked.append(path)
        files = picked
    reports = []
    for path in files:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["_file"] = os.path.basename(path)
        data["_at"] = os.path.getmtime(path)
        reports.append(data)
    return reports


def parse_log() -> list[dict]:
    """从 overnight.log 里切出每一轮：起止时间、退出码。"""
    if not LOG.exists():
        return []
    rounds: list[dict] = []
    current: dict | None = None
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        stamp = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if not stamp:
            continue
        at = stamp.group(1)
        if "round" in line and "start" in line:
            current = {"start": at, "end": "", "exit": ""}
            rounds.append(current)
        elif "round" in line and "done" in line and current is not None:
            current["end"] = at
            found = re.search(r"exit=(-?\d+)", line)
            if found:
                current["exit"] = found.group(1)
    return rounds


def classify(source: dict) -> str:
    notes = " | ".join(source.get("notes") or [])
    if source.get("records"):
        return "ok"
    if "run time budget" in notes:
        return "skipped"
    if "early stop" in notes:
        return "no_new"
    if ("no links" in notes or "list_selector" in notes
            or "fetch_list failed" in notes):
        return "no_links"
    if source.get("dropped"):
        return "all_dropped"
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="多轮采集统计总结")
    parser.add_argument("--since", default=None, help="只看 HH:MM 之后的报告")
    parser.add_argument("--top", type=int, default=15, help="问题源排前 N")
    parser.add_argument("--include-probes", action="store_true",
                        help="连自迭代的复采探针一起算（默认排除）")
    args = parser.parse_args(argv)

    reports = load_reports(args.since, args.include_probes)
    if not reports:
        out("没有影子报告。先跑一次采集。")
        return 1

    rounds = parse_log()
    out("=" * 78)
    out(f"报告 {len(reports)} 份，覆盖 {len(rounds)} 轮"
        f"{'（overnight.log）' if rounds else ''}")

    # ---------------------------------------------------------------- 1. 总量
    total_rec = sum(r.get("records", 0) for r in reports)
    total_drop = sum(r.get("dropped", 0) for r in reports)
    out("-" * 78)
    out(f"累计采到 {total_rec} 条 | 门禁丢弃 {total_drop} 条 "
        f"| 通过率 {100*total_rec/max(1,total_rec+total_drop):.1f}%")

    routes: Counter = Counter()
    reasons: Counter = Counter()
    for report in reports:
        routes.update(report.get("by_route") or {})
        for key, value in (report.get("by_reason") or {}).items():
            reasons[key.split(":")[0] + ":" + key.split(":")[-1][:24]
                    if key.count(":") > 1 else key] += value
    if routes:
        spent = sum(routes.values())
        out("路由分布（决定渲染开销）:")
        for name, count in routes.most_common():
            cost = {"static": 3, "render": 30, "markdown": 50, "pdf": 10}.get(name, 1)
            out(f"  {name:<10} {count:>6}  {100*count/spent:>5.1f}%   相对成本 {cost:>3}x")
        weighted = sum(c * {"static": 3, "render": 30, "markdown": 50,
                            "pdf": 10}.get(k, 1) for k, c in routes.items())
        out(f"  加权开销 {weighted}（全走 static 是 {spent*3}，"
            f"所以渲染只多花了 {weighted/(spent*3)-1:+.0%}）")

    # ---------------------------------------------------------------- 2. 逐轮
    # 按 overnight.log 里的轮次时间窗归并。早先按分钟当 key 会把一轮切成十几行
    # （一轮 45 分钟里陆续写出 100 多份 shadow 报告），趋势就没法看了。
    out("-" * 78)
    out("逐轮:")
    boundaries = [(datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S"),
                   datetime.strptime(r["end"], "%Y-%m-%d %H:%M:%S")
                   if r.get("end") else None)
                  for r in rounds]

    def which_round(stamp: float) -> str:
        when = datetime.fromtimestamp(stamp)
        for index, (start, end) in enumerate(boundaries, 1):
            if when >= start and (end is None or when <= end):
                return f"round{index}"
        return "ad-hoc"

    by_round: dict[str, dict] = {}
    for report in reports:
        key = which_round(report["_at"])
        bucket = by_round.setdefault(key, {"rec": 0, "drop": 0, "src": set(),
                                          "at": datetime.fromtimestamp(report["_at"]),
                                          "reports": 0, "exit": ""})
        bucket["rec"] += report.get("records", 0)
        bucket["drop"] += report.get("dropped", 0)
        bucket["src"] |= {s["source_id"] for s in report.get("sources", [])}
        bucket["reports"] += 1
    for index, entry in enumerate(rounds, 1):
        by_round.get(f"round{index}", {})["exit"] = entry.get("exit", "")

    out(f"  {'轮次':<8}{'开始':<20}{'报告':>5}{'源':>5}{'采到':>7}{'丢弃':>7}{'通过率':>8}{'退出码':>7}")
    ordered = sorted(by_round.items(),
                     key=lambda kv: (kv[1]["at"] if isinstance(kv[1]["at"], datetime)
                                     else datetime.max))
    for key, b in ordered:
        if not isinstance(b.get("at"), datetime):
            continue
        rate = 100 * b["rec"] / max(1, b["rec"] + b["drop"])
        out(f"  {key:<8}{b['at'].strftime('%m-%d %H:%M:%S'):<20}{b['reports']:>5}"
            f"{len(b['src']):>5}{b['rec']:>7}{b['drop']:>7}{rate:>7.1f}%"
            f"{str(b.get('exit') or '-'):>7}")

    seq = [b["rec"] for _, b in ordered if isinstance(b.get("at"), datetime)]
    if len(seq) >= 3:
        trend = ("上升" if seq[-1] > seq[0]
                 else "持平" if seq[-1] == seq[0] else "衰减")
        out(f"  趋势：{seq[0]} -> {seq[-1]}（{trend}）")
        out("  注：增量轮次采到量逐轮下降是正常的——源被采空了。"
            "连续 3 轮都是 0 说明该换源或加 archive_pages。")

    # ---------------------------------------------------------------- 3. 逐源
    out("-" * 78)
    per_source: dict[str, dict] = defaultdict(
        lambda: {"ok": 0, "no_new": 0, "no_links": 0, "all_dropped": 0,
                 "skipped": 0, "unknown": 0, "rec": 0})
    for report in reports:
        for source in report.get("sources", []):
            bucket = per_source[source["source_id"]]
            bucket[classify(source)] += 1
            bucket["rec"] += source.get("records", 0)

    producing = sorted(((v["rec"], k) for k, v in per_source.items() if v["rec"] > 0),
                       reverse=True)
    out(f"有产出的源 {len(producing)}/{len(per_source)}：")
    for count, sid in producing[:args.top]:
        v = per_source[sid]
        out(f"  {sid:<18} {count:>5} 条   轮次 {v['ok']}成功/{v['no_new']}无新/"
            f"{v['skipped']}超时")

    barren = sorted((k for k, v in per_source.items() if v["rec"] == 0),
                    key=lambda s: -(per_source[s]["no_links"] * 10
                                    + per_source[s]["all_dropped"] * 5
                                    + per_source[s]["unknown"]))
    out(f"\n零产出源 {len(barren)} 个（按失败次数排序，前 {args.top}）:")
    for sid in barren[:args.top]:
        v = per_source[sid]
        out(f"  {sid:<18} no_links={v['no_links']} all_dropped={v['all_dropped']} "
            f"no_new={v['no_new']} skipped={v['skipped']} unknown={v['unknown']}")

    # ---------------------------------------------------------------- 4. 失败原因
    if reasons:
        out("-" * 78)
        out("失败原因 top 10:")
        for reason, count in reasons.most_common(10):
            out(f"  {count:>5}  {reason[:70]}")

    # ---------------------------------------------------------------- 5. 库现状
    if DB.exists():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from announcements").fetchone()[0]
        src = con.execute("select count(distinct source_id) from announcements").fetchone()[0]
        eng = Counter()
        for (extra,) in con.execute("select extra from announcements"):
            engine = (json.loads(extra or "{}").get("summary_engine") or "old")
            eng[engine] += 1
        con.close()
        out("-" * 78)
        out(f"announcements {n} 条 / {src} 个源")
        out(f"摘要引擎 {dict(eng)}")

    md = len(glob.glob(str(_ROOT / "data" / "markdown" / "*" / "*.md")))
    out(f"markdown 文件 {md} 个")
    out("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
