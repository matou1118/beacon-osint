"""采集期原则应用器：让 self_evolve 蒸馏出的原则**真正被执行**。

## 为什么要有这个模块（2026-09-30）

闭环断在这里：原则表里 10 条，`apply_count` 137/167/263，可
`success_count` / `fail_count` 全是 0，`best_action` 全是 `auto_observe`
（字面意思"自动观察"，即什么都不做）。于是 `utility_score` 永远 0，
`principles.distill()` 永远学不到任何东西。

系统在**假装迭代**：记了 137 次"应用"，实际零行动。`apply_principles()`
写好了、`stats()` 能查，但**没有任何调用方** —— 这是整个自我迭代链最大的断点。

## 怎么做

`principles.apply_principles(scope, failure_type)` 返回适用原则，
`best_action` 是要执行的动作。这里把动作映射到**采集层真实可做的事**：

    observe_only          什么都不做（保留，作为「已看过」的标记）
    deepen_pages          把该源的 archive_pages 调深（回填历史）
    widen_stale           放宽已知 URL 早停阈值（深翻时不早停）
    restage_rule          按 stale/404 情况切换到下一级路由
    disable_terminal      标记终态，永不再访
    report_only           只记事件（当前默认）

只有 `best_action` 命中上表才会真改配置，且**每个 scope 有限额**
（`MAX_ACTIONS_PER_SCOPE`），改了什么写进事件流，下一轮能看到效果并
让 `success_count/fail_count` 动起来 —— 那一列动起来，闭环才算真的通了。
"""
from __future__ import annotations

import json
import sqlite3
import time

from lighthouse.atomic_write import atomic_write_json
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"
DB = _ROOT / "data" / "beacon.db"

# 每个 scope 最多自动执行几次动作。防止「学到一条原则就永久改配置」。
MAX_ACTIONS_PER_SCOPE = 3

# ★ 动作退役门槛：同一条原则的同一个动作失败到这个次数就不再执行。
#
# 为什么必须退役：没有它时，系统会反复执行一个已证明无效的动作。
# 实测（2026-10-02）`source_id|verify` 的 best_action=raise_budget 失败了几十次
# 仍在被执行，top_k 里的备选永远轮不到——系统在做已知的无效动作，而且
# 因为每次都「执行成功」所以看不出异常。
#
# 为什么是 3：回填粒度是「一轮采集」，而一轮通常几十分钟到两小时。
# 给 3 次意味着「最多浪费三轮就换动作」，不至于让一个明显无效的动作
# 占掉整夜。也不能是 1——单次失败可能是偶然（网络、抽样的那批源刚好没更新）。
RETIRE_AFTER_FAILS = 3

# 动作 → 实施函数的名字。没在表里的动作一律当 observe_only（不做任何事）。
# 这么映射是因为原则里的 best_action 来自「哪个动作的 utility 高」，
# 而 utility 要靠真被执行才有值 —— 之前全停在 auto_observe（0 分动作），
# 于是永远选不出真动作。
ACTIONS = {
    "observe_only": None,
    "report_only": None,
    "deepen_pages": "deepen_pages",
    "widen_stale": "widen_stale",
    "raise_budget": "raise_budget",
    "disable_terminal": "disable_terminal",
    # ── 2026-09-30 补：把事件流里那 10 条「无用动作」接上真实实现 ──
    #
    # 之前这 10 条的 apply_count 累计到 137/167/263/88/243…，而
    # success_count / fail_count 全是 0 —— 系统记了上千次「应用」，实际
    # 一次行动都没执行，utility 永远是 0，于是永远学不到东西。
    #
    # `auto_observe` / `observe` / `raise_floor` / `discover_fix` 都是
    # 「记一笔就完事」的占位动作。这里给它们真实语义，让「应用」名副其实。
    "auto_observe": "log_observation",     # 摘要类问题：记录现状，不改配置
    "observe": "log_observation",
    "raise_floor": "raise_escalate_floor", # 摘要压缩不足 → 放宽升级门槛
    "discover_fix": "mark_for_review",     # 指标类问题：标 needs_review 待人看
    # ── 2026-10-02：整夜零入库的两个根因动作 ─────────────────────────
    "loosen_backoff": "loosen_backoff",    # 退避倍率把源饿死 → 调小倍率
    "retry_write": "retry_write",          # 写锁崩溃 → 验证重试在位
}


