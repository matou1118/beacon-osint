"""图表主题桥接 — 让 ECharts 跟随 6 套拼色主题。

之前所有图表硬编码 #5B8FF9 / #5AD8A6 等固定色，与 theme_config 的 6 套主题完全脱节。
本模块从 session_state 读取当前主题，生成 ECharts option 片段，由 chart_helpers 自动注入。

用法（自动，无需手动调用）：
    chart_helpers.render_echart(option)  # 主题已自动合并

手动获取色板：
    from chart_theme import chart_palette, chart_base_option
    palette = chart_palette()          # ["#22d3ee", "#7c3aed", ...]
    base = chart_base_option()         # {backgroundColor, textStyle, ...}
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from theme_config import THEMES, default_theme


def _current_theme_id() -> str:
    """从 session_state 读取当前主题 ID，回退到默认。"""
    try:
        return st.session_state.get("theme") or default_theme()
    except Exception:
        return default_theme()


def _theme_js() -> dict[str, Any]:
    """当前主题的 JS 色板令牌。"""
    tid = _current_theme_id()
    theme = THEMES.get(tid) or THEMES.get(default_theme()) or {}
    return theme.get("js") or {}


def chart_palette(n: int = 8) -> list[str]:
    """返回与当前主题协调的 n 色色板。

    顺序：accent → accent_2 → accent_3 → amber → green → red → 渐变插值。
    所有色值取自 theme_config，不硬编码。
    """
    js = _theme_js()
    base = [
        js.get("accent", "#22d3ee"),
        js.get("accent_2", "#7c3aed"),
        js.get("accent_3", "#f472b6"),
        js.get("amber", "#fbbf24"),
        js.get("green", "#34d399"),
        js.get("red", "#f87171"),
        js.get("accent_dim", "#0ea5c4"),
        js.get("muted", "#8a94ab"),
    ]
    # 去重并保持顺序
    seen: set[str] = set()
    unique = [c for c in base if c and not (c in seen or seen.add(c))]
    # 不足 n 色时循环复用
    while len(unique) < n:
        unique.append(unique[len(unique) % len(unique)])
    return unique[:n]


def chart_base_option() -> dict[str, Any]:
    """生成 ECharts 基础 option：背景、文字、轴线、tooltip、legend 的主题样式。

    由 chart_helpers.render_echart 自动浅合并到用户 option 中。
    用户显式设置的同名字段优先（浅合并，不深递归）。
    """
    js = _theme_js()
    palette = chart_palette()
    ink = js.get("ink", "#e8ecf5")
    muted = js.get("muted", "#8a94ab")
    panel = js.get("panel", "#161c2a")
    line = js.get("line", "rgba(140,160,200,0.12)")
    accent = js.get("accent", "#22d3ee")

    return {
        "color": palette,
        "backgroundColor": "transparent",
        "textStyle": {"color": ink, "fontFamily": "inherit"},
        "title": {"textStyle": {"color": ink}},
        "legend": {
            "textStyle": {"color": muted, "fontSize": 11},
            "icon": "roundRect",
            "itemWidth": 12,
            "itemHeight": 8,
        },
        "tooltip": {
            "backgroundColor": panel,
            "borderColor": line,
            "textStyle": {"color": ink, "fontSize": 12},
            "extraCssText": "box-shadow:0 4px 16px rgba(0,0,0,0.4);border-radius:6px;",
        },
        "xAxis": {
            "axisLine": {"lineStyle": {"color": line}},
            "axisLabel": {"color": muted, "fontSize": 10},
            "splitLine": {"lineStyle": {"color": line, "type": "dashed"}},
        },
        "yAxis": {
            "axisLine": {"lineStyle": {"color": line}},
            "axisLabel": {"color": muted, "fontSize": 10},
            "splitLine": {"lineStyle": {"color": line, "type": "dashed"}},
        },
        "categoryAxis": {
            "axisLine": {"lineStyle": {"color": line}},
            "axisLabel": {"color": muted, "fontSize": 10},
            "splitLine": {"show": False},
        },
        "valueAxis": {
            "axisLine": {"lineStyle": {"color": line}},
            "axisLabel": {"color": muted, "fontSize": 10},
            "splitLine": {"lineStyle": {"color": line, "type": "dashed"}},
        },
    }


def merge_theme(option: dict[str, Any]) -> dict[str, Any]:
    """将主题基础样式浅合并到用户 option。

    合并策略：
    - color / backgroundColor：用户优先
    - textStyle / title / legend / tooltip：浅合并（用户字段覆盖主题字段）
    - xAxis / yAxis：用户若为 dict 则浅合并，若为 list 则逐项浅合并
    """
    base = chart_base_option()
    merged = dict(option)

    # 简单顶层字段：用户优先
    for key in ("color", "backgroundColor"):
        if key not in merged:
            merged[key] = base[key]

    # 浅合并的 dict 字段
    for key in ("textStyle", "title", "legend", "tooltip"):
        if key in base:
            user_val = merged.get(key)
            if isinstance(user_val, dict):
                merged[key] = {**base[key], **user_val}
            elif key not in merged:
                merged[key] = base[key]

    # xAxis / yAxis：可能是 dict、list[dict]、或不存在
    for axis_key in ("xAxis", "yAxis"):
        user_axis = merged.get(axis_key)
        base_axis = base.get(axis_key)
        if base_axis is None:
            continue
        if user_axis is None:
            merged[axis_key] = base_axis
        elif isinstance(user_axis, dict):
            merged[axis_key] = {**base_axis, **user_axis}
        elif isinstance(user_axis, list):
            merged[axis_key] = [
                {**base_axis, **ax} if isinstance(ax, dict) else ax
                for ax in user_axis
            ]

    return merged


def status_color(status: str) -> str:
    """语义状态色：成功/警告/错误/信息，取自当前主题。"""
    js = _theme_js()
    green = js.get("green", "#34d399")
    amber = js.get("amber", "#fbbf24")
    red = js.get("red", "#f87171")
    accent = js.get("accent", "#22d3ee")
    table = {
        "ok": green, "success": green, "正常": green, "成功": green,
        "warn": amber, "warning": amber, "待审": amber, "pending": amber, "待运行": amber,
        "error": red, "fail": red, "failed": red, "失败": red, "异常": red,
        "info": accent, "default": accent,
    }
    return table.get(status, accent)
