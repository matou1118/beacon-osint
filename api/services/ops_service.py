from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from storage.db import Database
from storage.models import SourceSyncState

# status.json 只在轮次之间写，一轮采集期间它停在开轮时的值。
# 前端必须显示快照时间，否则会把陈旧数据读成实时状态。
LIGHTHOUSE_STATUS_PATH = Path("data/runtime/lighthouse/status.json")
LIGHTHOUSE_TIERS_PATH = Path("data/runtime/lighthouse/source_tiers.json")
LIGHTHOUSE_DOCTOR_PATH = Path("data/runtime/lighthouse/source_doctor.json")

# 连续失败次数分桶。20+ 单独成桶是因为这批源已经废了，
# 和"偶发抖动"（1-4）在处置动作上完全不同。
FAILURE_BUCKETS = (("0", 0, 0), ("1-4", 1, 4), ("5-19", 5, 19), ("20+", 20, None))


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 存回来的时间戳可能不带时区，统一按 UTC 比较。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value).astimezone(timezone.utc).isoformat()
    return str(value)


def query_source_health(db: Database, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    tier_doc = query_source_tiers()
    tier_by_source: dict[str, dict[str, Any]] = tier_doc.get("sources") or {}

    # 走 ORM 而不是裸 SQL —— 裸 text() 拿回来的日期是字符串，
    # SQLite 不做类型转换，跟 now 比较会直接抛 AttributeError。
    with db.session() as session:
        rows = session.execute(select(SourceSyncState)).scalars().all()

    total = len(rows)
    failed = [r for r in rows if r.status != "ok"]

    tiers: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.cadence_tier or "unknown"
        bucket = tiers.setdefault(
            key,
            {"cadence_tier": key, "interval_seconds": row.interval_seconds, "ok": 0, "error": 0},
        )
        bucket["error" if row.status != "ok" else "ok"] += 1
    tier_list = sorted(tiers.values(), key=lambda t: (t["interval_seconds"] or 0, t["cadence_tier"]))

    buckets = []
    for label, low, high in FAILURE_BUCKETS:
        count = sum(
            1
            for row in rows
            if row.consecutive_failures >= low
            and (high is None or row.consecutive_failures <= high)
        )
        buckets.append({"label": label, "count": count})

    sources = []
    for row in rows:
        next_due = _aware(row.next_due_at)
        # next_due_at 已经过点却还没成功 = 该跑没跑。这比 error 字符串有用得多：
        # 55 个源的 last_error 全是字面量 "unknown error"，没有诊断价值。
        overdue = next_due is not None and next_due < now
        tier = tier_by_source.get(row.source_id) or {}
        sources.append(
            {
                "source_id": row.source_id,
                "cadence_tier": row.cadence_tier,
                "status": row.status,
                "consecutive_failures": row.consecutive_failures,
                "last_attempt_at": _iso(row.last_attempt_at),
                "last_success_at": _iso(row.last_success_at),
                "next_due_at": _iso(row.next_due_at),
                "last_error": row.last_error,
                "overdue": overdue,
                "collect_tier": tier.get("tier"),
                "tier_reason": tier.get("reason"),
                "records": tier.get("records"),
                "dry_streak": tier.get("dry_streak"),
                "days_since_output": tier.get("days_since_output"),
                "render_share": tier.get("render_share"),
                "dup_body_records": tier.get("dup_body_records"),
                "newest_publish": tier.get("newest_publish"),
            }
        )
    sources.sort(key=lambda s: (-s["consecutive_failures"], s["source_id"]))

    # 表本身的 freshness。这不是装饰：这张表上一次写入停在 2026-09-28，
    # 而采集一直在正常跑（当天还入库 237 条）。不把「数据有多旧」摆到台面上，
    # 看的人会把 8 天前的快照当成此刻的状态 —— 指标静默腐烂比指标缺失更危险。
    newest_attempt = max(
        (r.last_attempt_at for r in rows if r.last_attempt_at is not None),
        default=None,
    )
    state_age_seconds = None
    if newest_attempt is not None:
        state_age_seconds = max(0, int((now - _aware(newest_attempt)).total_seconds()))

    return {
        "totals": {
            "total": total,
            "ok": total - len(failed),
            "error": len(failed),
            "failed_20plus": sum(1 for s in sources if s["status"] != "ok" and s["consecutive_failures"] >= 20),
            "never_succeeded": sum(1 for s in sources if s["status"] != "ok" and s["last_success_at"] is None),
            "overdue": sum(1 for s in sources if s["status"] != "ok" and s["overdue"]),
            "last_write_at": _iso(newest_attempt),
            "state_age_seconds": state_age_seconds,
            # 超过一天没写就说明写者（现在是灯塔）又脱钩了。
            "state_stale": state_age_seconds is None or state_age_seconds > 86400,
        },
        "tiers": tier_list,
        "buckets": buckets,
        "sources": sources,
        "collect": {
            "available": tier_doc.get("available", False),
            "updated_at": tier_doc.get("updated_at"),
            "total": tier_doc.get("total"),
            "policy": tier_doc.get("policy") or [],
            "distribution": tier_doc.get("distribution") or [],
            "dry_streak": tier_doc.get("dry_streak") or [],
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _age(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


# source_tiers.json 里有 T0–T5 的采集策略（回访间隔/深度/条数上限/路由），
# 以及每个源的判定理由。source_sync_state 只知道"失败了"，
# 这份数据才说明"为什么"。两者必须一起看。
def query_source_tiers() -> dict[str, Any]:
    if not LIGHTHOUSE_TIERS_PATH.exists():
        return {"available": False, "reason": "没有 source_tiers.json", "tiers": {}, "policy": {}, "distribution": []}

    data = _load_json(LIGHTHOUSE_TIERS_PATH)
    tiers = data.get("tiers") or {}
    policy = data.get("policy") or {}

    distribution: dict[str, int] = {}
    for entry in tiers.values():
        key = str(entry.get("tier") or "?")
        distribution[key] = distribution.get(key, 0) + 1

    signals: dict[str, dict[str, int]] = {}
    for entry in tiers.values():
        sig = entry.get("signals") or {}
        streak = sig.get("dry_streak")
        if streak is None:
            continue
        signals[str(streak)] = signals.get(str(streak), 0) + 1

    return {
        "available": True,
        "path": str(LIGHTHOUSE_TIERS_PATH),
        "updated_at": _age(LIGHTHOUSE_TIERS_PATH),
        "total": len(tiers),
        "policy": [
            {
                "tier": key,
                "label": val.get("label"),
                "revisit_seconds": val.get("revisit_seconds"),
                "depth": val.get("depth"),
                "max_items": val.get("max_items"),
                "routes": val.get("routes") or [],
            }
            for key, val in sorted(policy.items())
        ],
        "distribution": [
            {"tier": k, "count": v} for k, v in sorted(distribution.items())
        ],
        "dry_streak": [
            {"streak": int(k), "count": v} for k, v in sorted(signals.items(), key=lambda i: int(i[0]))
        ],
        "sources": {
            sid: {
                "tier": entry.get("tier"),
                "reason": entry.get("reason"),
                "records": (entry.get("signals") or {}).get("records"),
                "dry_streak": (entry.get("signals") or {}).get("dry_streak"),
                "days_since_output": (entry.get("signals") or {}).get("days_since_output"),
                "render_share": (entry.get("signals") or {}).get("render_share"),
                "dup_body_records": (entry.get("signals") or {}).get("dup_body_records"),
                "dead_pages": (entry.get("signals") or {}).get("dead_pages"),
                "newest_publish": (entry.get("signals") or {}).get("newest_publish"),
            }
            for sid, entry in tiers.items()
        },
    }


# source_doctor.json 给出"为什么停了"和"该怎么办"。它的 ok 不等于健康 ——
# 111 个 ok 里有相当一部分是"未启用"，无需处理；真正要动的是 warn。
def query_source_doctor() -> dict[str, Any]:
    if not LIGHTHOUSE_DOCTOR_PATH.exists():
        return {"available": False, "reason": "没有 source_doctor.json"}

    data = _load_json(LIGHTHOUSE_DOCTOR_PATH)
    results = data.get("results") or []
    flagged = [r for r in results if r.get("status") != "ok"]

    return {
        "available": True,
        "path": str(LIGHTHOUSE_DOCTOR_PATH),
        "updated_at": _age(LIGHTHOUSE_DOCTOR_PATH),
        "at": data.get("at"),
        "total": data.get("total"),
        "by_status": data.get("by_status") or {},
        "by_tier": data.get("by_tier") or {},
        "collective": data.get("collective") or {},
        "flagged": [
            {
                "source_id": r.get("source_id"),
                "status": r.get("status"),
                "tier": r.get("tier"),
                "reason": r.get("reason"),
                "hint": r.get("hint"),
                "evidence": r.get("evidence"),
            }
            for r in flagged[:200]
        ],
    }


def query_lighthouse_status(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else LIGHTHOUSE_STATUS_PATH
    if not target.exists():
        return {"available": False, "reason": "没有 lighthouse 状态快照", "path": str(target)}

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"状态快照无法解析：{exc}", "path": str(target)}

    snapshot = target.stat()
    rounds = raw.get("round_log") or []
    productive = [r for r in rounds if (r.get("gained") or 0) > 0]

    return {
        "available": True,
        "path": str(target),
        "snapshot_at": datetime.fromtimestamp(snapshot.st_mtime, timezone.utc).isoformat(),
        "pid": raw.get("pid"),
        "started_at": raw.get("started_at"),
        "deadline": raw.get("deadline"),
        "rounds_planned": raw.get("rounds_planned"),
        "rounds_done": raw.get("rounds_done"),
        "db_start": raw.get("db_start"),
        "db_now": raw.get("db_now"),
        "last_error": raw.get("last_error"),
        "rounds_total": len(rounds),
        "rounds_productive": len(productive),
        "total_gained": sum((r.get("gained") or 0) for r in rounds),
        "round_log": rounds,
        "self_iteration": raw.get("self_iteration") or [],
    }