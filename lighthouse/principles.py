"""原则表：从事件流蒸馏可复用的修复原则。

为什么需要它
------------
两个自迭代系统各自记事件，但事件是**实例级**的（「src_gov_cn 这一次
no_links，反推出 a.detail-link，验证通过」）。要跨源、跨轮复用，需要
蒸馏成**原则级**的知识：

    (scope_type=no_links, failure_type=selector_broken)
      → 最佳动作：heal_selector，效用 0.72，适用 5 次

原则按 ``(scope_type, failure_type)`` 分组。scope_type 从 scope 字段提取
（``source_id:xxx`` → ``source_id``，``metric:xxx`` → ``metric``）。
同一组内按 utility_score 取 Top 3，供 apply_principles() 路由。

效用分计算
----------
每次事件有 utility：
    * kept + verified=true  → +1.0
    * kept + verified=false → -1.0（修复反而有害）
    * reverted              → -0.5（试了没用）
    * candidate             → 0.0（只是候选，没生效）
    * observed              → 0.0（只报告没动手）

utility_score = Σ utility × decay(days_since_last_event)

decay(d) = 0.95^d  —— 30 天前的贡献衰减到 ~21%，90 天前 ~1.2%

退休：utility_score < -2.0 的原则标记 retired，不再自动应用。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lighthouse import event_bus
from lighthouse.atomic_write import atomic_write_json

_ROOT = Path(__file__).resolve().parents[1]
PRINCIPLES_FILE = _ROOT / "data" / "runtime" / "lighthouse" / "principles.json"

DECAY_BASE = 0.95          # 每天衰减 5%
RETIRE_THRESHOLD = -2.0    # 效用分低于此值退休
TOP_K = 3                  # apply_principles 返回前几条


def _scope_type(scope: str) -> str:
    """从 scope 提取类型：'source_id:xxx' → 'source_id'。"""
    return scope.split(":", 1)[0] if ":" in scope else scope


def _event_utility(rec: dict[str, Any]) -> float:
    """单条事件的效用分。"""
    result = rec.get("repair_result", "")
    verified = rec.get("verified")
    if result == "kept" and verified is True:
        return 1.0
    if result == "kept" and verified is False:
        return -1.0
    if result == "reverted":
        return -0.5
    return 0.0


def _decay_weight(ts: str, now: datetime) -> float:
    """时间衰减权重。ts 是 ISO 格式。"""
    try:
        event_time = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return 0.0
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    days = (now - event_time).total_seconds() / 86400.0
    if days < 0:
        days = 0.0
    return math.pow(DECAY_BASE, days)


def distill() -> list[dict[str, Any]]:
    """从事件流蒸馏原则。

    **不会覆盖人工沉淀的原则**（2026-09-30 修）。

    之前这个函数整体重写 `principles.json`，而它只从事件流算得出「哪个
    failure_type 多」，算不出「这个问题该怎么修」——后者来自人工修复。于是
    `seed_principles.py` 写进去的 4 条实测经验（404 死链烧预算、link_flood
    误杀、采干该加深翻页、预算不够该加时）在下一次 distill 时全部消失。

    症状很难发现：表里仍有 10 条、`apply_count` 累计上千，看着一切正常，
    只是查 `budget_exhausted` 时命中 0 条 —— 闭环静默失效，不报错。

    现在改成：**先算出蒸馏结果，再与人工沉淀的合并**。判据是
    `why` 字段（人工写的带，蒸馏出的没有）。

    合并前**直接读文件**而不是调 load()：load() 在文件不存在/解析失败时
    会回落到 distill()，而 distill() 又调 merge_principles(base=load()) →
    load() → distill()，无限递归（2026-09-30 实测 9 个测试报
    RecursionError）。读不到就当作空表，不回落。
    """
    fresh = _distill_principles()
    existing: list[dict[str, Any]] = []
    if PRINCIPLES_FILE.exists():
        try:
            existing = json.loads(PRINCIPLES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = []
    merged = merge_principles(fresh, keep_manual=True, base=existing)
    try:
        atomic_write_json(PRINCIPLES_FILE, merged)
    except OSError:
        pass
    return merged


def _distill_principles() -> list[dict[str, Any]]:
    """从 events.jsonl 蒸馏原则表。返回原则列表，同时写盘。

    每条原则：
        {
          "key": "no_links|selector_broken",
          "scope_type": "no_links",
          "failure_type": "selector_broken",
          "best_action": "heal_selector",
          "utility_score": 2.34,
          "apply_count": 5,
          "success_count": 3,
          "fail_count": 1,
          "last_ts": "2026-09-30T...",
          "retired": false,
          "actions": [{"action": "heal_selector", "utility": 2.34, "count": 5}, ...]
        }
    """
    events = event_bus.read_events()
    now = datetime.now(timezone.utc)

    # 按 (scope_type, failure_type, action) 聚合
    groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"utility": 0.0, "count": 0, "success": 0, "fail": 0, "last_ts": ""}
    )
    for rec in events:
        st = _scope_type(rec.get("scope", ""))
        ft = rec.get("failure_type", "")
        act = rec.get("action", "")
        if not st or not ft or not act:
            continue
        key = (st, ft, act)
        u = _event_utility(rec)
        w = _decay_weight(rec.get("ts", ""), now)
        groups[key]["utility"] += u * w
        groups[key]["count"] += 1
        if u > 0:
            groups[key]["success"] += 1
        elif u < 0:
            groups[key]["fail"] += 1
        ts = rec.get("ts", "")
        if ts > groups[key]["last_ts"]:
            groups[key]["last_ts"] = ts

    # 按 (scope_type, failure_type) 合并，取最佳 action
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (st, ft, act), info in groups.items():
        by_group[(st, ft)].append({
            "action": act,
            "utility": round(info["utility"], 4),
            "count": info["count"],
            "success": info["success"],
            "fail": info["fail"],
            "last_ts": info["last_ts"],
        })

    # ★ 事件流里的 action 字段可能存的是**函数名**而不是动作名。
    #
    # 2026-10-03 实测：`source_id|observed` 的 best_action 被蒸馏成
    # `log_observation`，`source_id|detail_pattern_stale` 被蒸馏成
    # `observe_only` —— 两者在 principle_applier.ACTIONS 里都映射不到
    # 真实动作（后者刻意映射 None），于是 `test_principle_loop` 的
    # 「所有 best_action 都可执行」当场失败。
    #
    # 根因是事件有两个来源：`_emit()` 写的是调用点传进来的 action，
    # 而观察类调用点传的是函数名。ACTIONS 是「动作名 -> 函数名」的映射，
    # 所以反查一次就能归位；反查不到就退回 observe（记一笔，不改配置），
    # 绝不写一个ACTIONS 里不存在的名字进去。
    _FN2ACTION: dict[str, str] = {}
    try:
        from lighthouse.principle_applier import ACTIONS as _ACTIONS
        ACTIONS = _ACTIONS
        _FN2ACTION = {v: k for k, v in ACTIONS.items() if v}
    except Exception:                                  # noqa: BLE001
        _FN2ACTION = {}

    def _canonical(action: str) -> str:
        name = str(action or "").strip()
        if not name:
            return "auto_observe"
        if name in ACTIONS and ACTIONS[name]:
            return name
        # 反查函数名 -> 动作名
        mapped = _FN2ACTION.get(name)
        if mapped and ACTIONS.get(mapped):
            return mapped
        # observe_only / report_only 在 ACTIONS 里刻意映射成None（它们就是
        # 「什么都不做」）。把它们当best_action 写进原则表，等于声明
        # 「这条原则最好的动作是假装处理」——而 test_principle_loop 要求
        # 每条best_action 都对应真实动作。降级成 observe（记一笔）。
        return "observe"

    principles: list[dict[str, Any]] = []
    for (st, ft), actions in by_group.items():
        for a in actions:
            a["action"] = _canonical(a["action"])
        actions.sort(key=lambda a: a["utility"], reverse=True)
        best = actions[0]
        score = best["utility"]
        # 「真动作」= 真的改了配置的动作。observe_only / report_only /
        # auto_observe / observe 只是记一笔，不产生任何系统行为。
        #
        # 为什么要单独一个数：apply_count 把它们算进去，于是
        # `drained_sources` 显示「应用 142 次」，看起来极勤快，实际一次行动
        # 都没执行（实测 2026-10-02）。用它判断「系统有没有在干活」会得出
        # 完全相反的结论。action_count 只数真动作。
        _MUTATING = ("deepen_pages", "widen_stale", "raise_budget",
                     "disable_terminal", "raise_floor")
        principles.append({
            "key": f"{st}|{ft}",
            "scope_type": st,
            "failure_type": ft,
            "best_action": best["action"],
            "utility_score": score,
            "apply_count": sum(a["count"] for a in actions),
            "action_count": sum(a["count"] for a in actions
                                if a["action"] in _MUTATING),
            "success_count": sum(a["success"] for a in actions),
            "fail_count": sum(a["fail"] for a in actions),
            "last_ts": max(a["last_ts"] for a in actions),
            "retired": score < RETIRE_THRESHOLD,
            "actions": actions,
        })

    principles.sort(key=lambda p: p["utility_score"], reverse=True)

    # 不在这里写盘 —— 由 distill() 合并人工原则后统一写。
    # 这里写会覆盖掉 merge 之前还没并进来的内容。
    return principles


def load() -> list[dict[str, Any]]:
    """读原则表。文件不存在时从事件流重新蒸馏。"""
    if PRINCIPLES_FILE.exists():
        try:
            return json.loads(PRINCIPLES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return distill()


def save_principles(rows: list[dict[str, Any]]) -> bool:
    """写原则表。返回是否成功。

    为什么需要它：`distill()` 每次都会**整体重写**文件，而它是从事件流
    重新算出来的 —— 手工沉淀进去的原则（比如今天修的 404 死链、link_flood
    误杀）一旦被 distill 覆盖就没了。这跟 `apply_count` 累计到 137 却
    `success_count` 全 0 是同一个病的两个症状：**经验只进不出，靠重新计算
    维持**。有了 save_principles，人工确认过的原则能留下来，distill 只负责
    补充它算不出来的那部分。
    """
    try:
        atomic_write_json(PRINCIPLES_FILE, rows)
        return True
    except OSError:
        return False


def merge_principles(rows: list[dict[str, Any]],
                     keep_manual: bool = False,
                     base: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """把新原则并进现有表：同 key 覆盖（保留累计计数），新 key 追加。

    ``keep_manual=True`` 时：蒸馏结果会与**人工沉淀的原则**合并，人工的优先
    保留 —— 判据是带 ``why`` 字段（seed_principles.py 写的实测依据）。没有它
    的话，每次 distill 都会把手工经验冲掉，而系统只从事件流算得出「哪个
    failure_type 多」，算不出「该怎么修」。

    ``base`` 显式传入现有表。**不能在这里调 load()** —— load() 在文件不存在
    或解析失败时会回落到 distill()，而 distill() 又调 merge_principles()
    → load() → distill()，无限递归（2026-09-30 实测 9 个测试报
    RecursionError就是这么来的）。
    """
    current = base if base is not None else (load() if keep_manual else [])
    by_key = {p.get("key"): p for p in current}
    for row in rows:
        old = by_key.get(row.get("key"))
        if old:
            # 人工沉淀的优先：它带 why，且动作是实测得出的
            if keep_manual and old.get("why"):
                merged = dict(row)
                merged["why"] = old["why"]
                merged["best_action"] = old.get("best_action",
                                                row.get("best_action"))
                row = merged
            # 保留累计计数，否则每并一次就把历史清零
            for field in ("apply_count", "action_count",
                          "success_count", "fail_count"):
                row[field] = max(int(row.get(field) or 0),
                                 int(old.get(field) or 0))
        by_key[row.get("key")] = row
    return list(by_key.values())


def apply_principles(scope: str, failure_type: str,
                     top_k: int = TOP_K) -> list[dict[str, Any]]:
    """查原则表，返回 Top-K 适用原则。

    参数：
        scope:        当前问题的 scope，如 "source_id:src_gov_cn"
        failure_type: 当前问题的 failure_type，如 "no_links"
        top_k:        返回前几条

    返回的每条原则包含 best_action 和 utility_score，调用方据此决定是否
    自动执行。retired 的原则不返回。
    """
    st = _scope_type(scope)
    all_principles = load()
    matched = [
        p for p in all_principles
        if p.get("scope_type") == st
        and p.get("failure_type") == failure_type
        and not p.get("retired", False)
    ]
    matched.sort(key=lambda p: p.get("utility_score", 0), reverse=True)
    return matched[:top_k]


def record_outcome(key: str, success: bool,
                   note: str = "") -> bool:
    """把一次动作的**实际效果**记到原则上，写回原则表。

    这是闭环最后一块拼图，也是「系统真的在迭代」的判据。

    之前原则表只有 `apply_count`（应用次数）而 `success_count` / `fail_count`
    恒为 0 —— 因为**没有任何地方回填效果**。于是：

      * `utility_score` 永远是初值，distill() 挑不出更好的动作
      * `best_action` 永远不变，系统学不到东西
      * 自动动作会一直重复执行（限额内），改到封顶也不停

    有了这个回填之后，动作多了就记 success、没效果就记 fail，distill 会
    重新排序 actions 表，`best_action` 可能换成别的 —— 那才是「进化」。

    参数：
        key:      原则的 key，如 "source_id|budget_exhausted"
        success:  True = 这次动作之后指标变好了
        note:     可选说明，写进 last_ts 旁边的备注

    返回是否写盘成功。
    """
    rows = load()
    hit = False
    for r in rows:
        if r.get("key") != key:
            continue
        hit = True
        if success:
            r["success_count"] = int(r.get("success_count") or 0) + 1
        else:
            r["fail_count"] = int(r.get("fail_count") or 0) + 1
        r["last_ts"] = _now_iso()
        # 效用按 成功 / (成功+失败) 重算，并给一点先验（0.5）避免全 0 时乱选
        ok = int(r.get("success_count") or 0)
        bad = int(r.get("fail_count") or 0)
        total = ok + bad
        r["utility_score"] = round((ok + 0.5) / (total + 1.0), 4) if total else 0.0
        # actions 表里的同名动作也同步更新
        for a in r.get("actions") or []:
            if a.get("action") == r.get("best_action"):
                a["success"] = ok
                a["fail"] = bad
                a["utility"] = r["utility_score"]
                a["last_ts"] = r["last_ts"]
        if note:
            r["last_note"] = note[:200]
    if not hit:
        return False
    return save_principles(rows)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stats() -> dict[str, Any]:
    """原则表摘要。"""
    principles = load()
    if not principles:
        return {"total": 0, "active": 0, "retired": 0, "top": []}
    active = [p for p in principles if not p.get("retired")]
    retired = [p for p in principles if p.get("retired")]
    return {
        "total": len(principles),
        "active": len(active),
        "retired": len(retired),
        "top": [
            {"key": p["key"], "score": p["utility_score"],
             "action": p["best_action"], "count": p["apply_count"]}
            for p in active[:5]
        ],
    }
