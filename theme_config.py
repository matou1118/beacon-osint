"""拼色工作台主题配置 — 6 套统一色系。

拼色结构：左侧边栏一个实色块 + 顶部栏一个实色块 + 内容区中性深色，
三段不同色相拼合，形成清晰的整体分区，避免单一色块单调。

每套主题包含 CSS 令牌 + JS 令牌，用于：
1. app.py 的 Streamlit CSS 变量注入（码道工作台布局）
2. map_dashboard.py 的 ECharts/Three.js 颜色配置
3. home_dashboard.py 的 3D 地球色系
4. 右侧栏主题选择器

主题列表：
  - indigo_violet  蓝紫拼色（默认：靛蓝侧栏 + 紫罗兰顶栏 + 电光青强调）
  - teal_coral     青碧珊瑚（深青侧栏 + 珊瑚橙顶栏）
  - moss_lime      苔绿青柠（苔绿侧栏 + 青柠顶栏）
  - plum_rose      紫檀玫红（紫檀侧栏 + 玫红顶栏）
  - slate_amber    石墨鎏金（石墨侧栏 + 琥珀顶栏）
  - navy_sky       深海天青（海军蓝侧栏 + 天青顶栏）
"""

from __future__ import annotations

from typing import Any


THEME_ORDER = [
    "indigo_violet",
    "teal_coral",
    "moss_lime",
    "plum_rose",
    "slate_amber",
    "navy_sky",
]

THEME_LABELS = {
    "indigo_violet": "蓝紫拼色",
    "teal_coral": "青碧珊瑚",
    "moss_lime": "苔绿青柠",
    "plum_rose": "紫檀玫红",
    "slate_amber": "石墨鎏金",
    "navy_sky": "深海天青",
}

THEME_DESCRIPTIONS = {
    "indigo_violet": "靛蓝侧栏 + 紫罗兰顶栏，电光青强调",
    "teal_coral": "深青侧栏 + 珊瑚橙顶栏，清爽有活力",
    "moss_lime": "苔绿侧栏 + 青柠顶栏，自然冷静",
    "plum_rose": "紫檀侧栏 + 玫红顶栏，沉稳带暖意",
    "slate_amber": "石墨侧栏 + 琥珀顶栏，经典指挥台",
    "navy_sky": "海军蓝侧栏 + 天青顶栏，深海工位",
}

THEME_SWATCHES = {
    "indigo_violet": ["#312e81", "#7c3aed", "#22d3ee"],
    "teal_coral": ["#115e59", "#ea580c", "#2dd4bf"],
    "moss_lime": ["#166534", "#65a30d", "#4ade80"],
    "plum_rose": ["#6b21a8", "#e11d48", "#c084fc"],
    "slate_amber": ["#334155", "#d97706", "#fbbf24"],
    "navy_sky": ["#1e40af", "#0284c7", "#38bdf8"],
}


# ──────────────────────────────────────────────
# 主题令牌定义
# 新增拼色令牌：--side-*（左侧栏色块）、--top-*（顶栏色块）
# ──────────────────────────────────────────────

