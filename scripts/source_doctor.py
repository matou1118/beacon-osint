"""源健康诊断：逐个源回答「现在能采吗 / 为什么不能 / 怎么修」。

借鉴 Agent Reach（github.com/Panniantong/Agent-Reach）的 ``doctor.py`` 与
``probe.py``，只借方法不借代码。

借的是什么
----------
1. **「每个 channel 知道怎么检查自己」**：判定规则按源的类型分派，不是
   一个大if-else 糊在一起。加一类源就加一个 checker，不用改主流程。
2. **「看起来一样的失败要拆开」**（``probe.py`` 的 missing / broken /
   timeout / ok 四分法）。灯塔原来的 ``unknown`` 桶就是没拆的后果：
   「all29 list urls already known」和「early stop after 200 known urls」
   是同一件事，却因为句子不同，一边判成 ``no_new`` 一边掉进 ``unknown``。
3. **每个结论都带 hint（怎么修）**：光说「坏了」等于没说。
4. **单个源诊断失败绝不能带倒整份报告**：145 个源里一个抽风，其余 144 个
   的结论照样要给出来。

不借的是什么
------------
* 不发网络请求。``probe_sources.py`` 会真去抓（那是它的活），本脚本只读
  已有数据：源登记 + 退避状态 + 历史采集结果。好处是可以在采集运行时安全
  执行，不抢锁、不打扰采集。
* 不判「能不能采」靠猜，靠**三处已有事实的交叉**：登记是否完整、退避是否
  到期、最近一次采集实际发生了什么。

输出分级（照抄 Agent Reach 的分层，它按「装好即用 / 需要 key / 可选」分）
--------------------------------------------------------------------------
* ``tier 0`` 配置齐全、开着、到期可采 —— 不用管
* ``tier 1`` 开着但到期没到 / 采集过但零产出 —— 正常节流，等回访
* ``tier 2`` 开着但**配置缺件**（没 selector、没 detail_pattern）—— 要补
* ``tier 3`` 诊断本身出错了 —— 要人看（这类必须显眼，不能被别的结论盖住）
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "beacon.db"
SOURCES_YAML = _ROOT / "config" / "sources.yaml"
RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"
REPORT = RUNTIME / "source_doctor.json"

OK, WARN, BAD, ERROR = "ok", "warn", "bad", "error"


@dataclass
class Diagnosis:
    """一个源的诊断结论。

    ``status``  用四个值而不是两个，是因为「坏了」和「配不全」要做的
    事不一样：前者要修选择器，后者要补配置。
    """
    source_id: str
    status: str = OK
    tier: int = 0
    reason: str = ""
    hint: str = ""                    # 怎么修 —— 没 hint 的结论等于没说
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ 数据源


def load_sources() -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8")) or {}
    return list(data.get("sources") or [])


def load_backoff() -> dict[str, Any]:
    """退避状态。直接读 JSON —— 只是状态，不含判据。

    判据全在 ``_due_in`` 里，而那里**复用** ``SourceBackoff.backoff_interval``
    而不是自己抄一遍。曾经这里抄过一版 ``sleep_for * floor``，后来 run.py
    改成绝对阶梯，两边就再也对不上了。
    """
    path = RUNTIME / "source_backoff.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def load_last_results(rounds: int = 8) -> dict[str, dict[str, Any]]:
    """最近若干轮里，每个源最后一次采集的判定。

    用 ``lighthouse.run._source_classify`` —— 就地复用，不要在诊断脚本里
    再抄一份分类判据。抄一份的后果是两边判据漂移，然后「诊断说没问题、
    采集说有」这种矛盾出现，还很难定位。
    """
    from lighthouse.run import _source_classify

    files = sorted(RUNTIME.glob("shadow-*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:rounds]
    out: dict[str, dict[str, Any]] = {}
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in payload.get("sources") or []:
            sid = str(entry.get("source_id") or "")
            if not sid or sid in out:            # 已有更新的就不覆盖
                continue
            out[sid] = {
                "status": _source_classify(entry),
                "notes": entry.get("notes") or [],
                "records": int(entry.get("records") or 0),
                "dropped": int(entry.get("dropped") or 0),
                "seconds": float(entry.get("seconds") or 0),
                "at": path.name.replace("shadow-", "").replace(".json", ""),
            }
    return out


def load_yield_stats() -> dict[str, dict[str, Any]]:
    """库里各源的历史产出：最后一次入库时间、总条数、最近 30 天条数。"""
    if not DB.exists():
        return {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT source_id, COUNT(*), MAX(created_at), "
            "       SUM(CASE WHEN created_at >= date('now','-30 day') "
            "           THEN 1 ELSE 0 END) "
            "FROM announcements GROUP BY source_id").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {r[0]: {"total": r[1], "last_ingest": r[2], "recent_30d": r[3]}
            for r in rows}


# ------------------------------------------------------------------ 判据


def _due_in(row: Any, tier: str, now: float) -> float:
    """这个源还要等多少秒才能采。< 0 表示已到期。

    直接调 ``SourceBackoff.backoff_interval()``，不在这里抄一遍判据。

    抄一遍的后果很具体：第一版在这里写了 ``sleep_for * floor``，而
    ``run.py`` 那边后来改成了绝对阶梯（``BACKOFF_LADDER``）。于是 doctor 说
    「还要等 19.9 小时」而调度器用另一套数算等待时长，两边对不上——那种
    矛盾极难定位，因为它只在真出问题时才显形。

    所以凡是「判据只有一份」的场合，一律复用，不复述。
    """
    import importlib.util

    from lighthouse.run import SourceBackoff

    if not isinstance(row, (list, tuple)) or len(row) < 3:
        return 0.0                       # 没记录过 = 现在就能采
    last_epoch = row[2]
    if not last_epoch:
        return 0.0
    interval = SourceBackoff.backoff_interval(int(row[0]), tier)
    return max(0.0, float(last_epoch) + interval - now)


def diagnose(source: dict[str, Any], backoff: dict[str, Any],
             last: dict[str, dict[str, Any]],
             yields: dict[str, dict[str, Any]],
             now: float) -> Diagnosis:
    """诊断单个源。

    **纯函数**：只读入参，不碰文件、不发请求。这样它既能用于批量诊断，
    也能被测试逐条调用。
    """
    sid = str(source.get("id") or "")
    diag = Diagnosis(source_id=sid)
    enabled = bool(source.get("enabled"))

    # ── 未启用：不是故障，但要说明为什么没在采 ──────────────────
    if not enabled:
        diag.status = OK
        diag.tier = 0
        diag.reason = "未启用（status=%s）" % (source.get("status") or "-")
        diag.evidence["enabled"] = False
        diag.evidence["collectable"] = False
        return diag

    # selector 字段只记下来供人参考，**不定级**。
    #
    # 第一版在这里判「既没 selector 也没 detail_pattern → 要修」，结果 11 个
    # 源全被抓出来——而它们实际采到了 26~484 条（cq_ny 327 条）。原因是 34 个
    # enabled 源里有 11 个本来就没有 selector 字段：采集走的是「从列表页直接
    # 提链接」的路子。把常态当成了病。
    #
    # tier2 的判据必须是**实测**，见下面 in_db == 0 那条。
    has_list = bool(source.get("list_selector") or source.get("link_selector"))
    has_detail = bool(source.get("detail_pattern"))

    # ── 到期没到：这是正常节流，不是故障 ─────────────────────
    wait = _due_in(backoff.get(sid), str(source.get("cadence_tier") or "daily"), now)
    if wait > 0:
        hours = wait / 3600
        diag.status = WARN
        diag.tier = 1
        diag.evidence["enabled"] = True
        diag.reason = "回访间隔未到，还要等 %.1f 小时" % hours
        diag.hint = ("不用管。到点后 watchdog 会自动拉起。"
                     "退避不是故障：源刚访问过，或距上次发布还早。")
        diag.evidence = {"wait_hours": round(hours, 2),
                         "collectable": False}
        return diag

    # ── 能采了：看历史上到底采到了什么 ─────────────────────
    info = last.get(sid)
    stats = yields.get(sid) or {}
    common = {"in_db": int(stats.get("total") or 0),
              "recent_30d": int(stats.get("recent_30d") or 0)}

    # 配置「看起来不全」不是故障。
    #
    # 第一版把「既没 list_selector 也没 detail_pattern」判成 tier2 要修，
    # 结果 11 个源全被抓出来——而它们实际采到了 26~484 条（cq_ny 327 条）。
    # 原因：34 个 enabled 源里有 11 个本来就没有 selector 字段，采集走的是
    # 「从列表页直接提链接」的路子。**把常态当成了病**。
    #
    # 所以 tier2 的判据必须是**实测**：「开了、到期了、历史上一条都没采到」。
    # 配置字段只能进 evidence 供人参考，不能定级。
    no_selector = not has_list and not has_detail
    if no_selector and int(stats.get("total") or 0) == 0:
        diag.status = BAD
        diag.tier = 2
        diag.evidence["enabled"] = True
        diag.reason = "enabled + 已到期，但库里 0 条，且没有 selector 配置"
        diag.hint = ("跑 python scripts/probe_sources.py 探一次（会真发请求，"
                     "结果存 data/probe_results.jsonl），看是该站改版了"
                     "还是需要补 selector")
        diag.evidence = dict(common, has_selector=False,
                             in_db=int(stats.get("total") or 0))
        return diag

    if info is None:
        # 从没被采过：可能是刚加的源，也可能是排不上队。
        diag.status = WARN
        diag.tier = 1
        diag.evidence["enabled"] = True
        diag.reason = "已到期，但最近 %d 轮里没有它的采集记录" % 8
        diag.hint = ("还没轮上（源多轮时会被 deadline 砍掉），或刚加进登记。"
                     "跑 python lighthouse/run.py --source %s --limit 20 单跑一次看结果"
                     % sid)
        diag.evidence = {"in_db": int(stats.get("total") or 0)}
        return diag

    status = str(info.get("status") or "unknown")
    notes = " | ".join(info.get("notes") or [])
    common = {"last_status": status, "notes": notes[:160],
              "records": info.get("records"),
              "in_db": int(stats.get("total") or 0),
              "recent_30d": int(stats.get("recent_30d") or 0)}

    if status == "ok":
        diag.status = OK
        diag.tier = 0
        diag.reason = "正常，最近一轮采到 %s 条" % info.get("records")
        diag.evidence = common
        diag.evidence["enabled"] = True
        diag.evidence["collectable"] = True
        return diag

    if status == "no_new":
        # 关键区分：no_new 是**健康**的。用 zero-yield 把它和真故障分开，
        # 否则「所有源都在 no_new」看起来像坏了，其实只是都没更新。
        stale_days = None
        if stats.get("last_ingest"):
            try:
                when = datetime.fromisoformat(
                    str(stats["last_ingest"]).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                stale_days = round(
                    (datetime.now(timezone.utc) - when).total_seconds() / 86400, 1)
            except ValueError:
                stale_days = None
        if stale_days is not None and stale_days > 30:
            diag.status = WARN
            diag.tier = 1
            diag.reason = "列表里全是已知 URL，%s 天没有新记录" % stale_days
            diag.hint = ("源可能改版了或改用新栏目。用 "
                         "python scripts/probe_sources.py 探一次，"
                         "或看该站首页是否有新栏目需要加源。")
            diag.evidence["collectable"] = True
            diag.evidence = dict(common, stale_days=stale_days)
            return diag
        diag.status = OK
        diag.tier = 0
        diag.reason = "列表里全是已知 URL（正常增量空转%s）" % (
            "，%s 天没更新" % stale_days if stale_days is not None else "")
        diag.evidence = dict(common, stale_days=stale_days,
                             enabled=True, collectable=True)
        return diag

    if status == "no_links":
        diag.status = BAD
        diag.tier = 2
        diag.reason = "列表页取不到链接：选择器或 URL 失效"
        diag.hint = ("跑 python lighthouse/repair.py --source %s --apply "
                     "从真实列表页反推选择器；反推不成功说明站点结构变了，"
                     "要人工看该站首页" % sid)
        diag.evidence["collectable"] = False
        diag.evidence = common
        return diag

    if status == "broken_page":
        diag.status = BAD
        diag.tier = 2
        diag.reason = "翻页地址或渲染链路坏了（404 / 渲染失败）"
        diag.hint = ("archive_pages 或翻页 URL 失效。核对 base_url 的分页参数，"
                     "或把该源 archive_pages 调小到能覆盖的范围")
        diag.evidence["collectable"] = False
        diag.evidence = common
        return diag

    if status == "all_dropped":
        diag.status = BAD
        diag.tier = 2
        diag.reason = "链接取到了但正文全被质量门禁拦下（%s 条）" % info.get("dropped")
        diag.hint = ("门禁太严或正文抽不出来。用 "
                     "python lighthouse/bench.py 看门禁对这个源判成了什么")
        diag.evidence["collectable"] = False
        diag.evidence = common
        return diag

    if status == "skipped":
        diag.status = WARN
        diag.tier = 1
        diag.reason = "上一轮到时限没轮到它"
        diag.hint = "不是源的毛病。源多时按 deadline 砍，可调 max-minutes"
        diag.evidence["collectable"] = False
        diag.evidence = common
        return diag

    # unknown：_source_classify 认不出来的说明。真出现时这里是最后一道
    # 防线，标 ERROR 让人看——绝不能默默归进「正常」。
    diag.status = ERROR
    diag.tier = 3
    diag.reason = "无法分类：%s" % (notes[:120] or "(无 notes)")
    diag.hint = ("分类器没认出这个 notes。需要往 "
                 "lighthouse/run.py:_source_classify 加一条判据——"
                 "注意按语义归，别只认某一句话的写法")
    diag.evidence["collectable"] = False
    diag.evidence = common
    return diag


# ------------------------------------------------------------------ 批量


def check_all() -> dict[str, Any]:
    """诊断所有源。

    **单个源诊断失败绝不能带倒整份报告**（Agent Reach doctor.py 的核心）。
    这里用一个源一个 try：145 个源里哪怕 140 个抽风，另外 5 个的结论
    也必须给出来。整份报告因为一个源崩掉，等于零信息。
    """
    sources = load_sources()
    backoff = load_backoff()
    last = load_last_results()
    yields = load_yield_stats()
    now = datetime.now(timezone.utc).timestamp()

    results: list[Diagnosis] = []
    for source in sources:
        sid = str(source.get("id") or "?")
        try:
            results.append(diagnose(source, backoff, last, yields, now))
        except Exception as exc:                            # noqa: BLE001
            results.append(Diagnosis(
                source_id=sid, status=ERROR, tier=3,
                reason="诊断器自身异常 %s: %s" % (type(exc).__name__, exc),
                hint="这是 doctor 的 bug，不是源的故障。要修 doctor。"))

    by_status = collections.Counter(r.status for r in results)
    by_tier = collections.Counter(r.tier for r in results)

    # ★ 集体判定：源逐个看都是「正常节流」，合起来却可能是停摆。
    #
    # 2026-10-01 整夜就是这么丢的：34 个源逐个都「回访间隔未到」，tier2=0、
    # tier3=0，全绿——但它们**全都**在等，于是没有任何源能采，一夜零入库，
    # 而每一项单独看都正常。逐源诊断天生看不见这种病。
    #
    # 所以这里必须补一句集体的判断：可采源一个都没有 = 停摆。
    # 这条判据不能放在 diagnose() 里——单源视角下它根本不存在。
    enabled = [r for r in results if r.evidence.get("enabled", True)]
    collectable = [r for r in results if r.evidence.get("collectable") is True]
    collective: dict[str, Any] = {
        "enabled": len(enabled),
        "collectable_now": len(collectable),
    }
    if enabled and not collectable:
        waits = []
        for row in results:
            hours = row.evidence.get("wait_hours")
            if isinstance(hours, (int, float)):
                waits.append(hours)
        soonest = min(waits) if waits else None
        collective["verdict"] = "全体停摆：可采源为 0"
        collective["soonest_hours"] = soonest
        collective["hint"] = (
            "所有 enabled 源都在退避/回访间隔里。查两件事："
            "(1) SourceBackoff.BACKOFF_LADDER —— 零产出源要等多久才回访，"
            "按连续零产出轮数查这张绝对秒数的表（它不再和 cadence 下限相乘，"
            "所以改 floor 不会顺带放大退避）；"
            "(2) 库最后一次写入距今多久——%s"
            % ("%.1f 小时内会解冻" % soonest if soonest is not None
               else "拿不到解冻时间"))

    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(results),
        "by_status": dict(by_status),
        "by_tier": dict(by_tier),
        "collective": collective,
        "results": [r.to_dict() for r in results],
    }


TIER_LABEL = {0: "正常，不用管", 1: "正常节流/待观察", 2: "要修", 3: "诊断器出错"}


def render(report: dict[str, Any]) -> str:
    lines = ["灯塔源健康诊断", "=" * 60]
    by = report["by_status"]
    lines.append("共 %d 个源：  正常 %s | 观察 %s | 要修 %s | 报错 %s" % (
        report["total"], by.get(OK, 0), by.get(WARN, 0),
        by.get(BAD, 0), by.get(ERROR, 0)))

    # 集体判定放最上面：它比任何单源结论都重要。2026-10-01 整夜所有单源
    # 都是绿的（逐个看都「回访间隔未到」），合起来却是一夜零入库。
    col = report.get("collective") or {}
    if col.get("verdict"):
        lines.append("")
        lines.append("!! %s" % col["verdict"])
        lines.append("   enabled %d 个，现在能采的 %d 个" % (
            col.get("enabled", 0), col.get("collectable_now", 0)))
        if col.get("soonest_hours") is not None:
            lines.append("   最近的源还有 %.1f 小时解冻" % col["soonest_hours"])
        lines.append("   %s" % col.get("hint", ""))

    for tier in (3, 2, 1, 0):
        group = [r for r in report["results"] if r["tier"] == tier]
        if not group:
            continue
        lines.append("")
        lines.append("【tier %d】%s —— %d 个" % (tier, TIER_LABEL.get(tier, ""), len(group)))
        for row in group:
            mark = {"ok": " ", "warn": "!", "bad": "x", "error": "E"}.get(
                row["status"], "?")
            lines.append("  [%s] %-20s %s" % (mark, row["source_id"][:20], row["reason"]))
            if row["hint"] and tier >= 2:
                lines.append("        怎么修: %s" % row["hint"])

    bad = [r for r in report["results"] if r["tier"] == 2]
    if bad:
        lines.append("")
        lines.append("要修 %d 个源。按「怎么修」逐个处理；" % len(bad))
        lines.append("要一次性探活用：python scripts/probe_sources.py")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="源健康诊断")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--write", action="store_true",
                        help="写到 data/runtime/lighthouse/source_doctor.json")
    parser.add_argument("--source", default="", help="只看一个源")
    args = parser.parse_args(argv)

    report = check_all()
    if args.source:
        report["results"] = [r for r in report["results"]
                             if r["source_id"] == args.source]
        report["total"] = len(report["results"])

    if args.write:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
          else render(report))
    return 1 if report["by_status"].get(ERROR) else 0


if __name__ == "__main__":
    raise SystemExit(main())
