"""品牌资产统一入口。

集中管理品牌名称、标语与矢量 Logo，供 app.py（Streamlit 主文档）与
home_dashboard.py（iframe 内嵌首页）共用，避免文案与图形出现多份副本。

约定：界面文字仅使用中文，品牌名称的标准写法为 `灯塔工作台`。
"""

from __future__ import annotations

import itertools
from pathlib import Path

BRAND_NAME = "灯塔工作台"
BRAND_EYEBROW = "运营中心"
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
        return "<span aria-hidden='true' class='logo-fallback'></span>"

    svg = svg.replace("beaconLamp", f"beaconLamp{next(_LOGO_COUNTER)}")

    if decorative:
        svg = svg.replace(
            ' role="img" aria-label="灯塔工作台"',
            ' aria-hidden="true"',
        )
    return svg