THEMES: dict[str, dict[str, Any]] = {
    # ═══════════════════════════════════════════
    # 蓝紫拼色 — 默认主题
    # 靛蓝侧栏 + 紫罗兰顶栏 + 电光青强调
    # ═══════════════════════════════════════════
    "indigo_violet": {
        "name": "蓝紫拼色",
        "is_dark": True,
        "css": {
            "--ground": "#0f1218",
            "--ground-2": "#131824",
            "--panel": "#161c2a",
            "--panel-glass": "rgba(22,28,42,0.88)",
            "--panel-glass-light": "rgba(28,34,50,0.65)",
            "--panel-glass-deep": "rgba(14,18,28,0.92)",
            "--panel-2": "#1c2336",
            "--line": "rgba(140,160,200,0.12)",
            "--line-glow": "rgba(34,211,238,0.35)",
            "--ink": "#e8ecf5",
            "--ink-bright": "#ffffff",
            "--muted": "#8a94ab",
            "--muted-2": "#5a6478",
            "--accent": "#22d3ee",
            "--accent-2": "#7c3aed",
            "--accent-3": "#f472b6",
            "--accent-dim": "#0ea5c4",
            "--accent-glow": "rgba(34,211,238,0.35)",
            "--accent-2-glow": "rgba(124,58,237,0.35)",
            "--accent-3-glow": "rgba(244,114,182,0.3)",
            "--amber": "#fbbf24",
            "--red": "#f87171",
            "--green": "#34d399",
            "--border-glow": "rgba(34,211,238,0.3)",
            "--accent_rgb": "34,211,238",
            "--accent-2_rgb": "124,58,237",
            "--accent-3_rgb": "244,114,182",
            "--amber_rgb": "251,191,36",
            "--red_rgb": "248,113,113",
            "--green_rgb": "52,211,153",
            "--shadow-deep": "rgba(0,0,0,0.45)",
            "--shadow-glow": "rgba(34,211,238,0.12)",
            # 拼色块：左侧栏
            "--side-bg": "#312e81",
            "--side-bg-2": "#37349a",
            "--side-ink": "#ffffff",
            "--side-muted": "rgba(226,232,255,0.62)",
            "--side-line": "rgba(255,255,255,0.14)",
            "--side-active": "rgba(255,255,255,0.14)",
            "--side-accent": "#22d3ee",
            "--top-bg": "#7c3aed",
            "--top-ink": "#ffffff",
        },
        "js": {
            "ground": "#0f1218",
            "ground_texture": "#131824",
            "panel": "#161c2a",
            "panel_rgba": "rgba(22,28,42,0.88)",
            "panel_glass": "rgba(22,28,42,0.6)",
            "line": "rgba(140,160,200,0.12)",
            "ink": "#e8ecf5",
            "muted": "#8a94ab",
            "accent": "#22d3ee",
            "accent_2": "#7c3aed",
            "accent_3": "#f472b6",
            "accent_dim": "#0ea5c4",
            "amber": "#fbbf24",
            "red": "#f87171",
            "deep": "#131824",
            "border": "rgba(34,211,238,0.3)",
            "border_glow": "rgba(34,211,238,0.4)",
            "map_low": "#131824",
            "map_mid": "#1e5a8a",
            "map_high": "#0ea5c4",
            "map_peak": "#22d3ee",
            "ocean": "#0c1626",
            "land": "#123047",
            "china_highlight": "#1e4a6e",
            "star_colors": ["#22d3ee", "#7c3aed", "#ffffff"],
        },
    },

    # ═══════════════════════════════════════════
    # 青碧珊瑚 — 深青侧栏 + 珊瑚橙顶栏
    # ═══════════════════════════════════════════
    "teal_coral": {
        "name": "青碧珊瑚",
        "is_dark": True,
        "css": {
            "--ground": "#0e1516",
            "--ground-2": "#111a1b",
            "--panel": "#142022",
            "--panel-glass": "rgba(20,32,34,0.88)",
            "--panel-glass-light": "rgba(24,38,40,0.65)",
            "--panel-glass-deep": "rgba(12,20,22,0.92)",
            "--panel-2": "#182829",
            "--line": "rgba(130,190,190,0.12)",
            "--line-glow": "rgba(45,212,191,0.35)",
            "--ink": "#e6f2f0",
            "--ink-bright": "#ffffff",
            "--muted": "#8aa5a2",
            "--muted-2": "#5a7472",
            "--accent": "#2dd4bf",
            "--accent-2": "#ea580c",
            "--accent-3": "#fbbf24",
            "--accent-dim": "#14b8a6",
            "--accent-glow": "rgba(45,212,191,0.32)",
            "--accent-2-glow": "rgba(234,88,12,0.35)",
            "--accent-3-glow": "rgba(251,191,36,0.28)",
            "--amber": "#fbbf24",
            "--red": "#f87171",
            "--green": "#34d399",
            "--border-glow": "rgba(45,212,191,0.28)",
            "--accent_rgb": "45,212,191",
            "--accent-2_rgb": "234,88,12",
            "--accent-3_rgb": "251,191,36",
            "--amber_rgb": "251,191,36",
            "--red_rgb": "248,113,113",
            "--green_rgb": "52,211,153",
            "--shadow-deep": "rgba(0,0,0,0.45)",
            "--shadow-glow": "rgba(45,212,191,0.1)",
            "--side-bg": "#115e59",
            "--side-bg-2": "#146c66",
            "--side-ink": "#ffffff",
            "--side-muted": "rgba(220,255,250,0.6)",
            "--side-line": "rgba(255,255,255,0.14)",
            "--side-active": "rgba(255,255,255,0.14)",
            "--side-accent": "#2dd4bf",
            "--top-bg": "#ea580c",
            "--top-ink": "#ffffff",
        },
        "js": {
            "ground": "#0e1516",
            "ground_texture": "#111a1b",
            "panel": "#142022",
            "panel_rgba": "rgba(20,32,34,0.88)",
            "panel_glass": "rgba(20,32,34,0.6)",
            "line": "rgba(130,190,190,0.12)",
            "ink": "#e6f2f0",
            "muted": "#8aa5a2",
            "accent": "#2dd4bf",
            "accent_2": "#ea580c",
            "accent_3": "#fbbf24",
            "accent_dim": "#14b8a6",
            "amber": "#fbbf24",
            "red": "#f87171",
            "deep": "#111a1b",
            "border": "rgba(45,212,191,0.28)",
            "border_glow": "rgba(45,212,191,0.38)",
            "map_low": "#111a1b",
            "map_mid": "#1a6b60",
            "map_high": "#14b8a6",
            "map_peak": "#2dd4bf",
            "ocean": "#0a1a1a",
            "land": "#12302c",
            "china_highlight": "#1a4a44",
            "star_colors": ["#2dd4bf", "#ea580c", "#ffffff"],
        },
    },

    # ═══════════════════════════════════════════
    # 苔绿青柠 — 苔绿侧栏 + 青柠顶栏
    # ═══════════════════════════════════════════
    "moss_lime": {
        "name": "苔绿青柠",
        "is_dark": True,
        "css": {
            "--ground": "#0f1410",
            "--ground-2": "#131a14",
            "--panel": "#161f17",
            "--panel-glass": "rgba(22,31,23,0.88)",
            "--panel-glass-light": "rgba(26,37,28,0.65)",
            "--panel-glass-deep": "rgba(13,19,14,0.92)",
            "--panel-2": "#1a261b",
            "--line": "rgba(150,200,160,0.12)",
            "--line-glow": "rgba(74,222,128,0.32)",
            "--ink": "#e9f2ea",
            "--ink-bright": "#ffffff",
            "--muted": "#8fa694",
            "--muted-2": "#5f7564",
            "--accent": "#4ade80",
            "--accent-2": "#65a30d",
            "--accent-3": "#a3e635",
            "--accent-dim": "#22c55e",
            "--accent-glow": "rgba(74,222,128,0.3)",
            "--accent-2-glow": "rgba(101,163,13,0.35)",
            "--accent-3-glow": "rgba(163,230,53,0.28)",
            "--amber": "#fbbf24",
            "--red": "#f87171",
            "--green": "#4ade80",
            "--border-glow": "rgba(74,222,128,0.26)",
            "--accent_rgb": "74,222,128",
            "--accent-2_rgb": "101,163,13",
            "--accent-3_rgb": "163,230,53",
            "--amber_rgb": "251,191,36",
            "--red_rgb": "248,113,113",
            "--green_rgb": "74,222,128",
            "--shadow-deep": "rgba(0,0,0,0.45)",
            "--shadow-glow": "rgba(74,222,128,0.1)",
            "--side-bg": "#166534",
            "--side-bg-2": "#17743b",
            "--side-ink": "#ffffff",
            "--side-muted": "rgba(220,255,230,0.6)",
            "--side-line": "rgba(255,255,255,0.14)",
            "--side-active": "rgba(255,255,255,0.14)",
            "--side-accent": "#4ade80",
            "--top-bg": "#65a30d",
            "--top-ink": "#ffffff",
        },
        "js": {
            "ground": "#0f1410",
            "ground_texture": "#131a14",
            "panel": "#161f17",
            "panel_rgba": "rgba(22,31,23,0.88)",
            "panel_glass": "rgba(22,31,23,0.6)",
            "line": "rgba(150,200,160,0.12)",
            "ink": "#e9f2ea",
            "muted": "#8fa694",
            "accent": "#4ade80",
            "accent_2": "#65a30d",
            "accent_3": "#a3e635",
            "accent_dim": "#22c55e",
            "amber": "#fbbf24",
            "red": "#f87171",
            "deep": "#131a14",
            "border": "rgba(74,222,128,0.26)",
            "border_glow": "rgba(74,222,128,0.36)",
            "map_low": "#131a14",
            "map_mid": "#2a6b3a",
            "map_high": "#22c55e",
            "map_peak": "#4ade80",
            "ocean": "#0a1a10",
            "land": "#12301e",
            "china_highlight": "#1a4a2c",
            "star_colors": ["#4ade80", "#a3e635", "#ffffff"],
        },
    },

    # ═══════════════════════════════════════════
    # 紫檀玫红 — 紫檀侧栏 + 玫红顶栏
    # ═══════════════════════════════════════════
    "plum_rose": {
        "name": "紫檀玫红",
        "is_dark": True,
        "css": {
            "--ground": "#15111a",
            "--ground-2": "#1a1520",
            "--panel": "#1e1826",
            "--panel-glass": "rgba(30,24,38,0.88)",
            "--panel-glass-light": "rgba(36,28,46,0.65)",
            "--panel-glass-deep": "rgba(18,14,24,0.92)",
            "--panel-2": "#241d2e",
            "--line": "rgba(190,150,220,0.12)",
            "--line-glow": "rgba(192,132,252,0.32)",
            "--ink": "#efe8f5",
            "--ink-bright": "#ffffff",
            "--muted": "#a294b5",
            "--muted-2": "#6f6180",
            "--accent": "#c084fc",
            "--accent-2": "#e11d48",
            "--accent-3": "#f9a8d4",
            "--accent-dim": "#a855f7",
            "--accent-glow": "rgba(192,132,252,0.32)",
            "--accent-2-glow": "rgba(225,29,72,0.35)",
            "--accent-3-glow": "rgba(249,168,212,0.28)",
            "--amber": "#fbbf24",
            "--red": "#fb7185",
            "--green": "#34d399",
            "--border-glow": "rgba(192,132,252,0.26)",
            "--accent_rgb": "192,132,252",
            "--accent-2_rgb": "225,29,72",
            "--accent-3_rgb": "249,168,212",
            "--amber_rgb": "251,191,36",
            "--red_rgb": "251,113,133",
            "--green_rgb": "52,211,153",
            "--shadow-deep": "rgba(0,0,0,0.45)",
            "--shadow-glow": "rgba(192,132,252,0.1)",
            "--side-bg": "#6b21a8",
            "--side-bg-2": "#7a28bd",
            "--side-ink": "#ffffff",
            "--side-muted": "rgba(245,230,255,0.6)",
            "--side-line": "rgba(255,255,255,0.14)",
            "--side-active": "rgba(255,255,255,0.14)",
            "--side-accent": "#c084fc",
            "--top-bg": "#e11d48",
            "--top-ink": "#ffffff",
        },
        "js": {
            "ground": "#15111a",
            "ground_texture": "#1a1520",
            "panel": "#1e1826",
            "panel_rgba": "rgba(30,24,38,0.88)",
            "panel_glass": "rgba(30,24,38,0.6)",
            "line": "rgba(190,150,220,0.12)",
            "ink": "#efe8f5",
            "muted": "#a294b5",
            "accent": "#c084fc",
            "accent_2": "#e11d48",
            "accent_3": "#f9a8d4",
            "accent_dim": "#a855f7",
            "amber": "#fbbf24",
            "red": "#fb7185",
            "deep": "#1a1520",
            "border": "rgba(192,132,252,0.26)",
            "border_glow": "rgba(192,132,252,0.36)",
            "map_low": "#1a1520",
            "map_mid": "#5a2a7a",
            "map_high": "#a855f7",
            "map_peak": "#c084fc",
            "ocean": "#160e22",
            "land": "#2a1a3e",
            "china_highlight": "#3e2a5a",
            "star_colors": ["#c084fc", "#e11d48", "#ffffff"],
        },
    },

    # ═══════════════════════════════════════════
    # 石墨鎏金 — 石墨侧栏 + 琥珀顶栏
    # ═══════════════════════════════════════════
    "slate_amber": {
        "name": "石墨鎏金",
        "is_dark": True,
        "css": {
            "--ground": "#14161a",
            "--ground-2": "#181b20",
            "--panel": "#1c1f26",
            "--panel-glass": "rgba(28,31,38,0.88)",
            "--panel-glass-light": "rgba(34,38,46,0.65)",
            "--panel-glass-deep": "rgba(16,18,22,0.92)",
            "--panel-2": "#22262e",
            "--line": "rgba(180,190,210,0.12)",
            "--line-glow": "rgba(251,191,36,0.32)",
            "--ink": "#eaecf0",
            "--ink-bright": "#ffffff",
            "--muted": "#98a0ad",
            "--muted-2": "#666e7a",
            "--accent": "#fbbf24",
            "--accent-2": "#d97706",
            "--accent-3": "#fde68a",
            "--accent-dim": "#d99e0b",
            "--accent-glow": "rgba(251,191,36,0.3)",
            "--accent-2-glow": "rgba(217,119,6,0.35)",
            "--accent-3-glow": "rgba(253,230,138,0.28)",
            "--amber": "#fbbf24",
            "--red": "#f87171",
            "--green": "#34d399",
            "--border-glow": "rgba(251,191,36,0.26)",
            "--accent_rgb": "251,191,36",
            "--accent-2_rgb": "217,119,6",
            "--accent-3_rgb": "253,230,138",
            "--amber_rgb": "251,191,36",
            "--red_rgb": "248,113,113",
            "--green_rgb": "52,211,153",
            "--shadow-deep": "rgba(0,0,0,0.45)",
            "--shadow-glow": "rgba(251,191,36,0.1)",
            "--side-bg": "#334155",
            "--side-bg-2": "#3b4c63",
            "--side-ink": "#ffffff",
            "--side-muted": "rgba(226,232,240,0.6)",
            "--side-line": "rgba(255,255,255,0.14)",
            "--side-active": "rgba(255,255,255,0.14)",
            "--side-accent": "#fbbf24",
            "--top-bg": "#d97706",
            "--top-ink": "#ffffff",
        },
        "js": {
            "ground": "#14161a",
            "ground_texture": "#181b20",
            "panel": "#1c1f26",
            "panel_rgba": "rgba(28,31,38,0.88)",
            "panel_glass": "rgba(28,31,38,0.6)",
            "line": "rgba(180,190,210,0.12)",
            "ink": "#eaecf0",
            "muted": "#98a0ad",
            "accent": "#fbbf24",
            "accent_2": "#d97706",
            "accent_3": "#fde68a",
            "accent_dim": "#d99e0b",
            "amber": "#fbbf24",
            "red": "#f87171",
            "deep": "#181b20",
            "border": "rgba(251,191,36,0.26)",
            "border_glow": "rgba(251,191,36,0.36)",
            "map_low": "#181b20",
            "map_mid": "#6b5a1a",
            "map_high": "#d99e0b",
            "map_peak": "#fbbf24",
            "ocean": "#1a1c22",
            "land": "#2e3140",
            "china_highlight": "#444a5e",
            "star_colors": ["#fbbf24", "#fde68a", "#ffffff"],
        },
    },

    # ═══════════════════════════════════════════
    # 深海天青 — 海军蓝侧栏 + 天青顶栏
    # ═══════════════════════════════════════════
    "navy_sky": {
        "name": "深海天青",
        "is_dark": True,
        "css": {
            "--ground": "#0a1220",
            "--ground-2": "#0d1626",
            "--panel": "#101b30",
            "--panel-glass": "rgba(16,27,48,0.88)",
            "--panel-glass-light": "rgba(20,32,56,0.65)",
            "--panel-glass-deep": "rgba(9,15,28,0.92)",
            "--panel-2": "#15223c",
            "--line": "rgba(130,170,230,0.12)",
            "--line-glow": "rgba(56,189,248,0.32)",
            "--ink": "#e6eefb",
            "--ink-bright": "#ffffff",
            "--muted": "#8598b8",
            "--muted-2": "#576a88",
            "--accent": "#38bdf8",
            "--accent-2": "#0284c7",
            "--accent-3": "#7dd3fc",
            "--accent-dim": "#0ea5e9",
            "--accent-glow": "rgba(56,189,248,0.32)",
            "--accent-2-glow": "rgba(2,132,199,0.35)",
            "--accent-3-glow": "rgba(125,211,252,0.28)",
            "--amber": "#fbbf24",
            "--red": "#f87171",
            "--green": "#34d399",
            "--border-glow": "rgba(56,189,248,0.26)",
            "--accent_rgb": "56,189,248",
            "--accent-2_rgb": "2,132,199",
            "--accent-3_rgb": "125,211,252",
            "--amber_rgb": "251,191,36",
            "--red_rgb": "248,113,113",
            "--green_rgb": "52,211,153",
            "--shadow-deep": "rgba(0,0,0,0.45)",
            "--shadow-glow": "rgba(56,189,248,0.1)",
            "--side-bg": "#1e40af",
            "--side-bg-2": "#2349c2",
            "--side-ink": "#ffffff",
            "--side-muted": "rgba(219,234,254,0.6)",
            "--side-line": "rgba(255,255,255,0.14)",
            "--side-active": "rgba(255,255,255,0.14)",
            "--side-accent": "#38bdf8",
            "--top-bg": "#0284c7",
            "--top-ink": "#ffffff",
        },
        "js": {
            "ground": "#0a1220",
            "ground_texture": "#0d1626",
            "panel": "#101b30",
            "panel_rgba": "rgba(16,27,48,0.88)",
            "panel_glass": "rgba(16,27,48,0.6)",
            "line": "rgba(130,170,230,0.12)",
            "ink": "#e6eefb",
            "muted": "#8598b8",
            "accent": "#38bdf8",
            "accent_2": "#0284c7",
            "accent_3": "#7dd3fc",
            "accent_dim": "#0ea5e9",
            "amber": "#fbbf24",
            "red": "#f87171",
            "deep": "#0d1626",
            "border": "rgba(56,189,248,0.26)",
            "border_glow": "rgba(56,189,248,0.36)",
            "map_low": "#0d1626",
            "map_mid": "#1a4a7a",
            "map_high": "#0ea5e9",
            "map_peak": "#38bdf8",
            "ocean": "#081428",
            "land": "#0e2440",
            "china_highlight": "#16365c",
            "star_colors": ["#38bdf8", "#7dd3fc", "#ffffff"],
        },
    },
}


def get_theme(theme_id: str) -> dict[str, Any]:
    """获取指定主题的完整配置。"""
    return THEMES.get(theme_id, THEMES["indigo_violet"])


def get_theme_css(theme_id: str) -> str:
    """生成主题的 CSS 变量字符串，用于注入 :root。"""
    theme = get_theme(theme_id)
    css_vars = theme["css"]
    lines = [f"  {key}: {value};" for key, value in css_vars.items()]
    return ":root {\n" + "\n".join(lines) + "\n}"


def get_theme_js_colors(theme_id: str) -> dict[str, str]:
    """获取主题的 JS 颜色字典，用于 ECharts/Three.js 配置。"""
    theme = get_theme(theme_id)
    return theme["js"]


def theme_options() -> list[dict[str, str]]:
    """获取主题选择器选项列表。"""
    return [
        {"id": tid, "label": THEME_LABELS[tid], "description": THEME_DESCRIPTIONS[tid]}
        for tid in THEME_ORDER
    ]


def default_theme() -> str:
    """默认主题 ID。"""
    return "indigo_violet"
