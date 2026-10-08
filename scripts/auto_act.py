"""自主动作执行器：把「检测到的问题」变成「系统自己改配置」。

为什么需要这个文件
------------------
``status_summary._alarms()`` 已经能喊出静默停摆（2026-10-01 整夜零入库
那一课），``principle_applier`` 也有可执行动作（``loosen_backoff`` /
``retry_write``），但两者之间**没有连线**——告警只是喊，没有人执行。

所以「自我迭代」实际是断的：检测到了 → 记进报告 → 下次我人肉看 → 手工改。
这不是自主，是提醒。

这个模块做的事
--------------
每轮自迭代时把**机器判据**翻译成 ``failure_type``，交给
``principle_applier.apply()``。动作由原则表决定（``seed_principles.py`` 种的
那三条），改配置也由动作自己完成。判据只回答「是哪个 failure_type」，
具体做什么动作仍然查原则表——**判据和动作分开，以后调动作不用改这里**。

安全
----
* 一次最多执行一个动作（``MAX_ACTIONS_PER_SCOPE`` 之外还有一道总闸）
* 每次都把「为什么执行」写进事件流，可追溯
* 连续失败的动作会被 ``principle_applier`` 退役，不会反复做
* ``--dry-run`` 只报不动
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"

# 一次自迭代最多执行几个动作。多于一个就该分多轮做，而不是一口气改一堆配置。
MAX_ACTIONS_PER_ROUND = 1

# 库多久没增长算「静默停摆」。6 小时：正常一轮几十分钟到两小时。
SILENT_HOURS = 6


def _hours_since(stamp: Any) -> float | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def detect(summary: dict[str, Any]) -> list[tuple[str, str]]:
    """把运行状态翻译成 (failure_type, 为什么)。

    **只回答「是哪个问题」，不决定做什么动作** —— 动作查原则表。

    「可采源为 0」这一条**从 ``source_doctor`` 取**，不在这里重算。
    两处各算一套的后果是判据漂移，然后出现「doctor 说停摆、auto_act 说正常」
    这种互相打架的结论——2026-10-01 整夜就是这么丢的，而那晚两个模块
    都在跑。doctor 那边还多一样东西：解冻要等多久。
    """
    found: list[tuple[str, str]] = []
    db = summary.get("db") or {}

    idle = _hours_since(db.get("last_ingest"))
    if idle is not None and idle >= SILENT_HOURS:
        found.append(("no_production",
                      "库已 %.1f 小时没有新记录" % idle))

    verdict = _collective_verdict()
    if verdict:
        found.append(("all_gated", verdict))

    for failure_type, why in _backoff_shape_checks():
        found.append((failure_type, why))

    return found


def _backoff_shape_checks(backoff_obj=None) -> list[tuple[str, str]]:
    """退避表**形状**异常——不看内容也能判的两类故障。

    这两条判据的共同点：单看任一源都「正常」，只有把整张表摆在一起看才
    暴露问题。2026-10-02 那次「145 个源全部在回访间隔」就是这样溜过去的：
    逐源检查全是健康的，只有「条目数 == 配置源数」且「形状完全一致」
    才说明这些条目是同一次批量写入，不是各源独立演化出来的。
    """
    out: list[tuple[str, str]] = []
    try:
        import yaml
        from lighthouse.run import SourceBackoff

        backoff = backoff_obj if backoff_obj is not None else SourceBackoff.load()
        cfg_path = _ROOT / "config" / "sources.yaml"
        enabled = [s for s in (yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                               .get("sources") or []) if s.get("enabled")]
        # ① 全部源都进了退避表，且形状一模一样 = 批量误标
        if enabled and len(backoff.data) >= len(enabled):
            shapes = set()
            for row in backoff.data.values():
                padded = list(row) + [0] * (SourceBackoff.FIELDS - len(row))
                shapes.add(tuple(padded[:SourceBackoff.FIELDS]))
            if len(shapes) == 1:
                out.append(("skipped_marked_visited",
                            "退避表 %d 条与 enabled 源数 %d 相同，且所有条目形状"
                            "完全一致——同一次批量写入，通常是「没真抓却被记成"
                            "已访问」" % (len(backoff.data), len(enabled))))
    except Exception as exc:                                  # noqa: BLE001
        return out

    # ② 翻页深度对所有源算出来都一样 = 下限把分档抬平了
    try:
        import yaml
        from lighthouse.run import with_defaults

        cfg = yaml.safe_load(
            (_ROOT / "lighthouse" / "config" / "web_collector_config.yaml")
            .read_text(encoding="utf-8"))
        pages = {
            int(with_defaults(s, cfg, 3).get("archive_pages") or 0)
            for s in enabled
        }
        if len(pages) == 1:
            floor = (cfg.get("collect_defaults") or {}).get(
                "raise_archive_pages_below")
            out.append(("archive_depth_uniform",
                        "全部 %d 个源算出的 archive_pages 都是 %s%s——"
                        "按产出分档（深翻页/快节奏）已经失效"
                        % (len(enabled), pages.pop(),
                           "，正好等于 raise_archive_pages_below=%s" % floor if floor else "")))
    except Exception:                                         # noqa: BLE001
        pass
    return out


def _collective_verdict() -> str:
    """问 source_doctor「是不是全体停摆」。返回一句人话，没有就返回空串。"""
    try:
        from scripts.source_doctor import check_all

        report = check_all()
    except Exception as exc:                                  # noqa: BLE001
        return ""          # 诊断器自己坏了不该触发自主动作——那会去改配置
    collective = report.get("collective") or {}
    if not collective.get("verdict"):
        return ""
    out = "%s（enabled %s 个，可采 %s 个）" % (
        collective.get("verdict"), collective.get("enabled"),
        collective.get("collectable_now"))
    soonest = collective.get("soonest_hours")
    if isinstance(soonest, (int, float)):
        out += "；最近的源 %.1f 小时后解冻" % soonest
    return out


def act(dry_run: bool = False) -> dict[str, Any]:
    """跑一轮：检测 → 查原则 → 执行。返回执行记录。"""
    from lighthouse import principle_applier

    spec_path = _ROOT / "scripts" / "status_summary.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ss_act", spec_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = module.collect()

    found = detect(summary)
    results: list[dict[str, Any]] = []
    if not found:
        return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "detected": [], "actions": [],
                "note": "未检测到需要自主动作的问题"}

    for failure_type, why in found:
        # 这些是**全局**问题（零入库、全被闸挡住），不是某个源的问题，
        # 所以 scope 必须走 stage 级。``apply_principles()`` 按 scope_type
        # 精确匹配：传 ``source_id:_stage`` 会「无适用原则」——
        # 踩过，stage 级原则压根没被查到。
        stage_scope = "stage:%s" % failure_type
        if dry_run:
            results.append({"failure_type": failure_type, "why": why,
                            "scope": stage_scope, "dry_run": True})
            continue
        out = principle_applier.apply("_stage", failure_type, scope=stage_scope)
        results.append({"failure_type": failure_type, "why": why,
                        "scope": stage_scope, **out})
        if out.get("applied"):
            # 执行成功就停手：一轮一个动作，剩下的下轮再说。
            break

    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "detected": [{"failure_type": f, "why": w} for f, w in found],
            "actions": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自主动作执行器")
    parser.add_argument("--dry-run", action="store_true", help="只报不动")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    out = act(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if not out["detected"]:
            print("未检测到需要自主动作的问题。")
        for item in out["detected"]:
            print("检测: %-16s %s" % (item["failure_type"], item["why"]))
        for act_ in out["actions"]:
            if act_.get("dry_run"):
                print("  (dry-run) 未执行")
                continue
            print("  动作: applied=%s action=%s %s"
                  % (act_.get("applied"), act_.get("action"),
                     act_.get("reason", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())