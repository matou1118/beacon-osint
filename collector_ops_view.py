"""采集运维中心 — Streamlit view.

Answers "why is the store thin?" from the collector's own artefacts: the source
registry, the scheduler progress snapshot, the health metrics and the
self-improvement attempt log. Reuses the workbench CSS classes (panel, metric-row,
section-label, aux-stat) so it needs no styles of its own.
"""
from __future__ import annotations

import html
import json
from typing import Any

import streamlit as st

import collector_ops as ops

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


def render(db_path: str = "data/beacon.db") -> None:
    groups = ops.load_registry()
    summary = ops.summarize_registry(groups)
    progress = ops.read_progress()
    health = ops.read_health()

    # ── 源注册现状 ──
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

    with st.expander("源状态明细", expanded=False):
        st.dataframe(
            [{"来源": name, "状态": ops.STATUS_LABEL.get(str(s.get("status")), str(s.get("status"))),
              "启用": bool(s.get("enabled")), "层级": s.get("gov_level") or s.get("level"),
              "地区": s.get("region_province") or s.get("province") or "",
              "名称": s.get("name"), "URL": s.get("url")}
             for name, sources in groups.items() for s in sources],
            use_container_width=True, hide_index=True,
        )

    # ── 采集进度 ──
    st.markdown("<div class='section-label' style='margin-top:1rem'>采集进度</div>", unsafe_allow_html=True)
    if progress is None:
        st.info("暂无进度快照（调度器运行时会写入 data/runtime/collect_progress.json）。")
    else:
        failed = int(progress.get("failed") or 0)
        done = int(progress.get("done") or 0)
        succeeded = int(progress.get("succeeded") or 0)
        tone = "ok" if succeeded and not failed else ("warn" if failed else "idle")
        st.markdown(
            f"<div class='panel'><div class='aux-stat'><span>状态</span>"
            f"<b><span class='status-dot'></span>{html.escape(str(progress.get('status') or '未知'))}</b></div>"
            f"<div class='aux-stat'><span>完成 / 总数</span><b>{done} / {progress.get('total')}</b></div>"
            f"<div class='aux-stat'><span>成功 / 失败</span><b>{succeeded} / {failed}</b></div>"
            f"<div class='aux-stat'><span>入库记录</span><b>{progress.get('records')}</b></div>"
            f"<div class='aux-stat'><span>更新时间</span><b>{html.escape(str(progress.get('updated_at') or ''))}</b></div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if tone == "warn":
            st.error("进度快照显示失败数大于成功数，先看失败源的报错再放量；否则只是在重复失败。")

    c1, c2 = st.columns(2)
    with c1:
        limit = st.number_input("单轮采集源数", min_value=1, max_value=20, value=3, step=1,
                                key="ops_collect_limit")
    with c2:
        st.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        run = st.button("运行一轮采集", key="ops_run_collect", use_container_width=True)
    if run:
        with st.spinner("采集中…"):
            st.session_state["ops_collect_output"] = ops.run_collection(limit=int(limit))
        st.rerun()
    if st.session_state.get("ops_collect_output"):
        with st.expander("上次采集输出", expanded=True):
            st.code(str(st.session_state["ops_collect_output"]), language="text")

    # ── 体检 ──
    st.markdown("<div class='section-label' style='margin-top:1rem'>数据体检</div>", unsafe_allow_html=True)
    if not health:
        st.info("体检模块不可用。")
    elif "error" in health:
        st.error(f"体检失败：{health['error']}")
    else:
        metrics = health["metrics"]
        st.markdown(
            _metric_row(metrics, ("total", "summary_rate", "region_coverage_rate", "cross_level_programs")),
            unsafe_allow_html=True,
        )
        st.markdown(
            _metric_row(metrics, ("history_year_coverage", "chrome_residue_rate",
                                  "dropped_amount", "current_tier_rate")),
            unsafe_allow_html=True,
        )
        failing = [f for f in health["findings"] if not f["ok"]]
        if failing:
            st.dataframe(
                [{"指标": f["metric"], "说明": METRIC_LABEL.get(f["metric"], f["metric"]),
                  "当前值": f["value"], "要求": f["threshold"]} for f in failing],
                use_container_width=True, hide_index=True,
            )
        else:
            st.success("全部体检指标达标。")

    # ── 自修正历史 ──
    st.markdown("<div class='section-label' style='margin-top:1rem'>规则自修正记录</div>", unsafe_allow_html=True)
    log = ops.read_improve_log()
    if not log:
        st.caption("尚无尝试记录。")
    else:
        st.dataframe(
            [{"缺陷": row.get("defect"), "修复": row.get("fix"),
              "保留": row.get("kept"), "前": str(row.get("before"))[:18],
              "后": str(row.get("after"))[:18]} for row in log],
            use_container_width=True, hide_index=True,
        )
        st.caption("只在整组指标变好时才保留；失败的尝试会写回配置，不会留在生产里。")
