"""为 README 截图。用 Playwright 无头打开 Streamlit，按页面逐张抓图。

只做一件事：截图。不改应用、不碰采集进程。

用法 / Usage:
    python scripts/capture_screenshots.py --out docs/images
    python scripts/capture_screenshots.py --out docs/images --only home
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8501"

# (id, Streamlit 侧边栏里的菜单文字, 备注)
# 侧边栏文字要跟 app.py 的实际菜单一致，否则会停在首页截一堆重复图。
PAGES = [
    ("home", None, "首页概览"),
    ("map", "中国地图", "省级分布"),
    ("lighthouse", "采集控制台", "灯塔采集链路"),
    ("ops", "指挥中心", "质量与运营"),
]


def _sidebar_items(page) -> list[str]:
    """侧边栏里可点的菜单项文字。"""
    try:
        page.wait_for_selector('[data-testid="stSidebar"]', timeout=20000)
    except Exception:                                     # noqa: BLE001
        return []
    return [
        (el.inner_text() or "").strip()
        for el in page.query_selector_all('[data-testid="stSidebar"] [role="button"], '
                                         '[data-testid="stSidebar"] a')
        if (el.inner_text() or "").strip()
    ]


def _goto(page, target: str | None, items: list[str]) -> None:
    """点侧边栏；点不到就刷新首页（Streamlit 每次 rerun 会重置）。"""
    if not target:
        page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        return
    for item in items:
        if target in item:
            try:
                page.get_by_text(item, exact=False).first.click(timeout=8000)
                return
            except Exception:                             # noqa: BLE001
                continue
    page.goto(BASE, wait_until="domcontentloaded", timeout=60000)


def _settle(page, seconds: int) -> None:
    """等 Streamlit 把这轮渲染完。``st.status('running')`` 消失即算完。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            running = page.query_selector('[data-testid="stStatusWidget"]')
            if running is None:
                time.sleep(1.2)
                return
        except Exception:                                 # noqa: BLE001
            break
        time.sleep(1.0)
    time.sleep(1.5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/images")
    ap.add_argument("--only", default=None, help="只截某一页（home/map/lighthouse/ops）")
    ap.add_argument("--width", type=int, default=1680)
    ap.add_argument("--height", type=int, default=1050)
    ap.add_argument("--settle", type=int, default=45)
    args = ap.parse_args()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装：pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 1

    made: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        ctx = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=2,          # 2x 高清，README 里缩小后依然清晰
            locale="zh-CN",
            color_scheme="dark",
            reduced_motion="reduce",        # 禁掉动画，避免抓到半渲染状态
        )
        page = ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
        _settle(page, args.settle + 30)
        items = _sidebar_items(page)
        if not items:
            print("提示：侧边栏菜单项读不到，可能只能截首页", file=sys.stderr)

        for pid, target, note in PAGES:
            if args.only and pid != args.only:
                continue
            _goto(page, target, items)
            _settle(page, args.settle)
            path = out / f"{pid}.png"
            page.screenshot(path=str(path), full_page=False)
            size_kb = path.stat().st_size // 1024
            made.append(f"{path.name} ({size_kb} KB) - {note}")
            print(f"  {path.name:<16} {size_kb:>5} KB  {note}")

        ctx.close()
        browser.close()

    print("\n完成 / done:")
    for line in made:
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())