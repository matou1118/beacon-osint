"""画 README 用的架构示意图（5 级采集路由 + 数据管道）。

为什么是画图而不是截图：路由阶梯和数据管道是**代码结构**，跑起来也看不见；
截图只能拍到界面，拍不到「为什么这样设计」。这张图补的是截图补不了的那部分。

只用 PIL 画——本项目没有 matplotlib，而为了画三张图去装一个新依赖不划算
（工程原则：能一行stdin 解决的，不引入库）。

用法 / Usage:
    python scripts/draw_architecture.py --out docs/images
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 取自 dashboard 的暗色金线配色，保持与截图同一套视觉
BG = (10, 12, 20)
PANEL = (20, 24, 34)
BORDER = (44, 50, 66)
GOLD = (240, 185, 11)
GOLD_DIM = (150, 118, 20)
TEXT = (226, 232, 240)
TEXT_DIM = (138, 148, 168)
CYAN = (34, 211, 238)
RED = (244, 114, 114)
GREEN = (74, 222, 128)


def _font(size: int, bold: bool = False):
    """找一个能显示中文的字体。找不到就退回默认（中文会变方块）。"""
    from PIL import ImageFont

    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",              # 微软雅黑
        r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    if bold:
        candidates = [r"C:\Windows\Fonts\msyhbd.ttc"] + candidates
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:                          # noqa: BLE001
                continue
    return ImageFont.load_default()


def _center(draw, box, text, font, fill):
    x0, y0, x1, y1 = box
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    draw.text((x0 + (x1 - x0 - (r - l)) / 2 - l,
               y0 + (y1 - y0 - (b - t)) / 2 - t), text, font=font, fill=fill)


def draw_routing(out: Path) -> Path:
    """5 级采集路由：逐级升级，而不是一上来就用最贵的手段。"""
    from PIL import Image, ImageDraw

    W, H = 1680, 720
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = _font(34, bold=True)
    f_sub = _font(17)
    f_step = _font(22, bold=True)
    f_note = _font(14)
    f_cost = _font(13)

    d.text((48, 34), "5 级采集路由 · 按成本逐级升级", font=f_title, fill=GOLD)
    d.text((48, 82), "5-Stage Collection Routing — escalate by cost, never jump to the expensive tier first",
           font=f_sub, fill=TEXT_DIM)

    steps = [
        ("static", "静态 HTTP", "~0.3s", "绝大多数政府站首选", GREEN),
        ("render", "浏览器渲染", "~10s", "JS 建的列表页", GOLD),
        ("markdown", "Markdown 抽取", "~30s", "正文被包在结构标签里", CYAN),
        ("pdf", "PDF 解析", "~60s", "公告本体是 PDF", GOLD_DIM),
        ("drop", "丢弃", "—", "重试与换路由都救不回来", RED),
    ]
    bw, gap = 268, 40
    x0, y0, bh = 48, 150, 250
    for i, (name, label, cost, note, color) in enumerate(steps):
        x = x0 + i * (bw + gap)
        box = (x, y0, x + bw, y0 + bh)
        d.rounded_rectangle(box, radius=14, fill=PANEL, outline=color, width=2)
        d.text((x + 20, y0 + 22), name, font=f_step, fill=color)
        d.text((x + 20, y0 + 62), label, font=_font(18), fill=TEXT)
        d.text((x + 20, y0 + 108), cost, font=_font(22, bold=True), fill=GOLD)
        # 换行画说明
        line, yy = "", y0 + 152
        for ch in note:
            if d.textlength(line + ch, font=f_note) > bw - 40:
                d.text((x + 20, yy), line, font=f_note, fill=TEXT_DIM)
                line, yy = ch, yy + 22
            else:
                line += ch
        if line:
            d.text((x + 20, yy), line, font=f_note, fill=TEXT_DIM)
        if i < len(steps) - 1:
            ax = x + bw + 8
            d.line([(ax, y0 + bh // 2), (ax + gap - 18, y0 + bh // 2)], fill=GOLD_DIM, width=3)
            d.polygon([(ax + gap - 18, y0 + bh // 2 - 7),
                       (ax + gap - 6, y0 + bh // 2),
                       (ax + gap - 18, y0 + bh // 2 + 7)], fill=GOLD_DIM)

    # 底部：门禁与熔断
    y1 = y0 + bh + 56
    panels = [
        ("质量门禁", "chrome / link_flood / toc_page / listing_page / nav_page\n"
                     "区分「内容是垃圾」（值得升级渲染）与「内容好只是短」（不要升级）"),
        ("深翻游标", "early stop 时记住停在哪，下次从那里继续\n"
                     "已知 URL 用来跳过，不再用来停止"),
        ("退避与熔断", "按源记录干 streak，阶梯退避\n零产出自动熔断，不反复敲同一个站点"),
    ]
    pw = (W - 96 - 2 * 28) // 3
    for i, (t, body) in enumerate(panels):
        x = 48 + i * (pw + 28)
        d.rounded_rectangle((x, y1, x + pw, y1 + 168), radius=12,
                            fill=PANEL, outline=BORDER, width=2)
        d.text((x + 20, y1 + 18), t, font=_font(19, bold=True), fill=GOLD)
        for k, ln in enumerate(body.split("\n")):
            d.text((x + 20, y1 + 56 + k * 24), ln, font=f_cost, fill=TEXT_DIM)

    d.text((48, H - 46), "设计原则：先证明更便宜的路径走不通，再花更贵的钱。"
                         "  Principle: prove the cheap path fails before paying for the expensive one.",
           font=f_note, fill=TEXT_DIM)

    path = out / "architecture-routing.png"
    img.save(path, "PNG", optimize=True)
    return path


def draw_pipeline(out: Path) -> Path:
    """采集 → 抽取 → 分类 → 摘要 → 入库 的数据管道。"""
    from PIL import Image, ImageDraw

    W, H = 1680, 460
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = _font(30, bold=True)
    f_sub = _font(16)
    f_box = _font(19, bold=True)
    f_note = _font(13)

    d.text((48, 30), "数据管道 · 采集 → 抽取 → 分类 → 摘要 → 入库",
           font=f_title, fill=GOLD)
    d.text((48, 72), "Data Pipeline — collect → extract → classify → summarize → store",
           font=f_sub, fill=TEXT_DIM)

    stages = [
        ("① 采集", "sources.yaml\n155 个源", "退避/熔断/游标"),
        ("② 抽取", "标题·正文·日期\n金额·截止·对象", "带标签的值优先"),
        ("③ 分类", "21 类 / 105 子类\n命中用户订阅分类", "l1+l2 双向校验"),
        ("④ 摘要", "原文事实字段拼装\n不生成不改写", "空摘要带成因"),
        ("⑤ 入库", "SQLite + 指纹去重\n跨 URL 重复识别", "content_fingerprint"),
    ]
    bw, bh, gap = 288, 210, 34
    x0, y0 = 48, 128
    for i, (t, body, note) in enumerate(stages):
        x = x0 + i * (bw + gap)
        d.rounded_rectangle((x, y0, x + bw, y0 + bh), radius=14,
                            fill=PANEL, outline=GOLD_DIM, width=2)
        d.text((x + 20, y0 + 20), t, font=f_box, fill=GOLD)
        for k, ln in enumerate(body.split("\n")):
            d.text((x + 20, y0 + 64 + k * 26), ln, font=_font(15), fill=TEXT)
        d.line([(x + 20, y0 + bh - 42), (x + bw - 20, y0 + bh - 42)], fill=BORDER, width=1)
        d.text((x + 20, y0 + bh - 32), note, font=f_note, fill=CYAN)
        if i < len(stages) - 1:
            ax = x + bw + 6
            d.line([(ax, y0 + bh // 2), (ax + gap - 16, y0 + bh // 2)], fill=GOLD, width=3)
            d.polygon([(ax + gap - 16, y0 + bh // 2 - 7),
                       (ax + gap - 4, y0 + bh // 2),
                       (ax + gap - 16, y0 + bh // 2 + 7)], fill=GOLD)

    d.text((48, H - 46),
           "数据默认不入库到本仓库：采集产物受原始发布机关的权利约束，见 THIRD_PARTY_NOTICES.md",
           font=f_note, fill=TEXT_DIM)

    path = out / "architecture-pipeline.png"
    img.save(path, "PNG", optimize=True)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/images")
    args = ap.parse_args()

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    for fn in (draw_routing, draw_pipeline):
        try:
            path = fn(out)
            print("  %-34s %6d KB" % (path.name, path.stat().st_size // 1024))
        except Exception as exc:                        # noqa: BLE001
            print("  %-34s 失败: %s: %s" % (fn.__name__, type(exc).__name__, exc),
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())