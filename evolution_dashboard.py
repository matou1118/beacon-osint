"""进化仪表盘：展示自进化的历史、修复成功率、顽固缺陷和 discover_fix 候选。

这个页面让"自进化"从"黑箱"变成"白箱"——
不是"指标没改善"，而是"这 2 个样本没改善"；
不是"修复已用尽"，而是"discover_fix 发现了 3 个新候选"。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pandas as pd
import streamlit as st

import altair as alt

import altair as alt

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOG = ROOT / "data" / "self_improve_log.jsonl"
REGISTRY = ROOT / "config" / "defect_registry.yaml"


def load_history() -> list[dict[str, Any]]:
    if not LOG.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def load_registry() -> dict[str, Any]:
    import yaml
    if not REGISTRY.exists():
        return {}
    with REGISTRY.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def render(db_path: str) -> None:
    st.subheader("进化仪表盘")
    st.caption("自进化的历史、修复成功率、顽固缺陷和 discover_fix 候选")

    history = load_history()
    if not history:
        st.info("暂无进化记录。运行 `python scripts/self_improve.py --iterate` 开始自进化。")
        return

    # ── 顶部指标卡 ──
    total = len(history)
    kept = sum(1 for h in history if h.get("kept"))
    reverted = sum(1 for h in history if h.get("applied") and not h.get("kept"))
    skipped = sum(1 for h in history if not h.get("applied"))
    discover_count = sum(1 for h in history if h.get("fix") == "discover_fix")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("总尝试", total)
    col2.metric("保留", kept)
    col3.metric("撤销", reverted)
    col4.metric("跳过", skipped)
    col5.metric("discover 候选", discover_count)

    kept_rate = kept / total * 100 if total else 0
    st.progress(kept_rate / 100, text=f"修复保留率 {kept_rate:.1f}%")

    # ── 进化历史折线图 ──
    st.markdown("---")
    st.markdown("### 进化历史")

    # 按缺陷分组，展示 before/after 趋势
    by_defect: dict[str, list[dict]] = defaultdict(list)
    for h in history:
        defect = h.get("defect") or "unknown"
        by_defect[defect].append(h)

    chart_data: list[dict[str, Any]] = []
    for idx, h in enumerate(history):
        chart_data.append({
            "步": idx + 1,
            "时间": h.get("at", "")[:19],
            "缺陷": h.get("defect") or "",
            "修复": h.get("fix") or "",
            "before": h.get("before", 0),
            "after": h.get("after", 0),
            "保留": 1 if h.get("kept") else 0,
            "结果": "保留" if h.get("kept") else "撤销",
        })

    # ── 折线图：before/after 趋势 ──
    if chart_data:
        df = pd.DataFrame(chart_data)
        df_long = df.melt(
            id_vars=["步", "缺陷", "结果"],
            value_vars=["before", "after"],
            var_name="阶段",
            value_name="指标值",
        )

        line = (
            alt.Chart(df_long)
            .mark_line(point=True, strokeWidth=2)
            .encode(
                x=alt.X("步:Q", title="进化步数"),
                y=alt.Y("指标值:Q", title="指标值"),
                color=alt.Color("缺陷:N", legend=alt.Legend(title="缺陷", labelLimit=200)),
                shape=alt.Shape("阶段:N", legend=alt.Legend(title="阶段")),
                tooltip=["步", "缺陷", "阶段", "指标值", "结果"],
            )
            .properties(height=400)
            .interactive()
        )
        st.altair_chart(line, use_container_width=True)

        st.dataframe(chart_data, use_container_width=True, hide_index=True)

    # ── 回滚链可视化 ──
    st.markdown("---")
    st.markdown("### 回滚链")

    rollback_data: list[dict[str, Any]] = []
    for idx, h in enumerate(history):
        applied = h.get("applied", False)
        kept = h.get("kept", False)
        if applied and not kept:
            status = "🔄 撤销"
        elif kept:
            status = "✅ 保留"
        elif not applied:
            status = "⏭ 跳过"
        else:
            status = "❓ 未知"
        rollback_data.append({
            "步": idx + 1,
            "缺陷": h.get("defect") or "",
            "修复": h.get("fix") or "",
            "状态": status,
            "before": h.get("before", 0),
            "after": h.get("after", 0),
        })

    if rollback_data:
        rb_df = pd.DataFrame(rollback_data)
        # 用条形图展示回滚链
        bar = (
            alt.Chart(rb_df)
            .mark_bar()
            .encode(
                x=alt.X("步:Q", title="进化步数"),
                y=alt.Y("缺陷:N", title=""),
                color=alt.Color(
                    "状态:N",
                    legend=alt.Legend(title="结果"),
                    scale=alt.Scale(
                        domain=["✅ 保留", "🔄 撤销", "⏭ 跳过", "❓ 未知"],
                        range=["#2ecc71", "#e74c3c", "#95a5a6", "#f39c12"],
                    ),
                ),
                tooltip=["步", "缺陷", "修复", "状态", "before", "after"],
            )
            .properties(height=max(200, len(by_defect) * 30))
        )
        st.altair_chart(bar, use_container_width=True)

        # 回滚链文字摘要
        kept_chain = [r for r in rollback_data if "保留" in r["状态"]]
        revert_chain = [r for r in rollback_data if "撤销" in r["状态"]]
        skip_chain = [r for r in rollback_data if "跳过" in r["状态"]]
        col_x, col_y, col_z = st.columns(3)
        col_x.metric("保留链", len(kept_chain))
        col_y.metric("撤销链", len(revert_chain))
        col_z.metric("跳过链", len(skip_chain))

    # ── 按缺陷统计 ──
    st.markdown("---")
    st.markdown("### 按缺陷统计")

    defect_stats: list[dict[str, Any]] = []
    for defect, entries in sorted(by_defect.items()):
        d_total = len(entries)
        d_kept = sum(1 for e in entries if e.get("kept"))
        d_reverted = sum(1 for e in entries if e.get("applied") and not e.get("kept"))
        last = entries[-1]
        defect_stats.append({
            "缺陷": defect,
            "尝试": d_total,
            "保留": d_kept,
            "撤销": d_reverted,
            "最近 before": last.get("before", 0),
            "最近 after": last.get("after", 0),
            "最近结果": "保留" if last.get("kept") else "撤销",
        })

    st.dataframe(defect_stats, use_container_width=True, hide_index=True)

    # ── 顽固缺陷（试了 ≥3 次都不行）──
    st.markdown("---")
    st.markdown("### 顽固缺陷")
    stubborn = [s for s in defect_stats if s["撤销"] >= 3 and s["保留"] == 0]
    if stubborn:
        st.warning(f"发现 {len(stubborn)} 个顽固缺陷（试了 ≥3 次都撤销）")
        st.dataframe(stubborn, use_container_width=True, hide_index=True)
    else:
        st.success("没有顽固缺陷")

    # ── discover_fix 候选 ──
    st.markdown("---")
    st.markdown("### discover_fix 候选")
    discover_entries = [h for h in history if h.get("fix") == "discover_fix"]
    if discover_entries:
        for entry in discover_entries[-5:]:  # 最近 5 条
            with st.expander(f"{entry.get('defect')} — {entry.get('at', '')[:19]}"):
                for note in entry.get("notes") or []:
                    st.text(note)
    else:
        st.info("暂无 discover_fix 候选。运行 `python scripts/self_improve.py --iterate --discover` 触发自动发现。")

    # ── 缺陷登记表概览 ──
    st.markdown("---")
    st.markdown("### 缺陷登记表")
    registry = load_registry()
    defects = registry.get("defects") or []
    registry_data: list[dict[str, Any]] = []
    for defect in defects:
        fixes = defect.get("fixes") or []
        registry_data.append({
            "缺陷": defect.get("id"),
            "指标": defect.get("metric"),
            "方向": defect.get("direction"),
            "严重度": defect.get("severity"),
            "修复数": len(fixes),
            "描述": defect.get("description"),
        })
    st.dataframe(registry_data, use_container_width=True, hide_index=True)

    # ── 操作入口 ──
    st.markdown("---")
    st.markdown("### 操作")
    col_a, col_b, col_c = st.columns(3)
    if col_a.button("查看队列", use_container_width=True):
        st.info("在终端运行：`python scripts/self_improve.py --status`")
    if col_b.button("执行一轮进化", use_container_width=True):
        st.info("在终端运行：`python scripts/self_improve.py --iterate`")
    if col_c.button("执行一轮进化（含 discover）", use_container_width=True):
        st.info("在终端运行：`python scripts/self_improve.py --iterate --discover`")
