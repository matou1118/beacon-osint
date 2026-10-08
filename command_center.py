"""指挥中心 —— 工作台的唯一入口，回答三个问题。

之前这些答案散在五个页面里：想知道「现在系统怎么样」，得在总览、采集运维、
灯塔采集、进化仪表盘、成刊之间来回跳，每个页面只说其中一部分，还各自算
各自的统计。15 个导航视图之上还有 5 个 tab 组、16 个下拉控件，找一样东西
的成本全在导航上。

这一页把状态收成一处。它不新增任何统计口径——``scripts/status_summary.py``
的 ``collect()`` 已经有进程/库/轮次/退避/事件/告警的完整聚合，直接复用，
免得两处算出两个数（那种不一致比没有统计更麻烦）。

三段式：
  一、系统在做什么   —— 采集是否在跑、在等什么、上次产出
  二、要不要管       —— 告警集中在这里；没有告警就明说没有，不藏
  三、数据怎么样     —— 规模、历史深度、待复核、当期成刊
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import streamlit as st

# 本文件在**仓库根目录**，所以是 parents[0]。写成 parents[1]（子目录里用的写法）
# 会指向上一级 D:\TVbox，表现为「读不到状态」而不是报错——2026-10-01 踩过。
_ROOT = Path(__file__).resolve().parent
if not (_ROOT / "scripts" / "status_summary.py").exists():
    raise RuntimeError(f"工作台根目录判定错误：_ROOT={_ROOT}")
DB = _ROOT / "data" / "beacon.db"
PUBLICATIONS = _ROOT / "data" / "publications"
RUNTIME = _ROOT / "data" / "runtime" / "lighthouse"

_stamp: list[datetime] = []


def _summary() -> tuple[dict[str, Any], str]:
    """复用 status_summary.collect()。按 TTL 缓存——它要查进程和几张大表。

    返回 (数据, 错误说明)。**不要把异常吞掉返回 None**：那会让「页面没显示」
    和「状态读不出来」两件事看起来一模一样，排查时只能靠猜。
    """
    now = datetime.now()
    if _stamp and (now - _stamp[0]).total_seconds() < 20:
        return (st.session_state.get("_cmd_summary") or {}), st.session_state.get(
            "_cmd_error", "")
    path = _ROOT / "scripts" / "status_summary.py"
    spec = importlib.util.spec_from_file_location("_status_summary", path)
    if spec is None or spec.loader is None:
        return {}, f"无法加载 {path.name}"
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        data = module.collect()
    except Exception as exc:                              # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        st.session_state["_cmd_error"] = detail
        return {}, detail
    _stamp[:] = [now]
    st.session_state["_cmd_summary"] = data
    st.session_state["_cmd_error"] = ""
    return data, ""


def _metric(label: str, value: Any, note: str = "", tone: str = "") -> None:
    color = {"ok": "var(--ok, #16a34a)", "warn": "var(--warn, #d97706)",
             "bad": "var(--bad, #dc2626)"}.get(tone, "var(--ink)")
    note_html = (f"<div class='metric-note' style='color:{color}'>{note}</div>"
                 if note else "<div class='metric-note'></div>")
    st.markdown(
        f"<div class='metric'><div class='metric-label'>{label}</div>"
        f"<div class='metric-value'>{value}</div>{note_html}</div>",
        unsafe_allow_html=True)


def _section(title: str) -> None:
    st.markdown(f"<div class='section-label'>{title}</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ 数据库


def _scalar(con: sqlite3.Connection, sql: str, args: tuple = ()) -> Any:
    return con.execute(sql, args).fetchone()[0]


def _data_facts() -> dict[str, Any]:
    """数据侧的事实。独立于 status_summary——那些口径是运维的，这里是内容的。"""
    if not DB.exists():
        return {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        total = _scalar(con, "SELECT COUNT(*) FROM announcements")
        today = _scalar(con, "SELECT COUNT(*) FROM announcements "
                             # 本地日界。列存 UTC，date('now') 也是 UTC，
                             # 但给人看的「今日」得按本地零点切——否则每天有
                             # 8 小时把当天入库算到昨天（2026-10-06 实测）。
                             "WHERE created_at >= datetime('now','localtime',"
                             "'start of day','utc')")
        review = _scalar(con, "SELECT COUNT(*) FROM announcements "
                              "WHERE COALESCE(needs_review,0)=1")
        oldest = _scalar(con, "SELECT MIN(substr(publish_date,1,4)) FROM announcements "
                              "WHERE length(publish_date)>=4")
        recent_5y = _scalar(con, "SELECT COUNT(*) FROM announcements "
                                 "WHERE substr(publish_date,1,4) >= "
                                 "CAST(strftime('%Y','now') AS INTEGER)-4")
        corrob = _scalar(con, "SELECT COUNT(*) FROM announcements "
                              "WHERE corroboration_status='corroborated'")
        heat = _scalar(con, "SELECT COUNT(*) FROM heat_topics")
        provinces = _scalar(con, "SELECT COUNT(DISTINCT region_province) "
                                 "FROM announcements WHERE region_province<>'' "
                                 "AND region_province IS NOT NULL")
        return {"total": total, "today": today, "review": review, "oldest": oldest,
                "recent_5y": recent_5y, "corroborated": corrob,
                "heat_topics": heat, "provinces": provinces}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def _latest_publications() -> list[dict[str, Any]]:
    if not PUBLICATIONS.exists():
        return []
    out: list[dict[str, Any]] = []
    for kind in ("daily", "weekly", "monthly"):
        files = sorted(PUBLICATIONS.glob(f"{kind}-*.json"), reverse=True)
        if not files:
            continue
        try:
            import json

            payload = json.loads(files[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        payload["_file"] = files[0].name
        out.append(payload)
    return out


# ------------------------------------------------------------------ 渲染


def _next_due() -> datetime | None:
    """下一个源到期的时刻。

    status.json 里**没有**这个字段（实测：键只有 db_now/depth/phase/round_log/
    rounds_*/self_iteration/updated_at）。它写在每轮 shadow 报告的
    ``next_due_epoch`` 里，得从最新一份报告读。

    没有它就无法区分「卡死」和「正确地等下一个源到点」——32 个源在退避闸内时，
    状态文件可以几小时不更新，那是正常的（2026-10-01 实测 51 分钟）。
    """
    try:
        reports = sorted(RUNTIME.glob("shadow-*.json"),
                         key=lambda p: p.stat().st_mtime)
        if not reports:
            return None
        import json

        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
        epoch = int(payload.get("next_due_epoch") or 0)
        return (datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone()
                if epoch else None)
    except (OSError, ValueError, KeyError):
        return None


def _section_runtime(summary: dict[str, Any]) -> None:
    _section("一、系统在做什么")
    proc = summary.get("process") or {}
    status = summary.get("status") or {}
    db = summary.get("db") or {}

    alive = bool(proc.get("watchdog_alive"))
    age = float(proc.get("status_age_min") or 0)
    due = _next_due()
    waiting = due is not None and due > datetime.now().astimezone()

    # 判据要问「下一个源什么时候到」，不是「状态文件多久没动」。
    # 全部源都在退避闸内时，看门狗正确地睡到点再拉起调度器，状态文件几小时
    # 不更新是**预期**。2026-10-01 把 51 分钟的空闲报成黄色警告，是误报——
    # 那个时刻下一个源在 3.6 小时后到期。
    if not alive:
        tone, note = "bad", "看门狗不在，需要手工拉起"
    elif waiting:
        tone = "ok"
        left = (due - datetime.now().astimezone()).total_seconds() / 3600
        note = f"空闲中，下一源 {due:%H:%M} 到期（{left:.1f} 小时后）"
    elif age < 30:
        tone, note = "ok", "正在采集"
    else:
        tone, note = "warn", f"无到期源且状态已 {age:.0f} 分钟未更新"

    cols = st.columns(6)
    with cols[0]:
        _metric("采集", "运行" if alive else "停止", note, tone)
    with cols[1]:
        _metric("库内", db.get("total", "-"), "条")
    with cols[2]:
        _metric("今日", db.get("today", "-"), "条新增")
    with cols[3]:
        _metric("轮次", f"{status.get('rounds_done', 0)}/"
                        f"{status.get('rounds_planned', 0)}",
                f"阶段 {status.get('phase', '?')}")
    with cols[4]:
        _metric("翻页深度", status.get("depth", "-"), "页/源")
    with cols[5]:
        _metric("上次入库", (db.get("last_fetch") or "-")[11:19] or "-", "最后一条")

    backoff = summary.get("backoff") or {}
    if backoff:
        sleeping = int(backoff.get("sleeping") or 0)
        tracked = int(backoff.get("tracked") or 0)
        top_dry = backoff.get("top_dry") or []
        text = f"跟踪 {tracked} 源，{sleeping} 个在退避闸内"
        if top_dry:
            text += "；连续零产出最多：" + "、".join(
                f"{sid}({n})" for n, sid in top_dry[:4])
        st.caption(text + "　｜　退避不是故障：daily 源刚访问过（8 小时下限），"
                         "weekly 源距 7 天门槛还早。")


def _section_alerts(summary: dict[str, Any]) -> None:
    _section("二、要不要管")
    # status_summary._alarms() 返回的是 list[str]，不是 list[dict]。
    # 写成 dict 取值会让第一条告警就把整页炸掉——而「有告警」恰恰是最需要
    # 页面正常显示的时候。2026-10-02 实测踩到，视图冒烟测试当场抓住。
    alarms = summary.get("alarms") or []
    if not alarms:
        st.success("没有待处理的告警。采集在跑，退避在预期内，无需干预。")
        return
    for alarm in alarms:
        st.error(str(alarm))


def _section_doctor() -> None:
    """源健康摘要。逐个源的详情去看「来源健康」页，这里只给集体结论。

    **集体结论才是重点**：逐源全绿但可采源为 0，是 2026-10-01 整夜丢数据的
    那个形态——当时每项单独看都「正常」，合起来一夜零入库。
    """
    try:
        from scripts.source_doctor import check_all

        report = check_all()
    except Exception as exc:                              # noqa: BLE001
        st.caption("源诊断不可用：%s: %s" % (type(exc).__name__, exc))
        return

    col = report.get("collective") or {}
    by_tier = report.get("by_tier") or {}
    tier1, tier2, tier3 = by_tier.get(1, 0), by_tier.get(2, 0), by_tier.get(3, 0)

    if col.get("verdict"):
        st.error(col["verdict"])
        st.caption("enabled %s 个，现在能采 %s 个%s ｜ %s" % (
            col.get("enabled"), col.get("collectable_now"),
            ("，最近的源 %.1f 小时后解冻" % col["soonest_hours"])
            if col.get("soonest_hours") is not None else "",
            col.get("hint", "")))

    st.caption("源健康：待观察 %d ｜ 要修 %d ｜ 诊断器出错 %d（详见「来源健康」页）"
               % (tier1, tier2, tier3))


def _section_data(summary: dict[str, Any]) -> None:
    _section("三、数据怎么样")
    facts = _data_facts()
    if not facts:
        st.warning("读不到数据库，先看采集页。")
        return
    total = max(1, facts["total"])
    span = (datetime.now(timezone.utc).year - int(facts["oldest"] or 0)) if facts["oldest"] else 0
    review_pct = 100.0 * facts["review"] / total

    cols = st.columns(6)
    with cols[0]:
        _metric("总记录", facts["total"], "条")
    with cols[1]:
        _metric("历史起点", facts["oldest"] or "-", f"跨 {span} 年")
    with cols[2]:
        _metric("近 5 年", facts["recent_5y"],
                f"占 {100.0 * facts['recent_5y'] / total:.0f}%")
    with cols[3]:
        _metric("覆盖省份", facts["provinces"], "个")
    with cols[4]:
        _metric("交叉印证", facts["corroborated"], "条多源印证")
    with cols[5]:
        _metric("待复核", facts["review"],
                f"{review_pct:.1f}%" + ("，偏高" if review_pct > 10 else ""),
                "warn" if review_pct > 10 else "")

    events = summary.get("events") or {}
    rounds = summary.get("rounds") or []
    if events:
        by_source = events.get("by_source") or {}
        st.caption("自我迭代事件流 %d 条：%s" % (
            events.get("total", 0),
            "、".join(f"{k} {v}" for k, v in sorted(by_source.items(),
                                                      key=lambda kv: -kv[1]))))
    if rounds:
        with_rounds = [r for r in rounds if r.get("gained")]
        st.caption("最近各轮有产出的 %d / %d 轮；最近入库 %s 条" % (
            len(with_rounds), len(rounds),
            rounds[0].get("gained", 0) if rounds else 0))


def _section_publications() -> None:
    _section("四、当期成刊")
    pubs = _latest_publications()
    if not pubs:
        st.info("还没有成刊。采集每轮有新增时会自动出当日日报。")
        return
    cols = st.columns(len(pubs))
    for col, payload in zip(cols, pubs):
        stats = payload.get("stats") or {}
        with col:
            st.markdown(f"**{payload.get('edition_label', '')}**")
            st.caption(payload.get("period", {}).get("label", ""))
            st.markdown(f"收录 **{stats.get('total', 0)}** 条　"
                        f"事件 **{stats.get('events', 0)}**　"
                        f"交叉 **{stats.get('corroborated', 0)}**")
            if st.button("查看", key=f"pub_open_{payload.get('edition')}",
                         width="stretch"):
                st.session_state["active_view"] = "成刊"
                st.session_state["pub_edition"] = payload.get("edition", "daily")
                st.rerun()


def _section_actions(summary: dict[str, Any]) -> None:
    _section("五、常用动作")
    cols = st.columns(4)
    if cols[0].button("出当日刊", width="stretch", icon=":material/newspaper:"):
        from lighthouse.publish import build_edition, write_edition

        from datetime import date as _date

        payload = build_edition("daily", _date.today())
        paths = write_edition(payload)
        st.session_state["_cmd_summary"] = None
        st.success(f"已出刊：{payload['stats']['total']} 条 → {paths['markdown']}")
        st.rerun()
    if cols[1].button("回填历史后处理", width="stretch",
                      icon=":material/auto_fix_high:"):
        st.info("命令行跑：python scripts/backfill_postprocess.py --dry-run")
    if cols[2].button("标记脏数据", width="stretch", icon=":material/rule:"):
        st.info("命令行跑：python scripts/flag_dirty_records.py --dry-run")
    if cols[3].button("核对源与库", width="stretch", icon=":material/fact_check:"):
        st.info("命令行跑：python scripts/reconcile_check.py")
    st.caption("这几个动作都改数据，所以只给命令不给按钮——"
               "生产库写操作不该由一次误点触发。")


def _chart_sources() -> dict[str, Any]:
    """采集管线：多少源在管、多少在退避、哪些连续零产出。"""
    import yaml

    path = _ROOT / "config" / "sources.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or []
    by_status: dict[str, int] = {}
    for source in sources:
        by_status[str(source.get("status") or "?")] = (
            by_status.get(str(source.get("status") or "?"), 0) + 1)

    backoff: dict[str, Any] = {}
    bp = RUNTIME / "source_backoff.json"
    if bp.exists():
        try:
            import json

            backoff = json.loads(bp.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            backoff = {}
    now = datetime.now(timezone.utc).timestamp()
    sleeping = sum(1 for row in backoff.values()
                   if isinstance(row, dict) and float(row.get("due_at") or 0) > now)
    return {"status": by_status, "total": len(sources), "sleeping": sleeping}


def _chart_growth() -> dict[str, list]:
    """近 30 天每天入库量。回答「产量是不是在掉」。"""
    if not DB.exists():
        return {"days": [], "counts": []}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            # 按**本地**日分组：横轴给的是日期标签，UTC 日界会让每天有 8 小时
            # 的记录挂到前一根柱子上（2026-10-06 实测）。比较仍走 UTC 列，
            # 所以过滤条件末尾再 'utc' 转回去。
            "SELECT date(created_at,'localtime') d, COUNT(*) n FROM announcements "
            "WHERE created_at >= datetime('now','localtime','start of day',"
            "'-29 day','utc') "
            "GROUP BY date(created_at,'localtime') ORDER BY d").fetchall()
    except sqlite3.Error:
        return {"days": [], "counts": []}
    finally:
        con.close()
    return {"days": [r[0] for r in rows], "counts": [r[1] for r in rows]}


def _chart_categories() -> tuple[list[str], list[int]]:
    if not DB.exists():
        return [], []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT COALESCE(category_l1,'其他') c, COUNT(*) n FROM announcements "
            "GROUP BY c ORDER BY n DESC LIMIT 12").fetchall()
    except sqlite3.Error:
        return [], []
    finally:
        con.close()
    return [r[0] for r in rows], [r[1] for r in rows]


def _chart_provinces() -> tuple[list[str], list[int]]:
    if not DB.exists():
        return [], []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT region_province p, COUNT(*) n FROM announcements "
            "WHERE region_province IS NOT NULL AND region_province<>'' "
            "GROUP BY p ORDER BY n DESC LIMIT 12").fetchall()
    except sqlite3.Error:
        return [], []
    finally:
        con.close()
    return [r[0] for r in rows], [r[1] for r in rows]


def _section_charts(summary: dict[str, Any]) -> None:
    """仪表盘。四张图各回答一个问题，不重复同一批数字。"""
    from chart_helpers import render_echarts

    _section("三、仪表盘")
    left, right = st.columns([1.15, 1])

    # 图1 近 30 天入库量
    growth = _chart_growth()
    with left:
        st.markdown("#### 近 30 天入库量")
        if growth["days"]:
            total = sum(growth["counts"])
            st.caption("合计 %d 条，日均 %d 条" % (total, total // len(growth["days"])))
            render_echarts([{
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 44, "right": 14, "top": 18, "bottom": 26},
                "xAxis": {"type": "category", "data": growth["days"],
                          "axisLabel": {"fontSize": 9,
                                        "formatter": "{value}".replace("-", "/")[5:]}},
                "yAxis": {"type": "value", "splitLine": {"show": False}},
                "series": [{"type": "bar", "data": growth["counts"],
                            "barMaxWidth": 22, "itemStyle": {"borderRadius": [2, 2, 0, 0]}}],
            }], height=210)
        else:
            st.info("近 30 天没有入库记录。")

    # 图2 分类分布
    cats, cat_counts = _chart_categories()
    with right:
        st.markdown("#### 分类分布 TOP12")
        if cats:
            other = sum(cat_counts[-1:])
            render_echarts([{
                "tooltip": {"trigger": "item"},
                "series": [{
                    "type": "pie", "radius": ["42%", "68%"],
                    "data": [{"name": c, "value": v} for c, v in zip(cats, cat_counts)],
                    "label": {"fontSize": 9, "formatter": "{b}\n{d}%"},
                }],
            }], height=210)
            st.caption("最大一类「%s」占 %d 条。" % (cats[0], cat_counts[0]))
        else:
            st.info("还没有分类数据。")

    left2, right2 = st.columns([1.15, 1])

    # 图3 省份分布
    provs, prov_counts = _chart_provinces()
    with left2:
        st.markdown("#### 省份分布 TOP12")
        if provs:
            render_echarts([{
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "grid": {"left": 62, "right": 24, "top": 10, "bottom": 18},
                "xAxis": {"type": "value", "splitLine": {"show": False}},
                "yAxis": {"type": "category", "data": list(reversed(provs)),
                          "axisLabel": {"fontSize": 10}},
                "series": [{"type": "bar", "data": list(reversed(prov_counts)),
                            "barMaxWidth": 14}],
            }], height=250)
        else:
            st.info("还没有地区数据。")

    # 图4 源健康
    pipe = _chart_sources()
    with right2:
        st.markdown("#### 源健康")
        backoff = summary.get("backoff") or {}
        tracked = int(backoff.get("tracked") or 0)
        sleeping = int(backoff.get("sleeping") or 0)
        top_dry = backoff.get("top_dry") or []
        c1, c2, c3 = st.columns(3)
        c1.metric("在管源", tracked)
        c2.metric("退避中", sleeping)
        c3.metric("可立即采", max(0, tracked - sleeping))
        by_status = pipe.get("status") or {}
        if by_status:
            render_echarts([{
                "tooltip": {"trigger": "item"},
                "series": [{
                    "type": "pie", "radius": ["40%", "66%"],
                    "data": [{"name": k, "value": v}
                             for k, v in sorted(by_status.items(),
                                                key=lambda kv: -kv[1])],
                    "label": {"fontSize": 9, "formatter": "{b} {c}"},
                }],
            }], height=190)
        if top_dry:
            names = [sid for _n, sid in top_dry[:8]][::-1]
            counts = [n for n, _s in top_dry[:8]][::-1]
            st.markdown("**连续零产出 TOP**（正常，非故障）")
            render_echarts([{
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 78, "right": 26, "top": 8, "bottom": 18},
                "xAxis": {"type": "value", "splitLine": {"show": False}},
                "yAxis": {"type": "category", "data": names,
                          "axisLabel": {"fontSize": 10}},
                "series": [{"type": "bar", "data": counts, "barMaxWidth": 13}],
            }], height=190)
    st.caption("退避不是故障：源刚访问过（daily 档 8 小时下限）或距上次发布还早，"
               "到点会自动被拉起。")


def render(db_path: str = "data/beacon.db") -> None:
    summary, error = _summary()
    st.markdown("### 指挥中心")
    st.caption("一屏回答：系统在做什么 · 要不要管 · 数据怎么样。")

    if error:
        st.error(f"运行状态读不出来：{error}")
    if summary:
        _section_runtime(summary)
        _section_alerts(summary)
    _section_charts(summary)
    _section_doctor()
    st.markdown("---")
    _section_data(summary)
    _section_publications()
    _section_actions(summary)

    st.divider()
    st.caption("各专项页面在侧栏：成刊与检索看内容，采集运维与来源健康看管道，"
               "对比与复核看校验。")


__all__ = ["render"]
