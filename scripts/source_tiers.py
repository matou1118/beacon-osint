"""采集源等级制度：按**实测证据**把源分档，每档用不同的采集方法。

为什么要有等级
--------------
143 个源用同一种方式采，实测暴露三个完全不同的问题：

* 有些源静态就够（``static_html`` 5828 条），走渲染纯属浪费时间和带宽；
* 有些源静态**永远拿不到**（HTTP 412/WAF，实测 sc_gs_gov / sc_jx_gov / sc_xz_gov），
  每轮都先试静态再降级渲染，白白多花一次请求和一份超时预算；
* 有些源**根本采不出东西**（10-04 实测 143 个源里 70 个连续零产出 4 次），
  和健康源用同样的 8 小时下限去回访，是把预算花在死源上。

所以等级不是「给源贴标签」，而是**回答「这个源下一步该用什么方法试」**。

等级定义（全部由实测信号推出，不含拍脑袋的阈值）
--------------------------------------------------
T0 主力   稳定产出，且近期有新内容        -> 标准静态采集，8 小时回访
T1 低产   能采到但产出稀薄/不新          -> 标准静态采集，**降频**（16 小时）
T2 需渲染 静态拿不到（412/WAF/JS 渲染）  -> **直接走渲染**，跳过静态尝试
T3 链接形态可疑 采到了但正文是导航/栏目页 -> 低频试探 + 标记待修链接形态
T4 长眠   连续多轮零产出               -> 降到 weekly/monthly，几乎不占预算
T5 不可采 明确失效（404/熔断/robots）  -> 熔断，只做低频探活

阈值全部集中在 ``THRESHOLDS``，每个都注明**实测依据**，改之前先看那段注释。
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "beacon.db"
EVENTS = ROOT / "data" / "runtime" / "lighthouse" / "events.jsonl"
BACKOFF = ROOT / "data" / "runtime" / "lighthouse" / "source_backoff.json"
SOURCES = ROOT / "config" / "sources.yaml"
TIER_CACHE = ROOT / "data" / "runtime" / "lighthouse" / "source_tiers.json"

# ── 阈值（每条都带实测依据）────────────────────────────────────────
THRESHOLDS: dict[str, Any] = {
    # T0/T1 分界：90 天内有过新内容就算「还在活」
    # 依据：政府源发文周期以月计，90 天没更新基本等于停更；
    # 库内 meomoe_gov / mohrss_gov 实测就是这种情况（有历史、无当期）。
    "fresh_days": 90,

    # ★ 降频/判死用「距上次产出的天数」，**不用连续零产出轮数**。
    #
    # 第一版用 dry_streak，实测直接判错：143 个源里 132 个被判「长眠」。
    # 根因是 dry_streak 涨的是**访问轮次**，而 daily 档一天就访问 3 次——
    # 于是「连续 4 轮零产出」实际只是「4/3 ≈ 1.3 天没更新」，可政府源
    # 本来就按周甚至按月发文。更糟的是退避阶梯会把间隔越拉越长，
    # 轮次涨得越来越慢，所以轮数和时长之间**没有稳定关系**，
    # 拿它当时间判据必然错。
    #
    # 对照实测：同一时刻改用「距上次入库多少周」统计，126 个源是**本周内**
    # 有产出的，只有 16 个源是 1 周前。同一批数据、同一时刻，
    # 两个判据给出完全相反的结论。
    "days_since_output_low": 21,
    "days_since_output_dormant": 120,

    # dry_streak 降级为**辅助确认信号**：
    # 一个源既久没产出、又连着多轮零产出，才升到 T5；
    # 只有久没产出但最近还在出货的，不判死（避开长假/站点改版误杀）。
    "dry_streak_confirm_dormant": 4,

    # T5：明确失效的关键词（页面级，不是内容级）
    # 依据：2026-10-05 实测库里 34 条 404 页面，标题/正文含这些字样；
    # 这类页面**没有假阳性**——真公告不会写「Page Not Found」。
    "dead_markers": ("Page Not Found", "找不到网页", "找不到網頁",
                     "404 Not Found", "页面不存在", "页面走丢了"),

    # T3：正文像导航而不是文章。
    # 依据：2026-10-03 实测 sd_rst 一轮 598 条里 350 条正文是
    # 「索引 服务 日一二三四五六最新发布」，且**同一个源下多篇正文一模一样**。
    # 跨记录重复是这里唯一稳的信号——单条的「无句读」会误杀表格型公告
    # （生态环境部的环评公示正文就是表格，2111 条无句读里 8 成是真数据）。
    "dup_body_min_repeat": 3,

    # T3：重复正文占该源产出的比例
    # 依据：sd_rst 是 350/598 = 59%（极端）；sc_gs_gov 是 22/68 = 32%。
    # 取 25%：低于它算个别脏记录，高于它说明**系统性**取错了内容。
    "dup_body_ratio": 0.25,
}

# T2 的判定：静态层面就被挡住的源
# 依据：2026-10-03 实测 sc_gs_gov / sc_jx_gov / sc_xz_gov 的静态 fetch
# 返回 HTTP 412（WAF），页面只有骨架；配置里它们 playwright_fallback=True。
STATIC_BLOCK_CODES = (401, 403, 406, 412, 429, 503)

# ★ 所有等级信号**只统计干净记录**。
#
# 不加这条过滤时的实测（2026-10-05）：
#   源          全部  已标记needs_review  干净  干净中重复  被判
#   sd_rst      723   413                310   14 (4.5%)  T3
#   sc_nx_gov   401   17                 384   12 (3.1%)  T5「不可采」
#   sc_jx_gov   103   26                  77    4 (5.2%)  T3
#   mct_gov      59   34                  25    0          T3
#   tj_gov       32    8                  24    0          T5「不可采」
# sc_nx_gov 有 401 条正常记录，仅因**历史上采到过 1 条 404 页面**就被判「不可采」。
#
# 根因不是阈值，是口径：``dup_body_records`` 和 ``dead_pages`` 都是**累加量**，
# 而 needs_review 只加标记、不减计数。于是「把一个源修好了」这件事永远不会
# 反映到等级上——源会永久停在自己出问题那天的档位。11 个 T3/T5 里 5 个是假警报。
#
# 反过来，全部产出都是脏数据同样是有效信号（见 classify_source 里的 T3 分支），
# 所以另外统计 ``marked_records``，不靠「干净=0」来间接推断。
CLEAN = "coalesce(needs_review,0)=0"


@dataclass
class SourceTier:
    """一个源的等级判定结果。"""

    source_id: str
    tier: str                      # T0..T5
    reason: str                    # 人话理由，写进缓存便于事后审计
    signals: dict[str, Any] = field(default_factory=dict)
    # 建议的采集方法（由 tier 推导，不手工指定）
    routes: list[str] = field(default_factory=list)
    revisit_seconds: int = 28800   # 8 小时
    depth: int = 3
    max_items: int = 120

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "tier": self.tier,
            "reason": self.reason, "signals": self.signals,
            "routes": self.routes,
            "revisit_seconds": self.revisit_seconds,
            "depth": self.depth, "max_items": self.max_items,
        }


# 每档的采集策略。**这是等级制度的全部意义**——不是标签，是方法差异。
TIER_POLICY: dict[str, dict[str, Any]] = {
    "T0": {"routes": ["static"], "revisit_seconds": 28800,
           "depth": 6, "max_items": 200,
           "label": "主力源：稳定产出，标准静态采集"},
    "T1": {"routes": ["static"], "revisit_seconds": 57600,
           "depth": 6, "max_items": 120,
           "label": "低产源：能采但稀薄，降频到 16 小时"},
    "T2": {"routes": ["render"], "revisit_seconds": 28800,
           "depth": 4, "max_items": 120,
           "label": "需渲染：静态被 WAF 挡，直接走渲染，不浪费一次静态尝试"},
    "T3": {"routes": ["static"], "revisit_seconds": 43200,
           "depth": 3, "max_items": 60,
           "label": "链接形态可疑：采到了但正文是栏目页，降频试探并待修链接形态"},
    "T4": {"routes": ["static"], "revisit_seconds": 604800,
           "depth": 2, "max_items": 40,
           "label": "长眠源：长期零产出，降到每周一次，几乎不占预算"},
    "T5": {"routes": [], "revisit_seconds": 2592000,
           "depth": 1, "max_items": 20,
           "label": "不可采：明确失效，只做低频探活"},
}


def _load_events() -> list[dict[str, Any]]:
    if not EVENTS.exists():
        return []
    out = []
    for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _prod_signals() -> dict[str, dict[str, Any]]:
    """每个源的产出统计：条数、最近发布、最新入库、重复正文占比。"""
    out: dict[str, dict[str, Any]] = {}
    if not DB.exists():
        return out
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        for sid, n, newest_pub, newest_in in con.execute(
                "SELECT source_id, COUNT(*), MAX(publish_date), MAX(created_at) "
                "FROM announcements WHERE " + CLEAN + " GROUP BY source_id"):
            # days_since_output 是**降频/判死的唯一时间判据**。
            # 用库��最近一次入库（created_at）而不是最新发布日期：
            # publish_date 会因为「转载稿写『新华社北京9月18日电』」而缺，
            # 用它会把「刚采到的」判成「很久没更新」。
            # 采集动作发生在什么时候，只有 created_at 知道。
            days = None
            if newest_in:
                try:
                    days = (datetime.date.today()
                            - datetime.date.fromisoformat(str(newest_in)[:10])).days
                except ValueError:
                    days = None
            out[str(sid)] = {"records": int(n or 0), "newest_publish": newest_pub,
                             "newest_ingest": newest_in,
                             "days_since_output": days}
        # 重复正文（同源多篇正文一模一样 = 系统性取错内容）
        # 注意：SQLite 的 HAVING 里不能用 SELECT 别名，必须重写表达式。
        # （实测报「no such column: body」——别名只在 SELECT/ORDER BY 里可用。）
        for sid, times in con.execute(
                "SELECT source_id, COUNT(*) "
                "FROM announcements WHERE " + CLEAN +
                " AND length(coalesce(content,'')) > 0 "
                "GROUP BY source_id, substr(coalesce(content,''),1,200) "
                "HAVING COUNT(*) >= ?",
                (THRESHOLDS["dup_body_min_repeat"],)):
            rec = out.setdefault(str(sid), {"records": 0})
            rec["dup_body_records"] = rec.get("dup_body_records", 0) + int(times)
        # ★ render_share = 渲染采到的 / (渲染 + 静态)
        #
        # T2（需渲染）的**唯一实证判据**。不用配置里的 playwright_fallback：
        # 实测所有源都开了兜底，零区分度。
        for sid, cs, n in con.execute(
                "SELECT source_id, coalesce(content_source,'static_html'), COUNT(*) "
                "FROM announcements WHERE " + CLEAN + " "
                "GROUP BY source_id, coalesce(content_source,'static_html')"):
            rec = out.setdefault(str(sid), {"records": 0})
            if cs == "render_html":
                rec["_render"] = rec.get("_render", 0) + int(n)
            else:
                rec["_static"] = rec.get("_static", 0) + int(n)
        for rec in out.values():
            render = rec.pop("_render", 0)
            static = rec.pop("_static", 0)
            total = render + static
            rec["render_share"] = (render / total) if total else None

        # 失效页面
        marks = THRESHOLDS["dead_markers"]
        where = " OR ".join(["title LIKE ? OR substr(coalesce(content,''),1,300) LIKE ?"
                             for _ in marks])
        params: list[Any] = []
        for m in marks:
            params.extend(["%" + m + "%", "%" + m + "%"])
        for sid, n in con.execute(
                "SELECT source_id, COUNT(*) FROM announcements "
                "WHERE " + CLEAN + " AND (" + where + ")"
                " GROUP BY source_id", params):
            rec = out.setdefault(str(sid), {"records": 0})
            rec["dead_pages"] = rec.get("dead_pages", 0) + int(n)
        # 已被判为脏的记录数：用于「这个源只产出垃圾」的 T3 判定。
        # 单独统计而不是靠「干净记录=0」反推——后者分不清
        # 「从没产出过」和「产出了但全是垃圾」，而这两者该给不同的理由。
        for sid, n in con.execute(
                "SELECT source_id, COUNT(*) FROM announcements "
                "WHERE coalesce(needs_review,0)=1 GROUP BY source_id"):
            rec = out.setdefault(str(sid), {"records": 0})
            rec["marked_records"] = int(n)
    finally:
        con.close()
    return out


def _backoff_signals() -> dict[str, dict[str, Any]]:
    if not BACKOFF.exists():
        return {}
    try:
        raw = json.loads(BACKOFF.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    out = {}
    for sid, row in (raw or {}).items():
        row = list(row or [])
        out[str(sid)] = {
            "dry_streak": int(row[0]) if len(row) > 0 else 0,
            "last_epoch": int(row[1]) if len(row) > 1 else 0,
            "visits": int(row[2]) if len(row) > 2 else 0,
        }
    return out


def _declared_signals() -> dict[str, dict[str, Any]]:
    """源自己声明的属性：是否配了渲染兜底、层级、启用状态。"""
    import yaml
    if not SOURCES.exists():
        return {}
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8")) or {}
    out = {}
    for s in data.get("sources") or []:
        out[str(s.get("id"))] = {
            "enabled": bool(s.get("enabled")),
            "playwright_fallback": bool(s.get("playwright_fallback")),
            "access_method": str(s.get("access_method") or ""),
            "level": str(s.get("level") or ""),
            "cadence_tier": str(s.get("cadence_tier") or "daily"),
        }
    return out


def classify_source(source_id: str, prod: dict[str, Any], back: dict[str, Any],
                    declared: dict[str, Any]) -> SourceTier:
    """按实测信号给一个源定级。判据顺序 = 严重程度从高到低。

    **降频/判死用「距上次产出的天数」，不用「连续零产出轮数」。**
    理由见 THRESHOLDS 里的注释：第一版用轮数，143 个源里 132 个被判长眠，
    而同一时刻按天数统计有 126 个源本周刚产出过——两个判据结论完全相反。
    """
    days = prod.get("days_since_output")
    signals = {
        "records": prod.get("records", 0),
        "marked_records": prod.get("marked_records", 0),
        "dry_streak": back.get("dry_streak", 0),
        "dead_pages": prod.get("dead_pages", 0),
        "dup_body_records": prod.get("dup_body_records", 0),
        "playwright_fallback": declared.get("playwright_fallback", False),
        "render_share": prod.get("render_share"),
        "newest_publish": prod.get("newest_publish"),
        "days_since_output": days,
    }
    tier = "T0"
    reason = "稳定产出，走标准静态采集"

    # ── T5 不可采 ──────────────────────────────────────────────
    # 明确失效优先于一切：页面级证据（404）没有假阳性，
    # 而「很久没产出」可能只是长假或站点改版。
    if signals["dead_pages"] > 0:
        tier = "T5"
        reason = ("库里 %d 条记录的标题或正文含 404 页面标志"
                  "（Page Not Found / 找不到网页 / 页面不存在）——"
                  "页面级证据，无假阳性" % signals["dead_pages"])
    elif (days is not None
          and days >= THRESHOLDS["days_since_output_dormant"]
          and signals["dry_streak"] >= THRESHOLDS["dry_streak_confirm_dormant"]):
        # 判死要两个信号同时成立：久没产出 + 连着多轮零产出。
        # 单看任一个都会误杀——久没产出可能是长假，轮数多可能只是没访问。
        tier = "T5"
        reason = ("已 %d 天没产出，且连续 %d 轮零产出——两个信号同时成立"
                  "才判不可采" % (days, signals["dry_streak"]))
    # ── T4 长眠 ────────────────────────────────────────────────
    elif (days is not None
          and days >= THRESHOLDS["days_since_output_low"]):
        tier = "T4"
        reason = ("已 %d 天没采到新内容（阈值 %d）——按 8 小时下限每天回访 3 次"
                  "纯烧预算，降频到每周"
                  % (days, THRESHOLDS["days_since_output_low"]))
    # ── T3 链接形态可疑 ────────────────────────────────────────
    # 放在 T2 之前：走渲染也可能取到栏目页，形态不对就是不对。
    elif (signals["records"] >= 20
          and signals["dup_body_records"] / float(signals["records"])
          >= THRESHOLDS["dup_body_ratio"]):
        tier = "T3"
        reason = ("%d/%d 条正文是重复模板（%.0f%%，阈值 %.0f%%）——"
                  "标题对但正文是栏目页，详情页链接形态没找对"
                  % (signals["dup_body_records"], signals["records"],
                     100 * signals["dup_body_records"] / signals["records"],
                     100 * THRESHOLDS["dup_body_ratio"]))
    # ── T3 只产出脏数据 ────────────────────────────────────────
    # 干净记录 0、但脏记录不少：这个源**不是没产出，是产出的全是垃圾**。
    # 上一条 T3 分支靠 dup/records 比值，抓不到这种情况——比值是 0。
    # 不单独处理的话它会掉到 T1「刚启用还没产出」，理由完全说错，
    # 而且会以 16 小时频率继续产垃圾。
    elif signals["records"] == 0 and signals["marked_records"] >= 10:
        tier = "T3"
        reason = ("%d 条产出**全部**被判为需复核，干净记录 0 条——"
                  "链接形态系统性取错内容，待修链接形态"
                  % signals["marked_records"])
    # ── T2 需渲染 ──────────────────────────────────────────────
    # ★ 判据是**实际产出占比**，不是配置里的 playwright_fallback。
    #
    # 第一版用配置声明，实测 143 个源里 130 个被判 T2——因为**所有源
    # 都配了 playwright_fallback=True**（当初为兜底统一开的），
    # 这字段零区分度。实证里只有 37 个源真的靠渲染出的货更多
    # （render_html > static_html），106 个从没渲染产出过。
    #
    # 配置声明是「意图」，产出占比是「事实」。等级必须建立在事实上——
    # 否则一次「给所有源都打开兜底」的批量操作，就能把 130 个健康源
    # 全打成 T2，然后它们全部跳过静态、每轮直接起浏览器（慢且更易被封）。
    elif (signals["render_share"] is not None
          and signals["render_share"] >= 0.6):
        tier = "T2"
        reason = ("%.0f%% 的记录是渲染采到的（阈值 60%%）——静态拿不到，"
                  "每轮先试静态再降级渲染是白花一次请求"
                  % (100 * signals["render_share"]))
    # ── T1 低产 ────────────────────────────────────────────────
    elif signals["records"] > 0 and days is not None and days >= 7:
        tier = "T1"
        reason = ("库里有 %d 条但已 %d 天没新增——采得到但稀薄，降频到 16 小时"
                  % (signals["records"], days))
    elif signals["records"] == 0:
        tier = "T1"
        reason = "刚启用还没产出，先按低产观察一轮"

    policy = TIER_POLICY[tier]
    return SourceTier(
        source_id=source_id, tier=tier, reason=reason, signals=signals,
        routes=list(policy.get("routes", [])),
        revisit_seconds=int(policy["revisit_seconds"]),
        depth=int(policy["depth"]),
        max_items=int(policy["max_items"]),
    )


def classify_all() -> dict[str, SourceTier]:
    prod = _prod_signals()
    back = _backoff_signals()
    declared = _declared_signals()
    out: dict[str, SourceTier] = {}
    for sid, decl in declared.items():
        if not decl.get("enabled"):
            continue
        out[sid] = classify_source(sid, prod.get(sid, {}), back.get(sid, {}), decl)
    return out


def save(tiers: dict[str, SourceTier]) -> Path:
    TIER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "thresholds": THRESHOLDS,
        "policy": TIER_POLICY,
        "tiers": {sid: t.to_dict() for sid, t in tiers.items()},
    }
    TIER_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return TIER_CACHE


def load() -> dict[str, dict[str, Any]]:
    if not TIER_CACHE.exists():
        return {}
    try:
        return (json.loads(TIER_CACHE.read_text(encoding="utf-8")) or {}).get(
            "tiers", {})
    except ValueError:
        return {}


def summarize(tiers: dict[str, SourceTier]) -> str:
    import collections
    counts = collections.Counter(t.tier for t in tiers.values())
    lines = ["采集源等级分布（%d 个 enabled）" % len(tiers), "=" * 62]
    for tier in sorted(TIER_POLICY):
        members = [t for t in tiers.values() if t.tier == tier]
        if not members:
            continue
        lines.append("%s  %-34s %d 个"
                     % (tier, TIER_POLICY[tier]["label"][:32], len(members)))
        for t in sorted(members, key=lambda x: -x.signals.get("records", 0))[:5]:
            lines.append("      %-20s %s" % (t.source_id, t.reason[:60]))
    lines.append("")
    lines.append("按等级统计: " + "  ".join(
        "%s=%d" % (t, counts[t]) for t in sorted(counts)))
    return "\n".join(lines)


def main() -> int:
    tiers = classify_all()
    path = save(tiers)
    print(summarize(tiers))
    print()
    print("已写入 %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())