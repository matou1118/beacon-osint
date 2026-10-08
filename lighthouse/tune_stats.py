"""自迭代的使用统计：跨轮汇总它查了什么、动了什么、值不值。

单轮报告（``data/runtime/lighthouse/tuning/tune-NNN.json``）只说"这一轮怎么样"，
答不了"这功能到底有没有用"。本模块把所有轮次聚成一张台账：

  * 每类 finding 出现过多少轮、涉及多少个源
  * 哪些是自动动手的、哪些只报不改
  * **实测效果**：发现报过的问题，后来是否消失
    （no_links 修好后还在不在、时间预算浪费减少了没有、摘要空缺率降了没有）

效果统计是重点。一堆 finding 不等于有用 —— 得看它指出的问题有没有被消掉。
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 每类 finding 对应的"消失"判据。finding 报了就说明有问题，
# 后面同类问题的规模下降到阈值以下就算被消掉。
RESOLVED_RULES = {
    "no_links":            ("extract", 0),      # 一条都不许有
    "all_dropped":         ("extract", 3),      # 降到 3 个源以内
    "time_budget_wasted":  ("collect", 0),      # 不该再出现
    "summary_gap_high":    ("summary", None),   # 比率型，另算
    "gate_drop_concentrated": ("analyze", None),  # 提示型，不判消解
    "drained_sources":     ("collect", None),   # 正常状态，不判消解
    "coverage":            ("collect", None),   # 汇总行，不判消解
}

SUMMARY = "汇总行，不参与效果判定"
STAGE_CN = {"collect": "采集", "extract": "抽取",
            "summary": "摘要", "analyze": "分析"}


@dataclass
class UsageReport:
    rounds: int = 0
    files_read: int = 0
    stage_rounds: dict[str, int] = field(default_factory=dict)      # 每段出现过多少轮
    kind_rounds: dict[str, int] = field(default_factory=dict)       # 每类出现过多少轮
    kind_sources: dict[str, set] = field(default_factory=dict)     # 每类涉及多少源
    auto_kinds: collections.Counter = field(default_factory=collections.Counter)
    bucket_totals: collections.Counter = field(default_factory=collections.Counter)
    records_collected: int = 0
    records_dropped: int = 0
    findings_total: int = 0
    resolved: dict[str, str] = field(default_factory=dict)
    still_open: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "files_read": self.files_read,
            "stage_rounds": self.stage_rounds,
            "kind_rounds": self.kind_rounds,
            "kind_source_counts": {k: len(v) for k, v in self.kind_sources.items()},
            "auto_applied": dict(self.auto_kinds),
            "buckets": dict(self.bucket_totals),
            "records_collected": self.records_collected,
            "records_dropped": self.records_dropped,
            "findings_total": self.findings_total,
            "resolved": self.resolved,
            "still_open": self.still_open,
            "notes": self.notes,
        }


def collect(tuning_dir: Path) -> UsageReport:
    """扫 tuning/ 下所有 tune-*.json，聚合使用情况。"""
    report = UsageReport()
    if not tuning_dir.exists():
        report.notes.append("还没有 tune-*.json —— 自迭代还没跑过任何一轮")
        return report

    per_round: list[dict[str, Any]] = []
    for path in sorted(tuning_dir.glob("tune-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report.notes.append(f"{path.name} 读不出来，跳过")
            continue
        per_round.append(data)
        report.files_read += 1
        report.rounds = max(report.rounds, int(data.get("round") or 0))
        for f in data.get("findings") or []:
            kind = f.get("kind") or "?"
            stage = f.get("stage") or "?"
            report.findings_total += 1
            report.stage_rounds[stage] = report.stage_rounds.get(stage, 0) + 1
            report.kind_rounds[kind] = report.kind_rounds.get(kind, 0) + 1
            report.kind_sources.setdefault(kind, set()).update(f.get("sources") or [])
            if f.get("auto"):
                report.auto_kinds[kind] += 1
        for k, v in (data.get("buckets") or {}).items():
            report.bucket_totals[k] += int(v or 0)

    if not per_round:
        report.notes.append("tuning 目录里没有可解析的报告")
        return report

    first, last = per_round[0], per_round[-1]

    # 采集/入库量直接读 tune 报告自带的字段（新版本才有；旧报告回落到 shadow）
    shadow_dir = tuning_dir.parent
    shadows = sorted(shadow_dir.glob("shadow-*.json"), key=lambda p: p.stat().st_mtime)
    shadow_tail = []
    for path in shadows[-len(per_round):]:
        try:
            shadow_tail.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    for i, d in enumerate(per_round):
        if "records" in d:
            report.records_collected += int(d.get("records") or 0)
            report.records_dropped += int(d.get("dropped") or 0)
        elif i < len(shadow_tail):
            report.records_collected += int(shadow_tail[i].get("records") or 0)
            report.records_dropped += int(shadow_tail[i].get("dropped") or 0)

    # 效果判定：首轮报过、末轮规模掉下来的算消解
    for kind, (stage, threshold) in RESOLVED_RULES.items():
        if kind not in report.kind_rounds:
            continue
        if threshold is None:
            continue
        series = [_round_scale(d, kind) for d in per_round]
        if series[0] > threshold and series[-1] <= threshold:
            report.resolved[kind] = (f"{series[0]} -> {series[-1]}"
                                     f"（阈值 {threshold}）")
        elif series[-1] > threshold:
            report.still_open.append(
                f"{STAGE_CN.get(stage, stage)}/{kind}: 仍 {series[-1]}（阈值 {threshold}）")

    report.notes.append("首轮 buckets=%s" % (first.get("buckets"),))
    report.notes.append("末轮 buckets=%s" % (last.get("buckets"),))
    return report


def _round_scale(data: dict[str, Any], kind: str) -> int:
    """某一轮里，这类问题涉及多少个源。"""
    for f in data.get("findings") or []:
        if f.get("kind") == kind:
            return len(f.get("sources") or [])
    return 0


def render(report: UsageReport) -> str:
    """人看的一行行结论。"""
    out: list[str] = []
    out.append("=" * 68)
    out.append("自迭代使用统计（覆盖 %d 轮，扫了 %d 份报告）"
               % (report.rounds, report.files_read))
    out.append("=" * 68)

    if not report.rounds:
        for n in report.notes:
            out.append("  " + n)
        return "\n".join(out)

    out.append("")
    out.append("各段参与情况（出现过多少轮）:")
    for stage in ("collect", "extract", "summary", "analyze"):
        n = report.stage_rounds.get(stage, 0)
        bar = "#" * min(20, n)
        out.append("  %-4s %-3d 轮 %s" % (STAGE_CN[stage], n, bar))

    out.append("")
    out.append("各类 finding:")
    for kind, n in sorted(report.kind_rounds.items(), key=lambda kv: -kv[1]):
        srcs = len(report.kind_sources.get(kind, ()))
        auto = report.auto_kinds.get(kind, 0)
        tag = "自动动手 %d 次" % auto if auto else "只报不改"
        out.append("  %-24s %3d 轮  涉及 %3d 个源  %s" % (kind, n, srcs, tag))

    out.append("")
    out.append("源处理累计（buckets 逐轮相加）:")
    for k, v in sorted(report.bucket_totals.items(), key=lambda kv: -kv[1]):
        out.append("  %-12s %5d" % (k, v))
    out.append("")
    out.append("采集量: 采到 %d 条，被门禁丢弃 %d 条" %
               (report.records_collected, report.records_dropped))

    if report.resolved:
        out.append("")
        out.append("已消解的问题:")
        for kind, change in report.resolved.items():
            out.append("  ✓ %-24s %s" % (kind, change))
    if report.still_open:
        out.append("")
        out.append("仍未解决:")
        for line in report.still_open:
            out.append("  ✗ %s" % line)

    out.append("")
    out.append("说明:")
    for n in report.notes:
        out.append("  " + n)
    out.append("  %s" % SUMMARY)
    return "\n".join(out)


def main() -> int:
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "data/runtime/lighthouse/tuning")
    print(render(collect(root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
