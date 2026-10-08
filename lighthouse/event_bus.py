"""统一事件流：两个自迭代系统共用的可观测性骨干。

为什么需要它
------------
System A（scripts/self_improve.py）写 ``data/self_improve_log.jsonl``，
System B（lighthouse/repair.py + selector_events 表）写 SQLite。两份日志
格式不同、字段不同，跨系统聚合要各写一份适配代码，而且 System A 的日志
只记 fix 尝试、不记失效感知 —— 修选择器的事件在 B 里，修摘要规则的事件
在 A 里，没有一份能回答「这一轮到底发生了什么」。

events.jsonl 是**只追加**的统一事件流，两个系统都往里写一行。格式：

    {
      "event_id": "2026-09-30T12:00:00#repair#src_gov_cn#1",
      "ts": "2026-09-30T12:00:00+00:00",
      "layer": "collector",            // collector / extract / summary / analyze
      "scope": "source_id:src_gov_cn", // 分组键，principles 蒸馏按它来
      "failure_type": "no_links",      // 机器可读的问题类型
      "action": "heal_selector",       // 采取的动作
      "repair_result": "kept",         // kept / reverted / candidate / skipped
      "verified": null,                // 下一轮复检结果：null / true / false
      "utility": 0.0,                  // 效用分，principles 蒸馏读这个
      "detail": "反推出 a.detail-link，5 条链接",  // 人看的说明
      "source": "repair"               // 哪个系统写的：repair / self_improve / self_tune
    }

设计取舍
--------
* **只追加，不修改**。verified 和 utility 的更新写一条新事件（event_id 带
  序号），不回头改旧行。这样没有并发写同一行的问题，也能完整回溯。
* **best-effort**。写失败不能拖垮采集（和 selector_events.record 一样）。
* **event_id 幂等**。同一次失效重放不会写出重复行：用 ``ts#source#scope#seq``
  生成，seq 在同一秒内递增。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
EVENTS_FILE = _ROOT / "data" / "runtime" / "lighthouse" / "events.jsonl"

# 线程安全：多 worker 并发写事件时不能交错。
_lock = threading.Lock()
_seq = 0


def _next_event_id(ts: str, source: str, scope: str) -> str:
    """同一秒内递增序号，保证 event_id 唯一。"""
    global _seq
    with _lock:
        _seq += 1
        return f"{ts}#{source}#{scope}#{_seq}"


def emit(*,
         layer: str,
         scope: str,
         failure_type: str,
         action: str,
         repair_result: str,
         source: str,
         detail: str = "",
         verified: bool | None = None,
         utility: float = 0.0,
         extra: dict[str, Any] | None = None) -> str | None:
    """写一条事件到 events.jsonl。返回 event_id，失败返回 None。

    参数：
        layer:         collector / extract / summary / analyze
        scope:         分组键，如 "source_id:src_gov_cn" 或 "metric:median_content_chars"
        failure_type:  机器可读的问题类型，如 no_links / selector_broken / summary_gap_high
        action:        采取的动作，如 heal_selector / set_constant / scale_config
        repair_result: kept / reverted / candidate / skipped / observed
        source:        哪个系统写的：repair / self_improve / self_tune
        detail:        人看的说明
        verified:      下一轮复检结果（null = 尚未复检）
        utility:       效用分（正 = 有用，负 = 有害，0 = 未知）
        extra:         额外字段，原样写入
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    event_id = _next_event_id(ts, source, scope)
    record: dict[str, Any] = {
        "event_id": event_id,
        "ts": ts,
        "layer": layer,
        "scope": scope,
        "failure_type": failure_type,
        "action": action,
        "repair_result": repair_result,
        "verified": verified,
        "utility": round(float(utility), 4),
        "detail": detail,
        "source": source,
    }
    if extra:
        record["extra"] = extra

    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(EVENTS_FILE, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return event_id
    except OSError:
        return None


def read_events(*,
                source: str = "",
                layer: str = "",
                scope: str = "",
                failure_type: str = "",
                limit: int = 0) -> list[dict[str, Any]]:
    """读事件流，按条件过滤。limit=0 表示不限。

    纯读，不加锁 —— 追加写不会读到半行（JSON 一行一事件）。
    """
    if not EVENTS_FILE.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = EVENTS_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if source and rec.get("source") != source:
            continue
        if layer and rec.get("layer") != layer:
            continue
        if scope and rec.get("scope") != scope:
            continue
        if failure_type and rec.get("failure_type") != failure_type:
            continue
        out.append(rec)
    if limit > 0:
        out = out[-limit:]
    return out


def update_verified(event_id: str, verified: bool, utility: float = 0.0) -> bool:
    """复检后追加一条 verified 事件，不回头改旧行。

    旧行的 verified 字段保持 null；principles 蒸馏时按 event_id 关联最新的
    verified 事件。这样既保持只追加语义，又能回溯复检历史。
    """
    events = read_events(limit=200)
    original = None
    for rec in reversed(events):
        if rec.get("event_id") == event_id:
            original = rec
            break
    if original is None:
        return False
    return emit(
        layer=original.get("layer", ""),
        scope=original.get("scope", ""),
        failure_type=original.get("failure_type", ""),
        action=original.get("action", ""),
        repair_result="verified",
        source=original.get("source", ""),
        detail=f"复检 event {event_id}: {'通过' if verified else '失败'}",
        verified=verified,
        utility=utility,
        extra={"verifies": event_id},
    ) is not None


def stats() -> dict[str, Any]:
    """事件流摘要，给汇报和测试用。"""
    events = read_events()
    if not events:
        return {"total": 0, "by_source": {}, "by_layer": {}, "by_result": {}}
    by_source: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    by_result: dict[str, int] = {}
    for rec in events:
        by_source[rec.get("source", "?")] = by_source.get(rec.get("source", "?"), 0) + 1
        by_layer[rec.get("layer", "?")] = by_layer.get(rec.get("layer", "?"), 0) + 1
        by_result[rec.get("repair_result", "?")] = by_result.get(rec.get("repair_result", "?"), 0) + 1
    return {
        "total": len(events),
        "by_source": by_source,
        "by_layer": by_layer,
        "by_result": by_result,
    }


# ── 熔断（2026-09-30 从 selector_events.py 迁入）────────────────────────
# 原先事件有两个账本：selector_events(SQLite) 存采集事件、events.jsonl 存
# 跨系统统一流，同一事件写两遍。现已合并到 jsonl，熔断逻辑随之迁过来 ——
# 它是「该不该继续自动写回」的判据，丢了就没人在修坏的东西。

MAX_RUNTIME_FIXES = 2        # 累计自动写回次数达到就降级
DISQUALIFY_VERIFIED_FALSE = 2  # 累计验证失败次数达到就永久降级


def should_auto_apply(scope: str) -> tuple[bool, str]:
    """这个 scope 还允许自动写回吗。返回 (允许, 原因)。

    规则（纯计数，可复算）：
      * 累计验证失败 >= ``DISQUALIFY_VERIFIED_FALSE`` -> 永久降级
      * 累计「已采纳」次数 >= ``MAX_RUNTIME_FIXES`` -> 降级
      * 只提过候选（candidate）、从没采纳过 -> 仍然允许（还没证明它不可靠）

    失败数是**累计**而不是「每轮重新数」。按轮重数的话，一个只写回一次的源
    永远停在「失败 1 次」，够不到阈值，熔断形同虚设 —— 这是 2026-09-30 修掉
    的一个真 bug。

    判据映射：``repair_result='kept'`` 表示方案被采纳（相当于旧表的
    apply_mode='runtime'），``verified=False`` 表示下一轮复检没通过。
    """
    try:
        events = read_events()
    except OSError:
        return True, "读不到事件流，先当允许（首次运行属正常）"

    mine = [e for e in events if e.get("scope") == scope]
    kept = [e for e in mine if e.get("repair_result") == "kept"]
    failed = [e for e in kept if e.get("verified") is False]

    if len(failed) >= DISQUALIFY_VERIFIED_FALSE:
        return False, (f"已验证失败 {len(failed)} 次（阈值 "
                       f"{DISQUALIFY_VERIFIED_FALSE}），永久降级为只报不改")
    if len(kept) >= MAX_RUNTIME_FIXES:
        return False, (f"已自动写回 {len(kept)} 次（阈值 {MAX_RUNTIME_FIXES}），"
                       f"降级为只报不改")
    return True, (f"已写回 {len(kept)} 次、验证失败 {len(failed)} 次，未达阈值")
