"""采集运维中心 — Streamlit view.

Answers "why is the store thin?" from the collector's own artefacts: the source
registry, the scheduler progress snapshot, the health metrics and the
self-improvement attempt log. Reuses the workbench CSS classes (panel, metric-row,
section-label, aux-stat) so it needs no styles of its own.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

import streamlit as st

import collector_ops as ops
import quality_marks
import quality_marks

from chart_helpers import render_echart

from chart_helpers import render_echart

METRIC_LABEL = {
    "median_content_chars": "正文中位字数", "under_200_chars_rate": "正文过短率",
    "chrome_residue_rate": "页面残留率", "amount_rate": "含金额比例",
    "date_rate": "含日期比例", "summary_rate": "摘要覆盖率",
    "publish_date_rate": "发布日期覆盖率", "region_coverage_rate": "地区归属覆盖率",
    "cross_level_programs": "跨层级专项数", "section_page_rate": "栏目页混入率",
    "history_year_coverage": "近10年年份覆盖", "current_tier_rate": "当期比例",
    "summary_compression_high": "摘要过压缩", "summary_compression_low": "摘要欠压缩",
    "summary_breadcrumb_rate": "摘要含面包屑", "summary_toolbar_rate": "摘要含工具条",
    "dropped_amount": "金额丢失率", "attachment_download_rate": "附件下载率",
    "compare_coverage_rate": "对比字段覆盖", "corroboration_pass_rate": "交叉印证通过率",
}


def _metric_row(metrics: dict[str, Any], keys: tuple[str, ...]) -> str:
    cells = []
    for key in keys:
        value = metrics.get(key)
        cells.append(
            f"<div class='metric'><div class='metric-value'>{html.escape(str(value))}</div>"
            f"<div class='metric-note'>{html.escape(METRIC_LABEL.get(key, key))}</div></div>"
        )
    return f"<div class='metric-row'>{''.join(cells)}</div>"


def _render_live_progress(live: dict[str, Any] | None,
                          state: dict[str, Any] | None) -> None:
    """当前采集器的实时进度。真正的进度面板 —— 读 lighthouse 自己的产物。"""
    if live is None:
        st.info("采集器没在跑。启动后这里会逐源显示进度；"
                "「灯塔采集」页可以启动，也可以跑 python lighthouse/status.py。")
        return
    if live.get("stale"):
        st.warning(
            f"进度日志已停更 {live['age_seconds'] / 60:.0f} 分钟 —— "
            f"最后一条进度不是现在的状态，采集器可能已退出或卡死。"
            f"查 data/runtime/lighthouse/overnight.log 末尾。")
        return

    done, total = int(live["done"]), int(live["total"])
    pct = (done / total * 100.0) if total else 0.0
    running = live["running"]
    total_rows = ops.announcement_count()
    age = ops.status_age_seconds()
    # status.json 只在轮次之间写，一轮 30 分钟里它停在开轮时的值。轮次/深度/阶段
    # 标清楚是快照口径，别让它看起来跟上面的实时进度一样新。
    if age is not None and age > 90:
        suffix = f"（上次写快照 {age / 60:.0f} 分钟前，轮次结束时才更新）"
    else:
        suffix = ""
    if state:
        rounds = (f"第 {state.get('rounds_done', 0)}/{state.get('rounds_planned', 0)} 轮"
                  f" · 翻页深度 {state.get('depth')} 页"
                  + (f" · {state['phase']}" if state.get("phase") else "") + suffix)
    else:
        rounds = ""

    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{done}/{total}</div>"
        f"<div class='metric-note'>本轮源进度 {pct:.0f}%</div></div>"
        f"<div class='metric'><div class='metric-value'>{live['records']}</div>"
        f"<div class='metric-note'>本轮采到</div></div>"
        f"<div class='metric'><div class='metric-value'>{live['dropped']}</div>"
        f"<div class='metric-note'>本轮丢弃</div></div>"
        f"<div class='metric'><div class='metric-value'>{live['elapsed_minutes']:.0f}m</div>"
        f"<div class='metric-note'>已用 / 剩 {live['left_minutes']:.0f}m</div></div>"
        f"<div class='metric'><div class='metric-value'>{total_rows:,}</div>"
        f"<div class='metric-note'>库内总数（实时）</div></div>"
        f"</div>", unsafe_allow_html=True)

    st.progress(min(1.0, pct / 100.0), text=f"本轮 {done}/{total} 个源")
    tail = f"正在采集：{'、'.join(running)}" if running else "本轮源已全部提交完成"
    st.caption(f"{tail}　（{rounds}）" if rounds else tail)

    if live["dropped"] and live["records"] == 0 and done >= 8:
        st.warning(
            f"已采 {done} 个源、丢弃 {live['dropped']} 条、入库 0 —— "
            f"不是源采干了，是抓回来的内容过不了门禁。去「灯塔采集」页看丢弃原因分布。")


def _render_legacy_progress(progress: dict[str, Any] | None,
                            live: dict[str, Any] | None) -> None:
    """旧批处理采集器（scripts/batch_collect.py）的快照。

    必须标清楚它是哪套采集器的：它和 lighthouse 是两套进度文件，页面原来
    只读这套，于是停更两天的快照一直挂着 status: running，比没有还糟。
    """
    if progress is None:
        return
    updated = str(progress.get("updated_at") or "")
    age_h = ""
    if updated:
        try:
            when = datetime.fromisoformat(updated)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            age_h = f"，距今 {(datetime.now(timezone.utc) - when).total_seconds() / 3600:.0f} 小时"
        except ValueError:
            pass

    done = int(progress.get("done") or 0)
    succeeded = int(progress.get("succeeded") or 0)
    failed = int(progress.get("failed") or 0)
    with st.expander("旧批处理采集器快照（scripts/batch_collect.py，非当前采集器）",
                     expanded=False):
        st.caption(f"最后更新 {updated or '未知'}{age_h}。"
                   f"当前跑的是 lighthouse，进度以上面的实时面板为准。")
        st.markdown(
            f"<div class='panel'><div class='aux-stat'><span>状态</span>"
            f"<b>{html.escape(str(progress.get('status') or '未知'))}</b></div>"
            f"<div class='aux-stat'><span>完成 / 总数</span><b>{done} / {progress.get('total')}</b></div>"
            f"<div class='aux-stat'><span>成功 / 失败</span><b>{succeeded} / {failed}</b></div>"
            f"<div class='aux-stat'><span>入库记录</span><b>{progress.get('records')}</b></div>"
            f"</div>", unsafe_allow_html=True)
        if failed > succeeded and not live:
            st.error("这份快照里失败数大于成功数。先看失败源的报错再放量，"
                     "否则只是在重复失败。")


def _render_lighthouse_throughput(state: dict[str, Any] | None) -> None:
    """当前采集器（lighthouse）的逐轮吞吐与产出。

    原来这一段只读 data/rounds.jsonl，那是旧 batch_collect.py 的台账，写到
    2026-09-28 就停了。真正在跑的 lighthouse 的台账在 status.json 的
    round_log 里，之前没有任何页面读它 —— 于是"当前产出"是看不见的。
    """
    if not state:
        st.info("没有 lighthouse 状态快照（data/runtime/lighthouse/status.json）。"
                "跑一轮采集后会生成。")
        return
    log = state.get("round_log") or []
    if not log:
        st.info("本轮还没结束，尚无完整轮次台账。")
        return

    killed = [r for r in log if r.get("exit") == -9]
    gain = sum(int(r.get("gained") or 0) for r in log)
    minutes = sum(float(r.get("minutes") or 0) for r in log)
    per_hour = (gain / minutes * 60) if minutes else 0.0

    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{len(log)}</div>"
        f"<div class='metric-note'>已完成轮次 / {state.get('rounds_planned', '?')}</div></div>"
        f"<div class='metric'><div class='metric-value'>{gain:+,}</div>"
        f"<div class='metric-note'>本夜净增</div></div>"
        f"<div class='metric'><div class='metric-value'>{per_hour:.0f}</div>"
        f"<div class='metric-note'>条/小时</div></div>"
        f"<div class='metric'><div class='metric-value'>{len(killed)}</div>"
        f"<div class='metric-note'>被强杀轮次</div></div>"
        f"<div class='metric'><div class='metric-value'>{minutes/60:.1f}h</div>"
        f"<div class='metric-note'>累计墙钟</div></div>"
        f"</div>", unsafe_allow_html=True)

    if killed:
        st.warning(
            f"{len(killed)}/{len(log)} 轮被强杀（exit=-9）。强杀轮次的净增恒为 0，"
            f"因为来不及落盘 —— 别把它当成「源采干了」再去加深翻页。"
            f"查 overnight.log 里「超出宽限」的字样。")

    st.line_chart(
        {"净增入库": [int(r.get("gained") or 0) for r in log],
         "用时(分)": [float(r.get("minutes") or 0) for r in log]},
        x_label="轮次（按时间顺序）", y_label="条 / 分钟")
    st.dataframe(
        [{"轮": r.get("round"), "用时(分)": r.get("minutes"), "退出码": r.get("exit"),
          "入库前": r.get("db_before"), "入库后": r.get("db_after"),
          "净增": r.get("gained"), "翻页深度": r.get("depth"),
          "时间": str(r.get("at") or "")[5:16].replace("T", " ")} for r in log],
        width="stretch", hide_index=True,
        height=min(400, 38 + 35 * len(log)))


def _render_self_heal() -> None:
    """采集层自愈的观测结果（感知 -> 推导 -> 事件）。

    这一层**只记事件，不写回注册表** —— 写回要过 before/after 验证，由
    self_tune 在轮后决定。这里显示的是「感知准不准」：有没有把没坏的源误判
    为坏（假事件是纯规则系统最大的风险）。

    修复能力都在 lighthouse/repair.py 里（原先散在 selector_healer.py +
    self_heal.py 两个模块，已并入）。
    """
    try:
        from lighthouse import repair, self_tune
    except Exception as exc:                            # noqa: BLE001
        st.caption(f"自修复模块未启用：{type(exc).__name__}")
        return

    stats = repair.stats()
    mode = "只观测 + 记事件，不写回配置"

    if not stats.get("total"):
        st.info(f"{mode}。目前没有感知到入口失效 —— 这是好现象，不是没工作："
                f"感知只在 list_selector 匹配 0 元素、或 detail_pattern 一条都"
                f"滤不出时才触发。")
        return

    total = stats["total"]
    healed = stats["healed"]
    rows = self_tune._recent_broken_events(limit=12)

    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{total}</div>"
        f"<div class='metric-note'>感知到入口失效</div></div>"
        f"<div class='metric'><div class='metric-value'>{healed}</div>"
        f"<div class='metric-note'>推导出修复方案</div></div>"
        f"<div class='metric'><div class='metric-value'>{total - healed}</div>"
        f"<div class='metric-note'>判定修不了（不硬改）</div></div>"
        f"<div class='metric'><div class='metric-value'>不写回</div>"
        f"<div class='metric-note'>当前模式（写回须过验证）</div></div>"
        f"</div>", unsafe_allow_html=True)

    if rows:
        st.dataframe(
            [{"时间": str(r["ts"] or "")[5:19].replace("T", " "),
              "源": r["source_id"], "类型": r["failure_type"],
              "旧配置": (r["old"] or "")[:38],
              "新配置": (r["new"] or "")[:38],
              "结果": r["repair_result"],
              "是否生效": "否（未写回）",
              "原因": (r["reason"] or "")[:46]} for r in rows],
            width="stretch", hide_index=True)

    st.caption(f"{mode}。判定修不了时注册表零改动 —— 这是设计要求，不是缺陷。")


def _render_freshness(db_path: str) -> None:
    """P0-2：数据新鲜度告警。运维第一件要知道的事是「数据最后一次更新是什么时候」，
    而不是翻日志。超过阈值（quality_marks.STALE_HOURS）标黄。"""
    fresh = quality_marks.data_freshness(db_path)
    hours = fresh.get("hours_since_ingest")
    days_success = fresh.get("days_since_success")
    if hours is None:
        headline = "未知"
    elif hours < 24:
        headline = f"{hours:.1f} 小时前"
    else:
        headline = f"{hours / 24:.1f} 天前"
    days_text = "—" if days_success is None else f"{days_success:.2f}"
    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{html.escape(headline)}</div>"
        f"<div class='metric-note'>最新入库时间（距今）</div></div>"
        f"<div class='metric'><div class='metric-value'>{days_text}</div>"
        f"<div class='metric-note'>距最近一次成功采集（天）</div></div>"
        f"<div class='metric'><div class='metric-value'>{fresh.get('recent_count') or 0}</div>"
        f"<div class='metric-note'>最近 {fresh.get('recent_days')} 天新增</div></div>"
        f"<div class='metric'><div class='metric-value'>"
        f"{html.escape(str(fresh.get('latest_publish_date') or '—'))}</div>"
        f"<div class='metric-note'>最新发布日期</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if fresh.get("stale"):
        st.warning(
            f"数据已陈旧：超过 {fresh.get('stale_hours')} 小时没有新的成功采集。"
            "下面的采集进度和失败源清单说明卡在哪里。"
        )
    else:
        st.success("数据是新鲜的：最近一次成功采集在阈值之内。")


def render(db_path: str = "data/beacon.db") -> None:
    groups = ops.load_registry()
    summary = ops.summarize_registry(groups)
    progress = ops.read_progress()
    live = ops.read_live_progress()
    lh = ops.read_lighthouse_status()
    health = ops.read_health()

    _render_freshness(db_path)

    # ── 源注册现状（概览指标条）──
    st.markdown("<div class='section-label'>源注册现状</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{summary['by_status_labeled'].get('启用', 0)}</div>"
        f"<div class='metric-note'>启用源</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['total']}</div>"
        f"<div class='metric-note'>注册源总数</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['robots_blocked']}</div>"
        f"<div class='metric-note'>robots 拒绝（不绕过）</div></div>"
        f"<div class='metric'><div class='metric-value'>{len(summary['level'])}</div>"
        f"<div class='metric-note'>涉及层级</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    warn = ops.skew_warning(summary)
    if warn:
        st.warning(warn)
    gaps = ops.provinces_without_enabled(groups)
    if gaps:
        st.caption(f"有源但未启用的省份（{len(gaps)}）：{'、'.join(gaps)}　—— 这些是下一批探测的优先目标。")

    # ── Tabs 分区：概览 / 采集 / 体检 / 自修正 ──
    # tab 名要同时含「采集进度」和「采集吞吐与产出」两个连续子串：两个串都是
    # tests/test_view_content.py 的断言目标，中间不能插分隔符，否则子串断裂。
    tab_overview, tab_collect, tab_health, tab_heal = st.tabs([
        "源分布概览", "采集进度 / 采集吞吐与产出", "数据体检", "自修正记录",
    ])

    # ════════ Tab 1: 源分布概览 ════════
    with tab_overview:
        # 状态分布饼图 + 失败原因分类（两图并排，render_echarts 合一 iframe）
        from chart_theme import status_color
        _status_dist = sorted(summary["by_status_labeled"].items(), key=lambda kv: -kv[1])
        _status_pie_opt = None
        if len(_status_dist) >= 2:
            _status_pie_opt = {
                "title": {"text": "源状态分布", "left": "center", "top": 0,
                          "textStyle": {"fontSize": 12}},
                "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                "legend": {"bottom": 0, "textStyle": {"fontSize": 10}},
                "series": [{
                    "type": "pie", "radius": ["35%", "62%"],
                    "data": [{"name": k, "value": v,
                              "itemStyle": {"color": status_color(k)}}
                             for k, v in _status_dist],
                    "label": {"formatter": "{b}\n{c}", "fontSize": 9},
                }],
            }

        # 失败原因分类
        import re as _re
        _note_buckets: dict[str, int] = {}
        for _name, _sources in groups.items():
            for _s in _sources:
                _st_val = str(_s.get("status") or "")
                if _st_val == "enabled" and _s.get("enabled"):
                    continue
                _note = str(_s.get("notes") or "")
                if _re.search(r"robots|robotstxt", _note, _re.I):
                    _bucket = "robots 拒绝"
                elif _re.search(r"\b403\b|WAF|防火墙", _note, _re.I):
                    _bucket = "WAF/403 拒绝"
                elif _re.search(r"\b412\b", _note, _re.I):
                    _bucket = "412 预条件失败"
                elif _re.search(r"\b429\b|限流|rate.?limit", _note, _re.I):
                    _bucket = "429 限流"
                elif _re.search(r"timeout|超时", _note, _re.I):
                    _bucket = "超时"
                elif _re.search(r"ssl|tls|证书", _note, _re.I):
                    _bucket = "SSL/TLS 错误"
                elif _re.search(r"待审|probe|候选", _note, _re.I):
                    _bucket = "待审核"
                elif _note:
                    _bucket = "其他"
                else:
                    _bucket = "无备注"
                _note_buckets[_bucket] = _note_buckets.get(_bucket, 0) + 1

        _fail_bar_opt = None
        if _note_buckets:
            _sorted_buckets = sorted(_note_buckets.items(), key=lambda kv: -kv[1])
            _fail_bar_opt = {
                "title": {"text": "失败原因分类", "left": "center", "top": 0,
                          "textStyle": {"fontSize": 12}},
                "tooltip": {"trigger": "axis"},
                "grid": {"left": 80, "right": 30, "top": 30, "bottom": 10},
                "xAxis": {"type": "value", "splitLine": {"show": False}},
                "yAxis": {"type": "category", "data": [k for k, _ in _sorted_buckets],
                          "axisLabel": {"fontSize": 10}},
                "series": [{"type": "bar", "data": [v for _, v in _sorted_buckets],
                            "barWidth": 10,
                            "label": {"show": True, "position": "right", "fontSize": 9}}],
            }

        # 两图并排
        if _status_pie_opt and _fail_bar_opt:
            col_a, col_b = st.columns(2, gap="medium")
            with col_a:
                render_echart(_status_pie_opt, height=220)
            with col_b:
                render_echart(_fail_bar_opt, height=220)
        elif _status_pie_opt:
            render_echart(_status_pie_opt, height=220)

        with st.expander("源状态明细", expanded=False):
            st.dataframe(
                [{"来源": name, "状态": ops.STATUS_LABEL.get(str(s.get("status")), str(s.get("status"))),
                  "启用": bool(s.get("enabled")), "层级": s.get("gov_level") or s.get("level"),
                  "地区": s.get("region_province") or s.get("province") or "",
                  "名称": s.get("name"), "URL": s.get("url")}
                 for name, sources in groups.items() for s in sources],
                width="stretch", hide_index=True,
            )

        # 地区覆盖（移入概览 tab）
        st.markdown("<div class='section-label' style='margin-top:.6rem'>地区覆盖</div>",
                    unsafe_allow_html=True)
        region = ops.region_breakdown()
        if region.get("error"):
            st.error(f"地区统计失败：{region['error']}")
        else:
            st.markdown(
                f"<div class='metric-row'>"
                f"<div class='metric'><div class='metric-value'>{region['coverage']:.1%}</div>"
                f"<div class='metric-note'>地区归属覆盖率</div></div>"
                f"<div class='metric'><div class='metric-value'>{region['provinces']}</div>"
                f"<div class='metric-note'>覆盖省级区划</div></div>"
                f"<div class='metric'><div class='metric-value'>{region['unattributed']}</div>"
                f"<div class='metric-note'>未标注地区</div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            named = [(p, c) for p, c in region["rows"] if p != "（未标注）"]
            if named:
                st.bar_chart(dict(named), horizontal=True, height=min(480, 24 * len(named) + 60))
            else:
                st.caption("尚无带地区归属的记录。")

    # ════════ Tab 2: 采集进度与吞吐 ════════
    with tab_collect:
        _render_live_progress(live, lh)
        _render_legacy_progress(progress, live)

        c1, c2 = st.columns(2)
        with c1:
            limit = st.number_input("单轮采集源数", min_value=1, max_value=20, value=3, step=1,
                                    key="ops_collect_limit")
        with c2:
            st.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
            run = st.button("运行一轮采集", key="ops_run_collect", width="stretch")
        if run:
            with st.spinner("采集中…"):
                st.session_state["ops_collect_output"] = ops.run_collection(limit=int(limit))
            st.rerun()
        if st.session_state.get("ops_collect_output"):
            with st.expander("上次采集输出", expanded=True):
                st.code(str(st.session_state["ops_collect_output"]), language="text")

        _render_lighthouse_throughput(lh)
        with st.expander("旧批处理采集器历史台账（scripts/batch_collect.py，最后更新 2026-09-28）",
                         expanded=False):
            _render_legacy_throughput()

    # ════════ Tab 3: 数据体检 ════════
    with tab_health:
        if not health:
            st.info("体检模块不可用。")
        elif "error" in health:
            st.error(f"体检失败：{health['error']}")
        else:
            metrics = health["metrics"]
            failing = [f for f in health["findings"] if not f["ok"]]

            # ── 顶部红色告警：未达标指标一目了然 ──
            if failing:
                fail_summary = "、".join(
                    f"{METRIC_LABEL.get(f['metric'], f['metric'])}={f['value']}"
                    f"（要求{f['threshold']}）"
                    for f in failing
                )
                st.error(f"⚠️ {len(failing)} 项体检未达标：{fail_summary}")
            else:
                st.success("✅ 全部体检指标达标。")

            st.markdown(
                _metric_row(metrics, ("total", "summary_rate", "region_coverage_rate", "cross_level_programs")),
                unsafe_allow_html=True,
            )
            st.markdown(
                _metric_row(metrics, ("history_year_coverage", "chrome_residue_rate",
                                      "dropped_amount", "current_tier_rate")),
                unsafe_allow_html=True,
            )
            if failing:
                st.dataframe(
                    [{"指标": f["metric"], "说明": METRIC_LABEL.get(f["metric"], f["metric"]),
                      "当前值": f["value"], "要求": f["threshold"]} for f in failing],
                    width="stretch", hide_index=True,
                )

            # 下钻：点任一指标 → 列出触发它的记录
            drillable = [key for key in ops.DRILLABLE_METRICS if key in metrics]
            if drillable:
                pick = st.selectbox(
                    "下钻指标 → 触发记录",
                    drillable,
                    format_func=lambda key: f"{METRIC_LABEL.get(key, key)}（{key}）",
                    key="ops_health_drill",
                )
                detail = ops.metric_offenders(pick, db_path, limit=50)
                if detail.get("error"):
                    st.caption(f"无法下钻：{detail['error']}")
                elif detail["count"] == 0:
                    st.caption("该指标没有可列出的触发记录：指标达标，或由聚合值触发。")
                else:
                    st.caption(f"共 {detail['count']:,} 条触发记录，列出前 {len(detail['rows'])} 条。")
                    st.dataframe(
                        [{"ID": r["id"], "标题": r["title"], "源": r["source_id"],
                          "正文字数": r["length"]} for r in detail["rows"]],
                        width="stretch", hide_index=True,
                    )

            with st.expander(f"全部体检指标（{len(health['findings'])} 项，"
                             f"未达标 {len(failing)}）", expanded=False):
                st.dataframe(
                    [{"状态": "达标" if f["ok"] else "未达标",
                      "指标": f["metric"],
                      "说明": METRIC_LABEL.get(f["metric"], f["metric"]),
                      "当前值": f["value"], "要求": f["threshold"]}
                     for f in health["findings"]],
                    width="stretch", hide_index=True,
                )

    # ════════ Tab 4: 自修正记录 ════════
    with tab_heal:
        _render_self_heal()

        st.markdown("<div class='section-label' style='margin-top:.6rem'>规则自修正记录</div>",
                    unsafe_allow_html=True)
        log = ops.read_improve_log()
        if not log:
            st.caption("尚无尝试记录。")
        else:
            st.dataframe(
                [{"缺陷": row.get("defect"), "修复": row.get("fix"),
                  "保留": row.get("kept"), "前": str(row.get("before"))[:18],
                  "后": str(row.get("after"))[:18]} for row in log],
                width="stretch", hide_index=True,
            )
            st.caption("只在整组指标变好时才保留；失败的尝试会写回配置，不会留在生产里。")


def _render_legacy_throughput() -> None:
    rounds = ops.read_rounds()
    summary = ops.rounds_summary(rounds)
    if not summary.get("rounds"):
        st.info("尚无轮次记录。")
        return
    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{summary['rounds']}</div>"
        f"<div class='metric-note'>已完成轮次</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['fetched']}</div>"
        f"<div class='metric-note'>累计取回</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['inserted']}</div>"
        f"<div class='metric-note'>真正新增</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['duplicates']}</div>"
        f"<div class='metric-note'>重复命中</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['records_per_hour']:.0f}</div>"
        f"<div class='metric-note'>条/小时</div></div>"
        f"<div class='metric'><div class='metric-value'>{summary['wall_seconds']/3600:.1f}h</div>"
        f"<div class='metric-note'>累计墙钟</div></div>"
        f"</div>", unsafe_allow_html=True)
    st.line_chart(
        {"取回条数": [int(r.get("records") or 0) for r in rounds],
         "新增入库": [int(r.get("inserted") or 0) for r in rounds]},
        x_label="轮次（按时间顺序）", y_label="条数")
    st.dataframe(
        [{"轮": r.get("round"), "时间": str(r.get("at") or "")[11:19],
          "到期源": r.get("todo"), "成功": r.get("success"), "失败": r.get("failed"),
          "取回": r.get("records"), "新增": r.get("inserted"), "更新": r.get("updated"),
          "重复": r.get("unchanged"), "墙钟(分)": round(float(r.get("wall_seconds") or 0) / 60, 1),
          "条/分": r.get("records_per_minute")} for r in rounds],
        width="stretch", hide_index=True,
    )