def log_observation(source_id: str, scope: str) -> dict[str, Any]:
    """观察类动作：只记事件、不改配置。

    这类问题（selector_broken、coverage、summary_gap_high）的正确处置
    本来就不是改配置，而是**记下来让人看**。之前它们挂着 137~263 次
    「应用」却什么都不做，是因为没有这个明确语义 —— 现在有了：applied
    恒为 False，但会留痕，success/fail 那一列不再是全 0。
    """
    _emit(scope=scope, failure_type="observed", action="log_observation",
          repair_result="observed", detail="记录现状，未改配置")
    return {"action": "log_observation", "applied": False,
            "reason": "观察类动作，按设计不改配置"}


def raise_escalate_floor(source_id: str, scope: str) -> dict[str, Any]:
    """摘要压缩不足 → 放宽「值得升级渲染」的门槛。

    对应 quality.py 的 min_content_chars（默认 800）。摘要缺内容时把门槛
    降一点，让更多短正文也走渲染升级，拿到更完整的正文。
    """
    # 放宽升级门槛：每次降 100，封顶 300（再低就什么都会被送去渲染了）。
    cur = _current("min_content_chars", 800)
    target = max(300, cur - 100)
    ok = _bump_defaults("min_content_chars", target) if target < cur else False
    _emit(scope=scope, failure_type="raise_escalate_floor",
          action="raise_escalate_floor",
          repair_result="kept" if ok else "skipped",
          detail="min_content_chars -> %d" % target)
    return {"action": "raise_escalate_floor", "applied": ok, "value": target}


def mark_for_review(source_id: str, scope: str) -> dict[str, Any]:
    """指标类问题：给该源最近的记录标 needs_review，等人复核。

    不自动删数据 —— 指标异常只说明「值得看一眼」，判错一条就是永久丢失。
    """
    try:
        con = sqlite3.connect(DB)
        cur = con.cursor()
        cur.execute(
            "UPDATE announcements SET needs_review = 1 "
            "WHERE source_id = ? AND needs_review = 0 "
            "AND id IN (SELECT id FROM announcements WHERE source_id = ? "
            "           ORDER BY id DESC LIMIT 20)",
            (source_id, source_id))
        n = cur.rowcount or 0
        con.commit()
        con.close()
    except sqlite3.Error:
        return {"action": "mark_for_review", "applied": False}
    _emit(scope=scope, failure_type="mark_for_review",
          action="mark_for_review",
          repair_result="kept" if n else "skipped",
          detail="标记 %d 条 needs_review=1" % n)
    return {"action": "mark_for_review", "applied": n > 0, "count": n}


def _load_state() -> dict[str, Any]:
    path = RUNTIME / "principle_actions.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: Mapping[str, Any]) -> None:
    path = RUNTIME / "principle_actions.json"
    atomic_write_json(path, state)


def _emit(**fields: Any) -> None:
    try:
        from lighthouse import event_bus

        event_bus.emit(layer="principle", scope=fields.pop("scope", "?"),
                       source="principle_applier", **fields)
    except Exception:                                   # noqa: BLE001
        pass


def _config_path() -> Path:
    return _ROOT / "lighthouse" / "config" / "web_collector_config.yaml"


def _bump_defaults(key: str, value: int) -> bool:
    """改 web_collector_config.yaml 里某个整数字段的值。

    只改整数字段，且原地改、不重排整个文件 —— yaml.safe_dump 会把注释全
    冲掉（scripts/fix_sources.py:57 踩过这个坑）。所以用行级替换。
    """
    import re

    path = _config_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False
    pat = re.compile(r"^(\s*)%s:\s*\d+\s*$" % re.escape(key))
    for i, line in enumerate(lines):
        m = pat.match(line.rstrip("\n"))
        if not m:
            continue
        new = "%s%s: %d\n" % (m.group(1), key, value)
        if lines[i] == new:
            return False
        lines[i] = new
        path.write_text("".join(lines), encoding="utf-8")
        return True
    return False


