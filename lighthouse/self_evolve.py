"""自进化协调层：连接两个自迭代系统，统一事件流和原则表。

架构
----
    ┌─────────────────────────────────────────────┐
    │              self_evolve.step()               │  ← overnight.py 每轮后调用
    │  ┌──────────────┐    ┌──────────────────┐   │
    │  │  System B    │    │   System A       │   │
    │  │ self_tune    │    │  self_improve    │   │
    │  │ repair       │    │                  │   │
    │  └──────┬───────┘    └────────┬─────────┘   │
    │         │                     │              │
    │         ▼                     ▼              │
    │  ┌──────────────────────────────────┐       │
    │  │       event_bus.emit()           │       │  统一事件流
    │  │       events.jsonl               │       │
    │  └──────────────┬───────────────────┘       │
    │                 │                            │
    │                 ▼                            │
    │  ┌──────────────────────────────────┐       │
    │  │     principles.distill()         │       │  蒸馏原则
    │  │     principles.json              │       │
    │  └──────────────┬───────────────────┘       │
    │                 │                            │
    │                 ▼                            │
    │  ┌──────────────────────────────────┐       │
    │  │   apply_principles() 路由        │       │  下次决策
    │  └──────────────────────────────────┘       │
    └─────────────────────────────────────────────┘

step() 做三件事：
1. 把 self_tune 的 Finding 转写成事件（感知 → 记录）
2. 跑 System A 的 iterate_with_discover（判断 → 修复 → 记录）
3. 蒸馏原则表（记录 → 蒸馏）

不直接改配置。改配置的动作仍由 repair.py / self_improve.py 各自执行，
self_evolve 只负责让两个系统的事件汇入同一份流、同一份原则表。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lighthouse import event_bus, principles


def _finding_to_event(finding: Any, round_no: int,
                      seen: set[tuple[str, str, str]] | None = None) -> None:
    """把 self_tune.Finding 转写成事件。只记 observed，不改配置。

    ``seen`` 是本轮开始时已有的 (scope, failure_type, detail) 集合；命中就跳过。
    事件流要记的是**状态变化**，不是每轮快照 —— 不去重的话，同一条
    ``drained_sources`` 会每轮原样重写一遍，实测半小时灌了 61 条、
    ``coverage`` 54 条，全是同内容复读。后果有两个：事件流从信号变成日志垃圾，
    而 principles.distill() 和 should_auto_apply() 都在这份数据上算效用，
    复读会把统计整个带偏。

    detail 变了仍会写 —— 那说明问题变了（源数增减、耗时变化），是真正的状态变化。
    """
    stage = getattr(finding, "stage", "unknown")
    kind = getattr(finding, "kind", "unknown")
    sources = getattr(finding, "sources", []) or []
    auto = getattr(finding, "auto", False)
    detail = getattr(finding, "detail", "")

    # 每个 finding 可能涉及多个源，每个源一条事件
    if not sources:
        key = (f"stage:{stage}", kind, detail)
        if seen is not None:
            if key in seen:
                return
            seen.add(key)
        event_bus.emit(
            layer=stage,
            scope=f"stage:{stage}",
            failure_type=kind,
            action="observe" if not auto else "auto_observe",
            repair_result="observed",
            source="self_tune",
            detail=detail or f"{stage}/{kind}",
            extra={"round": round_no},
        )
        return

    for src in sources:
        scope = f"source_id:{src}"
        row_detail = detail or f"{stage}/{kind} on {src}"
        key = (scope, kind, row_detail)
        if seen is not None:
            if key in seen:
                continue
            seen.add(key)
        event_bus.emit(
            layer=stage,
            scope=scope,
            failure_type=kind,
            action="observe" if not auto else "auto_observe",
            repair_result="observed",
            source="self_tune",
            detail=row_detail,
            extra={"round": round_no, "source_id": src},
        )


def _step_to_event(step: Any) -> None:
    """把 self_improve.Step 转写成事件。"""
    metric = getattr(step, "metric", "unknown")
    defect_id = getattr(step, "defect_id", "")
    fix_id = getattr(step, "fix_id", "")
    applied = getattr(step, "applied", False)
    kept = getattr(step, "kept", False)
    before = getattr(step, "before", 0.0)
    after = getattr(step, "after", 0.0)
    notes = getattr(step, "notes", [])

    if not applied:
        repair_result = "skipped"
    elif kept:
        repair_result = "kept"
    else:
        repair_result = "reverted"

    # 效用分：kept → after - before（正=改善），reverted → -0.5
    if repair_result == "kept":
        direction = 1.0 if after >= before else -1.0
        utility = direction * abs(after - before) if before != 0 else 0.0
    elif repair_result == "reverted":
        utility = -0.5
    else:
        utility = 0.0

    event_bus.emit(
        layer="summary",
        scope=f"metric:{metric}",
        failure_type=defect_id,
        action=fix_id,
        repair_result=repair_result,
        source="self_improve",
        detail=f"{defect_id}/{metric}: {before}→{after} ({repair_result})",
        utility=utility,
        extra={"before": before, "after": after,
               "notes": notes[:3]},  # 只存前3条note，避免过长
    )


def step(round_no: int, findings: list[Any] | None = None,
         run_self_improve: bool = True) -> dict[str, Any]:
    """一轮的自进化步骤。由 overnight.py 在每轮采集后调用。

    参数：
        round_no:        轮次号
        findings:        self_tune.analyse() 的 findings（已由 overnight 计算）
        run_self_improve: 是否跑 System A 的 iterate_with_discover

    返回步骤摘要，写入 status.json 的 self_iteration 字段。
    """
    summary: dict[str, Any] = {
        "round": round_no,
        "events_emitted": 0,
        "self_improve_step": None,
        "principles_distilled": 0,
    }

    # 1. 转写 findings → 事件。同内容复读的去掉（见 _finding_to_event 的说明）：
    #    事件流记状态变化，不记每轮快照。
    seen: set[tuple[str, str, str]] = set()
    for rec in event_bus.read_events():
        seen.add((rec.get("scope", ""), rec.get("failure_type", ""),
                  rec.get("detail", "")))
    before_count = len(seen)

    if findings:
        for f in findings:
            _finding_to_event(f, round_no, seen=seen)

    events_before = event_bus.read_events()
    summary["events_emitted"] = len(events_before)
    summary["events_new"] = len(events_before) - before_count

    # 2. 跑 System A（摘要规则自迭代）
    # self_improve.record() 内部已写 event_bus，这里不再重复转写。
    if run_self_improve:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_self_improve", _ROOT / "scripts" / "self_improve.py")
            module = importlib.util.module_from_spec(spec)
            # 必须先注册进 sys.modules 再执行。self_improve.py 里有 @dataclass，
            # 而 Python 3.10 的 dataclasses 在处理字段（含 KW_ONLY）时会做
            # sys.modules[cls.__module__].__dict__ 查找 —— 模块没注册就是
            # AttributeError: 'NoneType' object has no attribute '__dict__'，
            # 崩在 import 阶段，self_improve 一次都跑不起来。
            sys.modules["_self_improve"] = module
            spec.loader.exec_module(module)

            metrics = module.measure()
            registry = module.load_registry()
            result = module.iterate_with_discover(metrics, registry)
            if result is not None:
                summary["self_improve_step"] = {
                    "defect": result.defect_id,
                    "metric": result.metric,
                    "fix": result.fix_id,
                    "applied": result.applied,
                    "kept": result.kept,
                    "before": result.before,
                    "after": result.after,
                }
        except Exception as exc:                              # noqa: BLE001
            summary["self_improve_error"] = f"{type(exc).__name__}: {exc}"

    # 3. 蒸馏原则表
    try:
        plist = principles.distill()
        summary["principles_distilled"] = len(plist)
    except Exception as exc:                                  # noqa: BLE001
        summary["principles_error"] = f"{type(exc).__name__}: {exc}"

    events_after = event_bus.read_events()
    summary["events_total"] = len(events_after)

    return summary


def report() -> dict[str, Any]:
    """自进化系统整体状态，给汇报和测试用。"""
    return {
        "events": event_bus.stats(),
        "principles": principles.stats(),
    }
