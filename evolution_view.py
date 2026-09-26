"""进化仪表盘 — defect registry and self-improvement queue.

This view existed in the navigation but the dispatch called
`render_evolution_dashboard`, which was never defined, so the menu item raised
NameError and blanked the page. It answers one question: which data defect is
currently failing, which candidate fixes are left, and which attempts were kept or
reverted. The queue logic is imported from scripts/self_improve.py so the page
cannot disagree with the loop that actually runs.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}


def _load() -> tuple[Any, Any, dict[str, Any], set[str]]:
    import self_improve  # type: ignore

    registry = self_improve.load_registry()
    metrics = self_improve.measure()
    return self_improve, registry, metrics, self_improve.used_fix_ids()


def render(db_path: str = "data/beacon.db") -> None:
    try:
        engine, registry, metrics, used = _load()
    except Exception as exc:  # noqa: BLE001 - never let the page die on the registry
        st.error(f"自修正模块不可用：{type(exc).__name__}: {exc}")
        return

    defects: list[dict[str, Any]] = registry.get("defects") or []
    failing = {item["metric"]: item for item in engine.failing(metrics, registry)}

    total_docs = metrics.get("total") or 0
    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{len(defects)}</div>"
        f"<div class='metric-note'>登记缺陷类别</div></div>"
        f"<div class='metric'><div class='metric-value'>{len(failing)}</div>"
        f"<div class='metric-note'>当前不达标</div></div>"
        f"<div class='metric'><div class='metric-value'>{len(used)}</div>"
        f"<div class='metric-note'>已尝试修复</div></div>"
        f"<div class='metric'><div class='metric-value'>{total_docs}</div>"
        f"<div class='metric-note'>受检记录数</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if not failing:
        st.success("当前没有不达标的指标，修正队列为空。")
    else:
        blocked: list[str] = []
        rows = []
        for item in failing.values():
            defect = next((d for d in defects if d.get("metric") == item.get("metric")), {})
            fixes = defect.get("fixes") or []
            remaining = [f for f in fixes if f.get("id") not in used]
            manual = [f for f in remaining if str((f.get("apply") or {}).get("kind")) == "none"
                      and not (f.get("apply") or {}).get("already_implemented")]
            if not remaining:
                blocked.append(str(defect.get("id") or item.get("metric")))
            kind = (remaining[0].get("apply") or {}).get("kind") if remaining else None
            rows.append({
                "缺陷": defect.get("id") or item.get("metric"),
                "说明": defect.get("description") or "",
                "严重度": SEVERITY_LABEL.get(str(defect.get("severity")), str(defect.get("severity"))),
                "当前值": item.get("value"),
                "要求": item.get("threshold"),
                "剩余可用修复": len(remaining),
                "需人工/Agent": "、".join(str(f.get("id")) for f in manual) or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        if blocked:
            st.warning(
                "这些缺陷的候选修复已全部尝试过，自动循环不会再动它们，需要新增候选或人工处理："
                + "、".join(blocked)
            )
        manual_only = [r for r in rows if r["需人工/Agent"] != "—"]
        if manual_only:
            st.caption("标为「需人工/Agent」的修复无法用改常量的方式生效（需要真实 URL、源扩容或改采集器）。")

    st.markdown("<div class='section-label' style='margin-top:1rem'>缺陷登记全表</div>", unsafe_allow_html=True)
    st.dataframe(
        [{"缺陷": d.get("id"), "指标": d.get("metric"), "方向": d.get("direction"),
          "严重度": SEVERITY_LABEL.get(str(d.get("severity")), str(d.get("severity"))),
          "说明": d.get("description"),
          "修复数": len(d.get("fixes") or []),
          "已用修复": "、".join(f.get("id") for f in (d.get("fixes") or []) if f.get("id") in used) or "—"}
         for d in defects],
        use_container_width=True, hide_index=True,
    )

    with st.expander("修正规则说明"):
        st.markdown(html.escape(str(registry.get("note") or "")).replace("\n", "  \n"))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("运行一轮自动修正", key="evo_run", use_container_width=True):
            import subprocess

            with st.spinner("测量 → 应用 → 复测中…"):
                proc = subprocess.run(
                    [sys.executable, "-B", "scripts/self_improve.py", "--iterate", "--max", "1", "--json"],
                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=1800,
                )
            st.session_state["evo_output"] = (proc.stdout or proc.stderr or "").strip()
            st.rerun()
    with c2:
        if st.button("刷新指标", key="evo_refresh", use_container_width=True):
            st.rerun()
    if st.session_state.get("evo_output"):
        with st.expander("上次运行输出", expanded=True):
            st.code(str(st.session_state["evo_output"]), language="text")