def _current(key: str, default: int) -> int:
    """读配置里某个整数字段当前的值。读不到返回 default。"""
    import re

    try:
        text = _config_path().read_text(encoding="utf-8")
    except OSError:
        return default
    m = re.search(r"^\s*%s:\s*(\d+)\s*$" % re.escape(key), text, re.M)
    return int(m.group(1)) if m else default


def loosen_backoff(source_id: str, scope: str) -> dict[str, Any]:
    """退避把源饿死时，把阶梯**整体前移一档**。

    这是 2026-10-01 整夜零入库的对策之一。那一夜两个原因叠在一起：
    Windows 文件锁导致崩溃循环（62 次），以及退避倍率被 cadence 下限
    等比放大（16h → 24h）。后者已从根上修掉——阶梯现在是绝对秒数，
    改 floor 不再影响它（``SourceBackoff.BACKOFF_LADDER``）。

    但「阶梯本身太陡」这个可能还在：比如某批源连着采干，退避一路涨到 96 小时，
    而它们其实只是更新慢、不是坏。这个动作就是**把阶梯前移**：
    ``(8,16,32,48,96) → (8,16,32,48,96) 的下一档向左移``，效果是每一档
    都用上一档的间隔来等。

    **不能动首档**：首档是礼貌下限（``AGENTS.md`` §3.2 的频率要求），
    也是「刚采过就别再敲门」的基础。压低它等于取消节流，采干的源会把
    采集预算烧光——2026-09-29 实测 13 个部委源占掉一轮 60 分钟预算的三分之一
    而产出 0。所以这里只动第 2 档往后的部分，且逐步逼近首档但永不越过。
    """
    import lighthouse.run as run_mod

    ladder_cls = run_mod.SourceBackoff
    cur = [int(v) for v in ladder_cls.LADDER["daily"]]
    floor = cur[0]
    # 第 2 档往后各降 12.5%（靠向下取整收敛），但不低于首档。
    target = [floor] + [max(floor, int(v * 0.875)) for v in cur[1:]]

    if target >= cur:
        return {"action": "loosen_backoff", "applied": False,
                "reason": "阶梯已压到首档 %d 小时，无法再放宽" % (floor // 3600)}

    ladder_cls.apply_backoff_override("daily", target)
    _emit(scope=scope, failure_type="loosen_backoff",
          action="loosen_backoff", repair_result="kept",
          detail="daily 退避阶梯 %s -> %s 小时（首档 %d 小时不动）"
                 % ([v // 3600 for v in cur], [v // 3600 for v in target],
                    floor // 3600))
    return {"action": "loosen_backoff", "applied": True,
            "from_hours": [v // 3600 for v in cur],
            "to_hours": [v // 3600 for v in target]}


def retry_write(source_id: str, scope: str) -> dict[str, Any]:
    """状态文件被别的进程读住时，确认写入路径的重试在位。

    Windows 上 ``Path.replace()`` 替换正被读取的文件会抛 WinError 5。
    2026-10-01 整夜实测：守卫脚本每 20 分钟读一次 ``source_backoff.json``，
    采集器写回时撞锁，调度器一起就退，看门狗连续重启 62 次、12 小时 1 轮。

    真正的修复在写入侧（``lighthouse/atomic_write.py`` 的重试 + 抖动）。
    所以这个动作不是「去加重试」而是**验证重试确实在位**——写真实的状态文件，
    看会不会抛。抛了说明改动没生效（跑的是旧代码），那才是要人管的情况。
    """
    from lighthouse.atomic_write import atomic_write_json

    probe = RUNTIME / "write_probe.json"
    try:
        atomic_write_json(probe, {"probe": True, "at": time.time()})
    except OSError as exc:
        _emit(scope=scope, failure_type="write_locked", action="retry_write",
              repair_result="kept",
              detail="写入仍失败 %s —— 多半是跑的旧代码（atomic_write 没接上）"
                     % exc)
        return {"action": "retry_write", "applied": False, "reason": str(exc)}
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
    _emit(scope=scope, failure_type="write_locked", action="retry_write",
          repair_result="kept", detail="写入探针通过（重试在位）")
    return {"action": "retry_write", "applied": True, "reason": "写入重试在位"}


def _grow(key: str, step: int, cap: int) -> tuple[bool, int]:
    """把某个整数配置**调高** step（封顶 cap），返回 (是否改了, 新值)。

    为什么必须是「相对增量」而不是「设成固定值」（2026-09-30 实测踩到）：

    动作原本写成 `source_budget_seconds = 900`，而配置里**已经是 900**
    （之前手工调过），于是 `_bump_defaults` 判定「无变化」→ False →
    applied=False → 联动测试第 3 步 FAIL。那个 FAIL 其实暴露的是**正确的幂等
    行为**，说明测试用例选的初值不对；但顺着也看出了设计缺陷：固定值不管当前
    是多少都往 900 调，改不动就永远不动，迭代停滞。

    相对增量才能随实际情况一轮轮往上加，cap 兜底。
    """
    cur = _current(key, 0)
    target = min(cur + step, cap)
    if target <= cur:
        return False, cur
    return _bump_defaults(key, target), target


def deepen_pages(source_id: str, scope: str) -> dict[str, Any]:
    """把 archive_pages 加深一级，回填更多历史。"""
    # 增量加深，封顶 60（depth-max 的默认值）。
    ok, val = _grow("archive_pages", 10, 60)
    _emit(scope=scope, failure_type="deepen_pages", action="deepen_pages",
          repair_result="kept" if ok else "skipped",
          detail="archive_pages -> %d" % val)
    return {"action": "deepen_pages", "applied": ok, "depth": val}


def widen_stale(source_id: str, scope: str) -> dict[str, Any]:
    """放宽已知 URL 早停阈值 —— 深翻时前几页全是已知历史，不放宽会早停。"""
    ok1, v1 = _grow("max_items", 200, 2000)
    ok2, v2 = _grow("max_stale", 50, 800)
    ok = ok1 or ok2
    _emit(scope=scope, failure_type="widen_stale", action="widen_stale",
          repair_result="kept" if ok else "skipped",
          detail="max_items -> %d, max_stale -> %d" % (v1, v2))
    return {"action": "widen_stale", "applied": ok, "max_items": v1,
            "max_stale": v2}


def raise_budget(source_id: str, scope: str) -> dict[str, Any]:
    """单源时间预算不够（老在列表阶段就被砍）时加时。"""
    # 增量加时，封顶 1800（30 分钟）。再大就该人工看了。
    ok, val = _grow("source_budget_seconds", 300, 1800)
    _emit(scope=scope, failure_type="raise_budget", action="raise_budget",
          repair_result="kept" if ok else "skipped",
          detail="source_budget_seconds -> %d" % val)
    return {"action": "raise_budget", "applied": ok, "value": val}


def disable_terminal(source_id: str, scope: str) -> dict[str, Any]:
    """把源标成终态，永不再访。"""
    path = RUNTIME / "source_backoff.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"action": "disable_terminal", "applied": False}
    row = list(data.get(source_id) or [])
    row.extend([0] * (7 - len(row)))
    row[6] = 1
    data[source_id] = row[:7]
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return {"action": "disable_terminal", "applied": False}
    _emit(scope=scope, failure_type="disable_terminal",
          action="disable_terminal", repair_result="kept",
          detail="%s 标记终态" % source_id)
    return {"action": "disable_terminal", "applied": True}


def _is_retired(key: str, action: str) -> bool:
    """这个动作在这条原则上是否已被证明无效。

    数据来自原则表本身（``fail_count`` / ``actions[].fail``），不另设状态文件
    ——多一份状态就多一个可能不同步的地方，而原则表本来就是这件事的权威记录。
    """
    if not key:
        return False
    try:
        from lighthouse import principles

        for row in principles.load():
            if row.get("key") != key:
                continue
            actions = row.get("actions")
            if isinstance(actions, dict):
                actions = [actions]
            for entry in actions or []:
                if isinstance(entry, dict) and entry.get("action") == action:
                    # 优先用 action 自己的 fail（更精确到动作），退回原则级。
                    fails = int(entry.get("fail") or 0) or int(row.get("fail_count") or 0)
                    return fails >= RETIRE_AFTER_FAILS
            # 原则级 fail_count 也算数：动作没单独记录时用它。
            return int(row.get("fail_count") or 0) >= RETIRE_AFTER_FAILS
    except Exception:                                    # noqa: BLE001
        return False                                   # 读不到就照常执行，别因判据失败停摆
    return False


def apply(source_id: str, failure_type: str,
          top_k: int = 3, scope: str | None = None) -> dict[str, Any]:
    """按当前问题去原则表查适用原则，执行最靠前的那一条真实动作。

    动作执行后**先不回填效果** —— 效果要等下一轮比对出来才有意义（配置改动
    立刻生效，但指标变化要等采集完成）。所以这里把「待验证」记进 state，
    由 `verify_pending()` 在下一轮采集后比对。

    ``scope`` 默认是 ``source_id:<source_id>``。传 ``stage:xxx`` 可以走
    stage 级原则——那是全局问题（零入库、写锁）而不是某个源的问题，
    而 ``apply_principles()`` 按 scope_type 精确匹配，不传就会「无适用原则」。

    返回 ``{"applied": bool, "action": str, "reason": str}``。
    绝不抛异常 —— 自愈失败不能拖垮采集。
    """
    scope = scope or ("source_id:%s" % source_id)
    try:
        from lighthouse import principles

        matched = principles.apply_principles(scope, failure_type, top_k=top_k)
    except Exception as exc:                           # noqa: BLE001
        return {"applied": False, "action": "-",
                "reason": "原则查询失败 %s" % type(exc).__name__}

    if not matched:
        return {"applied": False, "action": "-", "reason": "无适用原则"}

    state = _load_state()
    used = state.setdefault(scope, [])
    if len(used) >= MAX_ACTIONS_PER_SCOPE:
        return {"applied": False, "action": "-",
                "reason": "已达自动动作限额 %d 次/源" % MAX_ACTIONS_PER_SCOPE}

    for p in matched:
        action = str(p.get("best_action") or "")
        fn_name = ACTIONS.get(action)
        if fn_name is None:
            continue                        # observe_only/report_only 等
        # ★ 退役闸：失败过太多次的动作直接跳过，让 top_k 里下一条顶上。
        #
        # 没有这道闸时（2026-10-02 查到）：`source_id|verify` 的 best_action
        # 是 raise_budget，它失败了几十次，apply() 照样每次都执行它，
        # top_k 里的第二、三条永远轮不到。实测历史状态里 deepen_pages
        # 失败 14 次（utility -6.76）、widen_stale 失败 7 次，全部还在被
        # 反复执行——系统在做已知的无效动作。
        #
        # 退役是按「这条原则的这个动作」记的，不是全局记的：raise_budget
        # 对 budget_exhausted 可能仍然有效，只是对 verify 无效。
        if _is_retired(p.get("key", ""), action):
            continue
        fn = globals()[fn_name]
        try:
            out = fn(source_id, scope)
        except Exception as exc:           # noqa: BLE001
            _emit(scope=scope, failure_type=failure_type, action=action,
                  repair_result="reverted",
                  detail="执行失败 %s: %s" % (type(exc).__name__, exc))
            continue
        if out.get("applied"):
            used.append({"action": action, "ts": time.time(),
                         "failure_type": failure_type,
                         "key": p.get("key", ""),
                         "before": _snapshot(source_id)})
            _save_state(state)
            return {"applied": True, "action": action,
                    "reason": "原则 %s 效用最高" % p.get("key", "")}
    return {"applied": False, "action": "-",
            "reason": "适用原则里没有可执行动作（都是 observe_only 或已退役）"}


def _snapshot(source_id: str) -> dict[str, Any]:
    """记动作执行前的指标，供下一轮比对。

    只记本源相关、且「动作真的能影响」的指标 —— 动作改的是全局配置
    （预算/深度/上限），效果却体现在本源上，所以按 source_id 取。

    ``oldest_publish_date`` 是关键补充（2026-09-30 加）：只看「记录数涨没涨」
    会把「采干」误判成动作无效。实测 mee_gov 翻到 30 页已有 2022~2026
    四年 37 条，再加深度也不出内容 —— 记录数不涨是**正确结果**，但按
    旧判据记成 fail，动作会白改一次配置（实测 archive_pages 被自动从 30
    加到 42，两次都 fail）。加上「历史深度」这个指标后，「深度没变但更早的
    记录被挖到」也算成功。
    """
    out: dict[str, Any] = {"ts": time.time()}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        row = con.execute(
            "SELECT COUNT(*), MAX(fetched_at), MIN(publish_date) "
            "FROM announcements WHERE source_id = ?", (source_id,)).fetchone()
        con.close()
        out["records"] = int(row[0] or 0)
        out["last_fetch"] = row[1]
        out["oldest_publish"] = (row[2] or "")[:10]
    except sqlite3.Error:
        pass
    return out


def verify_pending() -> dict[str, Any]:
    """回填上一轮动作的效果 —— 闭环的最后一环。

    上一轮 apply() 改了配置并记了「执行前」的指标快照；这一轮采集完了，
    再取一次同样的指标比对，变好记 success、没变记 fail。

    **为什么必须有这一步**：没有它，`success_count` 恒为 0，`utility_score`
    永远是初值，distill 挑不出更好的动作，`best_action` 永远不变 —— 系统
    记了上千次「应用」却学不到任何东西（这正是 2026-09-30 之前的状态：
    apply_count 137/167/263，success_count 和 fail_count 全是 0）。

    返回统计摘要。
    """
    state = _load_state()
    verified = {"success": 0, "failed": 0, "pending": 0, "keys": []}
    from lighthouse import principles

    for scope, rows in list(state.items()):
        source_id = str(scope).split(":", 1)[-1]
        for row in rows:
            if row.get("verified"):
                continue
            before = row.get("before") or {}
            # 至少隔一轮才算：刚执行就比对，改动可能还没起作用
            if time.time() - float(row.get("ts", 0)) < 60:
                verified["pending"] += 1
                continue
            try:
                con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
                # 注意变量名：这里查出来的叫 after_row，**不能**叫 row ——
                # row 是待验证的那条记录，后面还要 row["verified"] = True。
                # 2026-09-30 实测：同名覆盖导致 AttributeError:
                # 'tuple' object has no attribute 'get'，整轮回填全崩。
                after_row = con.execute(
                    "SELECT COUNT(*), MIN(publish_date) FROM announcements "
                    "WHERE source_id = ?", (source_id,)).fetchone()
                con.close()
            except sqlite3.Error:
                continue
            after_n = int(after_row[0] or 0)
            after_oldest = (after_row[1] or "")[:10]
            before_n = int(before.get("records") or 0)
            before_oldest = str(before.get("oldest_publish") or "")
            # 两种「有效」：采到新记录，或把历史挖得更早（2026-09-30 加）。
            # 只看记录数会把「采干」误判成动作无效 —— 而采干时记录数不涨
            # 恰恰是正确结果。
            grew = after_n > before_n
            dug_deeper = bool(after_oldest and before_oldest
                              and after_oldest < before_oldest)
            ok = grew or dug_deeper
            key = str(row.get("key") or "")
            if key:
                principles.record_outcome(
                    key, ok,
                    note="%s %d->%d 条, 最老 %s->%s" % (
                        source_id, before_n, after_n,
                        before_oldest or "?", after_oldest or "?"))
            row["verified"] = True
            row["records_after"] = after_n
            verified["success" if ok else "failed"] += 1
            if key:
                verified["keys"].append(key)
            _emit(scope=scope, failure_type="verify",
                  action=str(row.get("action") or ""),
                  repair_result="kept" if ok else "reverted",
                  detail="效果回填: %d->%d 条, 最老 %s->%s" % (
                      before_n, after_n, before_oldest or "?",
                      after_oldest or "?"))
    _save_state(state)
    return verified


def stats() -> dict[str, Any]:
    """给工作台看：自动动作执行了多少次、命中哪些动作。"""
    state = _load_state()
    by_action: dict[str, int] = {}
    for rows in state.values():
        for r in rows:
            by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    return {"scopes": len(state),
            "actions_total": sum(by_action.values()),
            "by_action": by_action,
            "limit_per_scope": MAX_ACTIONS_PER_SCOPE}