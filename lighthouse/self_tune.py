"""采集→摘要全链路的自我迭代：按实测证据决定下一轮怎么调。

为什么不用 scripts/self_improve.py
----------------------------------
它只处理一种故障：源报 ``no links`` / ``list_selector`` 时调 repair.py 修选择器。
实测 2026-09-30 的真实故障分布是

    no_new 12 / all_dropped 5 / unknown 9 / no_links 0 / ok 1

也就是说它经常一整轮什么都不做，看起来"跑了但没用"。本模块覆盖四段：

  采集   源耗尽、时间预算打满仍 0 产出、耗时高但零产出
  抽取   列表页取不到链接（沿用 repair.py）、取到但全被门禁拦下
  摘要   no_usable_sentence 比例过高（图文解读策，摘不出来）
  分析   门禁丢弃原因分布异常集中（阈值可能过严或选择器取错栏目）

设计约束
--------
1. **证据不足就不动**。每个动作都要求有可复算的判定条件（见各处注释），
   不做「看着不对就调」。乱调门禁阈值比不调更糟 —— 那会污染整库。
2. **只自动执行高置信动作**，且全部落在 ``data/runtime/lighthouse/tuning/``
   下可回看；真正写配置的动作要 ``--apply`` 才生效。
3. **不动全局质量阈值**。实测 link_flood 阈值 25% 是对的：全库 6555 条里
   链接占比 P50~P99 全是 0.0%，超过 25% 的只有 7 条且全是索引页/404。
   放宽它等于绕过反爬。只在**单个源**持续全被拦时才提该源的证据。
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

# 判定阈值。都写在代码里而不是配置，因为这些是「观察者的耐心」而不是
# 「被观察者的参数」——调它们不该影响采集行为。
DRAINED_STREAK = 3          # 连续几轮零产出算采干
SLOW_ZERO_SECONDS = 420     # 单源耗时超过这个值还零产出 = 预算打满
SLOW_ZERO_MIN_SOURCES = 3   # 至少这么多源如此才值得调预算
DROPPED_ONLY_MIN = 8        # 单源连续被全拦多少条才提「选择器可能取错栏目」
SUMMARY_GAP_RATIO = 0.25    # 摘要空缺率超过这个比例才提摘要策略
ALL_STAGES = ("collect", "extract", "summary", "analyze")


@dataclass
class Finding:
    stage: str                 # collect / extract / summary / analyze
    kind: str                  # 机器可读的问题类型
    sources: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    action: str = ""           # 建议动作；空 = 只报告不动手
    auto: bool = False         # True = 高置信，可自动执行
    detail: str = ""           # 人看的说明


@dataclass
class TuneReport:
    round: int
    findings: list[Finding] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    # 原始数字。带上它们，tune_stats.py 才能跨轮聚合 —— 只存 findings 的话
    # 「本轮源覆盖 / 采到几条 / 丢弃几条」这些最该留档的量会丢。
    buckets: dict[str, int] = field(default_factory=dict)
    records: int = 0
    dropped: int = 0
    ingested: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {"round": self.round,
             "findings": [asdict(f) for f in self.findings],
             "actions_taken": self.actions_taken,
             "buckets": self.buckets,
             "records": self.records,
             "dropped": self.dropped,
             "ingested": self.ingested},
            ensure_ascii=False, indent=2)


def _notes_of(source: Mapping[str, Any]) -> str:
    return " | ".join(str(n) for n in (source.get("notes") or []))


def _is_slow_zero(source: Mapping[str, Any]) -> bool:
    return (not source.get("records")
            and float(source.get("seconds") or 0) >= SLOW_ZERO_SECONDS
            and "budget" in _notes_of(source))


def _recent_broken_events(limit: int = 20) -> list[dict[str, Any]]:
    """读采集期记下的入口失效事件（event_bus 最近若干条 collector 事件）。

    读不到就返回空列表 —— 事件流是可观测性设施，缺了不该让整份自迭代报告
    失败。这里吞异常，和 run.py 里采集失败不拖垮整轮是同一个取舍。
    """
    try:
        from lighthouse import event_bus

        events = event_bus.read_events()
    except Exception:                                   # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for e in reversed(events):
        if e.get("layer") != "collector":
            continue
        ex = e.get("extra") or {}
        out.append({
            "ts": e.get("ts", ""), "source_id": str(e.get("scope", "")).split(":")[-1],
            "failure_type": e.get("failure_type", ""),
            "old": ex.get("old_selector") or "",
            "new": ex.get("new_selector") or ex.get("new_pattern") or "",
            "repair_result": e.get("repair_result", ""),
            "action": e.get("action", ""),
            "reason": e.get("detail", ""),
        })
        if len(out) >= limit:
            break
    return out


def analyse(shadow: Mapping[str, Any], *,
            ingested: int = 0,
            collected: int = 0,
            summary_gap: int = 0,
            source_health: Sequence[Mapping[str, Any]] = ()) -> TuneReport:
    """从一份 shadow 报告 + 入库回执，推出这一轮该做什么。

    纯函数：不碰文件、不发请求。这样它能被直接测，也能在不开浏览器的情况下
    拿历史报告重放。

    ``source_health`` 是 SourceBackoff.health() 的快照。带上它才能报出
    熔断和回访闸 —— 这两个是**真会改变结果**的动作，只报不改的那些
    （换选择器、放宽门禁）证据不足，动不了。
    """
    report = TuneReport(round=0)
    sources = list(shadow.get("sources") or [])
    by_reason: Mapping[str, Any] = shadow.get("by_reason") or {}
    buckets: Mapping[str, Any] = shadow.get("buckets") or {}
    total_sources = len(sources) or 1

    # ── 采集段 ─────────────────────────────────────────────────────────
    # 先报已经生效的三个动作（回访闸 / 熔断 / 轮间节流）。它们不是"建议"，
    # 是 run.py 上一轮就真做过的事 —— 不记下来，自迭代的"监督作用"就无从证明。
    circuited = [str(h["source_id"]) for h in source_health if h.get("circuited")]
    if circuited:
        report.findings.append(Finding(
            stage="collect", kind="circuited_sources", sources=circuited,
            evidence={"count": len(circuited),
                      "health": [h for h in source_health if h.get("circuited")]},
            action="已踢出本趟采集；连续出过货会自动恢复", auto=True,
            detail=f"{len(circuited)} 个源被熔断（访问多次零产出、或长期没出货）。"
                   f"这是真会改变结果的动作：{circuited[:6]}"))

    next_due = int(shadow.get("next_due_epoch") or 0)
    if next_due:
        import time as _time

        wait_min = max(0, int((next_due - _time.time()) // 60))
        if wait_min >= 1:
            report.findings.append(Finding(
                stage="collect", kind="pacing_wait", sources=[],
                evidence={"next_due_epoch": next_due, "wait_minutes": wait_min},
                action="轮间节流：等源到期再起下一轮", auto=True,
                detail=f"下一个源约 {wait_min} 分钟后可访问，"
                       f"轮次会等它而不是空转。"))

    drained = [str(s.get("source_id")) for s in sources
               if not s.get("records") and "early stop" in _notes_of(s)]
    if drained:
        report.findings.append(Finding(
            stage="collect", kind="drained_sources", sources=drained,
            evidence={"count": len(drained), "total_sources": total_sources},
            action="交给 run.py 的 SourceBackoff 退避，本轮不必额外处理",
            auto=True,
            detail=f"{len(drained)} 个源报告 early stop（列表全是已知 URL）。"
                   f"这是正常增量空转：政府站发布慢，采完就是采完了。"))

    slow_zero = [str(s.get("source_id")) for s in sources if _is_slow_zero(s)]
    if len(slow_zero) >= SLOW_ZERO_MIN_SOURCES:
        report.findings.append(Finding(
            stage="collect", kind="time_budget_wasted", sources=slow_zero,
            evidence={"count": len(slow_zero),
                      "threshold_seconds": SLOW_ZERO_SECONDS,
                      "seconds": {str(s.get("source_id")): s.get("seconds")
                                  for s in sources if _is_slow_zero(s)}},
            action="下次调低 rate_limit.source_budget_seconds，或给这些源加退避",
            auto=False,
            detail=f"{len(slow_zero)} 个源烧满时间预算仍然 0 产出，"
                   f"合计约 {sum(float(s.get('seconds') or 0) for s in sources if _is_slow_zero(s))/60:.0f} 分钟。"))

    # ── 抽取段 ─────────────────────────────────────────────────────────
    no_links = [str(s.get("source_id")) for s in sources
                if "no links found" in _notes_of(s)]
    if no_links:
        report.findings.append(Finding(
            stage="extract", kind="no_links", sources=no_links,
            evidence={"count": len(no_links)},
            action="调 lighthouse/repair.py 重新提选择器；采集期挂钩已推导方案",
            auto=False,
            detail=f"{len(no_links)} 个源列表页取不到链接，选择器可能已失效。"))

    # 采集期挂钩记下来的入口失效事件。这是**已经推导过的修复方案**，不是待办 ——
    # 过去这里只能写「调 repair.py 重新提选择器」，把球踢给一个不会被调的脚本。
    # repair.py 并入选择器修复能力后，这里直接把结论摆出来。
    broken_events = _recent_broken_events()
    if broken_events:
        # event_bus 里 repair_result='candidate' = 推导出方案但未写回；
        # 'skipped' = 连方案都推不出（判定修不了，不硬改）。
        fixable = [e for e in broken_events
                   if e["repair_result"] == "candidate"]

        report.findings.append(Finding(
            stage="extract", kind="selector_broken", sources=sorted(
                {str(e["source_id"]) for e in broken_events}),
            evidence={"count": len(broken_events),
                      "fixable": len(fixable),
                      "events": broken_events[:10]},
            action=(f"{len(fixable)} 个源推导出修复方案；写回前须过 before/after 验证"
                    if fixable else "都修不了，保持现状（不硬改）"),
            auto=False,
            detail=f"感知到 {len(broken_events)} 次入口失效，"
                   f"推导成功 {len(fixable)} 次。"
                   f"涉及源：{sorted({str(e['source_id']) for e in broken_events})[:6]}。"))

    all_dropped = [str(s.get("source_id")) for s in sources
                   if s.get("dropped") and not s.get("records")]
    if all_dropped:
        per_source: dict[str, int] = {}
        for s in sources:
            sid = str(s.get("source_id"))
            if s.get("dropped") and not s.get("records"):
                per_source[sid] = int(s.get("dropped"))
        heavy = {k: v for k, v in per_source.items() if v >= DROPPED_ONLY_MIN}
        report.findings.append(Finding(
            stage="extract", kind="all_dropped", sources=list(per_source),
            evidence={"per_source": per_source, "heavy": heavy,
                      "by_reason": dict(by_reason)},
            action=("核对 heavy 源的 list_selector 是否抓到了栏目导航而非公告列表"
                    if heavy else "样本不足，先观察"),
            auto=False,
            detail=f"{len(per_source)} 个源「链接取到了但全被门禁拦下」，"
                   f"共 {sum(per_source.values())} 条。丢弃原因分布："
                   f"{dict(sorted(by_reason.items(), key=lambda kv: -kv[1])[:4])}"))

    # ── 摘要段 ─────────────────────────────────────────────────────────
    if collected and summary_gap / collected >= SUMMARY_GAP_RATIO:
        gap_sources = [str(s.get("source_id")) for s in sources
                       if not s.get("records") and s.get("dropped")]
        report.findings.append(Finding(
            stage="summary", kind="summary_gap_high",
            sources=gap_sources,
            evidence={"collected": collected, "no_summary": summary_gap,
                      "ratio": round(summary_gap / collected, 3),
                      "threshold": SUMMARY_GAP_RATIO},
            action="抽检这些源的正文形态；图文解读策抽不出句子属正常，别当脏数据删",
            auto=False,
            detail=f"本轮 {summary_gap}/{collected} 条写不出摘要"
                   f"（{summary_gap/collected:.0%}）。实测库里同判据的记录里"
                   f"约 97% 是正文为图片的真公告，删除只会丢真政策。"))

    # ── 分析段（门禁丢弃分布）──────────────────────────────────────────
    if by_reason:
        top, top_n = max(by_reason.items(), key=lambda kv: kv[1])
        dropped_total = sum(by_reason.values()) or 1
        if top_n / dropped_total >= 0.6 and top_n >= 20:
            report.findings.append(Finding(
                stage="analyze", kind="gate_drop_concentrated",
                sources=[],
                evidence={"top_reason": top, "count": top_n,
                          "share": round(top_n / dropped_total, 3),
                          "all": dict(by_reason)},
                action="别动全局阈值。先验证这个源抓到的是不是栏目导航页",
                auto=False,
                detail=f"丢弃高度集中在 {top}（{top_n}/{dropped_total}）。"
                       f"全局阈值经实测是对的：link_flood 的 25% 阈值下，"
                       f"全库链接占比 P50~P99 全是 0.0%，超阈的 7 条全是索引页/404。"
                       f"所以这里只提示，不自动放宽。"))

    report.findings.append(Finding(
        stage="collect", kind="coverage",
        sources=[],
        evidence={"buckets": dict(buckets), "records": shadow.get("records"),
                  "dropped": shadow.get("dropped"), "ingested": ingested},
        detail=f"本轮源覆盖：{dict(buckets)}；采集 {shadow.get('records')} 条，"
               f"入库 {ingested} 条。"))

    report.buckets = dict(buckets)
    report.records = int(shadow.get("records") or 0)
    report.dropped = int(shadow.get("dropped") or 0)
    report.ingested = int(ingested)
    return report


def save(report: TuneReport, out_dir: Path, round_index: int) -> Path:
    """报告落盘，可回看每轮为什么调了什么。"""
    report.round = round_index
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"tune-{round_index:03d}.json"
    path.write_text(report.to_json(), encoding="utf-8")
    return path


def summarise(report: TuneReport) -> str:
    """人看的一行行结论。"""
    lines = []
    for f in report.findings:
        mark = "!" if f.auto else "-"
        lines.append(f"  [{f.stage}/{mark}] {f.kind}: {f.detail}")
    return "\n".join(lines)
