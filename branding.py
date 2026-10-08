"""品牌资产统一入口。

集中管理品牌名称、标语与矢量 Logo，供 app.py（Streamlit 主文档）与
home_dashboard.py（iframe 内嵌首页）共用，避免文案与图形出现多份副本。

品牌名称：灯塔 Beacon OSINT
"""

from __future__ import annotations

import itertools
from pathlib import Path

BRAND_NAME = "灯塔 Beacon OSINT"
BRAND_TAGLINE = "来源健康 · 数据质量 · 公告检索 · 运维审计"

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.svg"
_LOGO_COUNTER = itertools.count(1)


def load_logo_svg(decorative: bool = True) -> str:
    """返回内联 SVG 源码。

    decorative=True：作为装饰图形输出（旁边已有可见品牌文字时使用）。
    此时移除 role/aria-label 并对屏幕阅读器隐藏，避免品牌名被重复朗读。

    同时为渐变 id 追加自增序号，保证同一文档内多次内联不会产生重复 id。
    """
    try:
        svg = LOGO_PATH.read_text(encoding="utf-8")
    except OSError:
        return _fallback_logo(decorative)

    svg = svg.replace("beaconLamp", f"beaconLamp{next(_LOGO_COUNTER)}")

    if decorative:
        svg = svg.replace(
            ' role="img" aria-label="灯塔 Beacon OSINT"',
            ' aria-hidden="true"',
        )
    return svg


def _fallback_logo(decorative: bool = True) -> str:
    """内联备用 Logo：灯塔 + 信号波纹，不依赖外部文件。"""
    aria = ' aria-hidden="true"' if decorative else ' role="img" aria-label="灯塔 Beacon OSINT"'
    return (
        f'<svg {aria} width="32" height="32" viewBox="0 0 32 32" fill="none" '
        f'xmlns="http://www.w3.org/2000/svg" style="color:var(--accent,#22d3ee)">'
        f'<defs>'
        f'<linearGradient id="beaconLampFallback" x1="16" y1="8" x2="16" y2="28" gradientUnits="userSpaceOnUse">'
        f'<stop stop-color="currentColor" stop-opacity="0.9"/>'
        f'<stop offset="1" stop-color="currentColor" stop-opacity="0.3"/>'
        f'</linearGradient>'
        f'<radialGradient id="beaconGlow" cx="16" cy="15" r="10" gradientUnits="userSpaceOnUse">'
        f'<stop stop-color="currentColor" stop-opacity="0.35"/>'
        f'<stop offset="1" stop-color="currentColor" stop-opacity="0"/>'
        f'</radialGradient>'
        f'</defs>'
        f'<!-- 光晕 -->'
        f'<circle cx="16" cy="15" r="14" fill="url(#beaconGlow)"/>'
        f'<!-- 外层信号弧 -->'
        f'<path d="M5 13a11 11 0 0 1 0-2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.3"/>'
        f'<path d="M27 13a11 11 0 0 0 0-2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.3"/>'
        f'<!-- 内层信号弧 -->'
        f'<path d="M9 14a7 7 0 0 1 0-2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.55"/>'
        f'<path d="M23 14a7 7 0 0 0 0-2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity="0.55"/>'
        f'<!-- 光束 -->'
        f'<path d="M15 15.5 4 11v9L15 15.5Z" fill="currentColor" opacity="0.1"/>'
        f'<path d="M17 15.5 28 11v9L17 15.5Z" fill="currentColor" opacity="0.1"/>'
        f'<!-- 塔身 -->'
        f'<path d="M13.5 28 14.8 18h2.4L18.5 28Z" fill="currentColor"/>'
        f'<!-- 塔基 -->'
        f'<rect x="11" y="26.5" width="10" height="2" rx="1" fill="currentColor" opacity="0.7"/>'
        f'<rect x="8" y="29.5" width="16" height="1.5" rx="0.75" fill="currentColor" opacity="0.25"/>'
        f'<!-- 灯室 -->'
        f'<circle cx="16" cy="15" r="3.5" fill="url(#beaconLampFallback)"/>'
        f'<circle cx="16" cy="15" r="1.2" fill="currentColor"/>'
        f'</svg>'
    )
