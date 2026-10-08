"""共享 ECharts 渲染辅助 — 内页图表统一入口。

v2 改进：
1. 自动注入主题色系（chart_theme.merge_theme）— 所有图表跟随 6 套拼色主题
2. render_echarts 多图合一 — N 个图表共享一个 echarts.min.js，加载从 N 次降到 1 次
3. 透明背景 — 图表融入面板，不再有白色矩形

使用方式：
    from chart_helpers import render_echart
    render_echart({"series": [...]}, height=320)  # 主题自动注入

    from chart_helpers import render_echarts
    render_echarts([opt1, opt2, opt3], height=200)  # 3 图合一，1 次 JS 加载
"""

from __future__ import annotations

import json
from typing import Any

import streamlit.components.v1 as components

from chart_theme import merge_theme

VENDOR_URL = "/app/static/vendor"


def render_echart(option: dict[str, Any], height: int = 320, key: str | None = None) -> None:
    """在 Streamlit 页面中渲染单个 ECharts 图表（自动注入主题）。

    Args:
        option: ECharts setOption 配置字典（主题色系会自动浅合并，用户字段优先）
        height: 图表高度（像素）
        key: 已废弃，仅为兼容保留
    """
    themed = merge_theme(option)
    option_json = json.dumps(themed, ensure_ascii=False, default=str)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="{VENDOR_URL}/echarts.min.js"></script>
<style>html,body{{margin:0;padding:0;overflow:hidden}}#c{{width:100%;height:{height}px}}</style>
</head><body><div id="c"></div><script>
var chart=echarts.init(document.getElementById('c'));
chart.setOption({option_json});
window.addEventListener('resize',function(){{chart.resize()}});
</script></body></html>"""
    components.html(html, height=height)


def render_echarts(options: list[dict[str, Any]], height: int = 320, gap: int = 8) -> None:
    """在同一 iframe 中渲染多个 ECharts 图表（减少重复加载 echarts.min.js）。

    N 个图表共享一次 echarts.min.js 加载，而非 N 次独立 iframe。
    主题自动注入每个图表。

    Args:
        options: 多个 ECharts setOption 配置字典
        height: 每个图表高度（像素）
        gap: 图表间距（像素）
    """
    themed_list = [merge_theme(opt) for opt in options]
    options_json = json.dumps(themed_list, ensure_ascii=False, default=str)
    n = len(themed_list)
    total = height * n + gap * max(0, n - 1)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<script src="{VENDOR_URL}/echarts.min.js"></script>
<style>html,body{{margin:0;padding:0;overflow:hidden}}
.c{{width:100%;height:{height}px;margin-bottom:{gap}px}}
.c:last-child{{margin-bottom:0}}
</style></head><body>
<script>
var opts={options_json};
var charts=[];
opts.forEach(function(o,i){{
  var d=document.createElement('div');
  d.className='c';d.id='c'+i;document.body.appendChild(d);
  var ch=echarts.init(d);ch.setOption(o);charts.push(ch);
}});
window.addEventListener('resize',function(){{charts.forEach(function(ch){{ch.resize()}});}});
</script></body></html>"""
    components.html(html, height=total)
