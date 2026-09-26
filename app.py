from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

# 把项目根目录（本文件所在目录）加到 sys.path 最前，
# 让同级包（storage、rules 等）在 streamlit run 时也能被找到。
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backup import backup_database
from branding import BRAND_EYEBROW, BRAND_NAME, BRAND_TAGLINE, load_logo_svg
from admin_fields_view import render as render_fields_review
from admin_relations_view import render as render_relations_review
from collector_ops_view import render as render_collector_ops
from compare_view import render as render_compare_workbench
from evolution_view import render as render_evolution_dashboard
from subscription_view import render as render_subscriptions
from graph_service import graph_summary
from home_dashboard import render_home
from map_dashboard import render_china_map
from rules.engine import match_announcements
from storage.db import Database
from storage.models import SourceSyncState
from theme_config import THEME_LABELS, THEME_ORDER, THEME_SWATCHES, default_theme, get_theme_css

st.set_page_config(page_title=f"{BRAND_NAME} · 运营工作台", layout="wide", initial_sidebar_state="collapsed")

CATEGORY_OPTIONS = [
    "新闻动态",
    "财政金融",
    "交通运输",
    "农业农村",
    "商务贸易",
    "法治司法",
    "工业制造",
    "医疗保障",
    "住房保障",
    "科技创新",
    "能源资源",
    "市场监管",
    "民政社会",
    "应急安全",
    "税收海关",
    "国防军队",
    "行业政策",
    "行业补贴",
    "公务员考试",
    "军队招录",
    "公招信息",
    "招标信息",
    "商业机会",
    "招聘信息",
    "活动通知",
    "人才政策",
    "养老与社区",
    "招生信息",
    "其他",
]
LEVEL_OPTIONS = ["国家部委", "省级", "市级", "县级"]
INDUSTRY_OPTIONS = ["新能源", "人工智能", "高端制造", "数字经济", "教育与就业"]


# 分组：侧栏按职能分段展示，13 个模块全部可点选。NAV_VIEWS 的顺序必须与
# NAV_GROUPS 一致，否则组标题会插在错误的位置。
NAV_GROUPS = [
    ("运营视图", ["总览", "中国地图", "热频话题"]),
    ("采集与来源", ["公告检索", "采集运维", "来源健康"]),
    ("政策对比", ["对比工作台", "关联确认", "字段复核"]),
    ("知识与留存", ["知识图谱", "订阅中心", "进化仪表盘", "质量与备份"]),
]
NAV_VIEWS = [view for _name, members in NAV_GROUPS for view in members]

# 侧栏与折叠态的图标：Material Icons 短名，交给 st.button(icon=) 原生渲染。
# 必须带 ":material/" 前缀，否则 Streamlit 抛 StreamlitAPIException 并让整页崩溃
# （曾导致侧栏之后的全部内容不渲染）。图标在按钮盒内，不需要任何覆盖层。
NAV_ICONS = {
    "总览": ":material/dashboard:",
    "中国地图": ":material/public:",
    "热频话题": ":material/local_fire_department:",
    "公告检索": ":material/search:",
    "采集运维": ":material/sync:",
    "来源健康": ":material/monitor_heart:",
    "对比工作台": ":material/compare_arrows:",
    "关联确认": ":material/link:",
    "字段复核": ":material/fact_check:",
    "知识图谱": ":material/hub:",
    "订阅中心": ":material/bookmark:",
    "进化仪表盘": ":material/trending_up:",
    "质量与备份": ":material/verified_user:",
}

# Fail at import rather than mid-render: a bad icon name blanks the whole page,
# because the sidebar renders before the body.
_BAD_ICONS = {v: k for k, v in NAV_ICONS.items() if not (v.startswith(":material/") and v.endswith(":"))}
if _BAD_ICONS:
    raise ValueError(f"图标名必须形如 ':material/name:'，否则整页崩溃: {_BAD_ICONS}")
_MISSING_ICONS = [view for view in NAV_VIEWS if view not in NAV_ICONS]
if _MISSING_ICONS:
    raise ValueError(f"这些视图缺少图标定义: {_MISSING_ICONS}")


def load_rules() -> dict[str, Any]:
    return yaml.safe_load(Path("config/rules.yaml").read_text(encoding="utf-8")) or {}


@st.cache_data(ttl=30, show_spinner=False)
def load_source_config() -> list[dict[str, Any]]:
    data = yaml.safe_load(Path("config/sources.yaml").read_text(encoding="utf-8")) or {}
    return [dict(item) for item in data.get("sources", []) if isinstance(item, dict)]


def as_record(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_id": item.source_id,
        "source_url": item.source_url,
        "title": item.title,
        "publish_date": item.publish_date,
        "issuing_body": (item.extra or {}).get("issuing_body") or item.source_id,
        "gov_level": item.gov_level,
        "region_province": item.region_province,
        "region_city": item.region_city,
        "region_county": item.region_county,
        "category_l1": item.category_l1,
        "category_l2": item.category_l2,
        "amount": item.amount,
        "deadline": item.deadline,
        "target_group": item.target_group,
        "target_industry": item.target_industry,
        "key_points": item.key_points or [],
        "institution_name": item.institution_name,
        "institution_type": item.institution_type,
        "admission_type": item.admission_type,
        "admission_sub_category": item.admission_sub_category,
        "enrollment_count": item.enrollment_count,
        "tuition_info": item.tuition_info,
        "employment_guarantee": item.employment_guarantee,
        "needs_review": item.needs_review,
        "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
        "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
        "focus_tracked": item.focus_tracked,
        "project_tracked": item.project_tracked,
        "industry_focus": item.industry_focus,
        "industry_tags": item.industry_tags or [],
        "guidance_source": item.guidance_source,
        "guidance_year": item.guidance_year,
        "media_type": item.media_type,
        "media_tags": item.media_tags or [],
        "media_cluster_id": item.media_cluster_id,
        "media_heat": item.media_heat,
        "media_terms": item.media_terms or [],
        "content": item.content,
    }


@st.cache_data(ttl=20, show_spinner=False)
def load_snapshot(db_path: str) -> dict[str, Any]:
    with Database(db_path) as database:
        records = [as_record(item) for item in database.list_announcements(limit=1000)]
        with database.session() as session:
            states = {item.source_id: item for item in session.query(SourceSyncState).all()}
    source_config = load_source_config()
    counts = Counter(str(item.get("source_id") or "") for item in records)
    sources = []
    for item in source_config:
        source_id = str(item.get("id") or "")
        state = states.get(source_id)
        status = "pending" if item.get("status") != "enabled" else "idle"
        if state is not None:
            status = state.status or status
        sources.append(
            {
                "source_id": source_id,
                "name": item.get("name") or source_id,
                "domain": item.get("domain") or "",
                "level": item.get("gov_level") or item.get("level") or "",
                "cadence": item.get("cadence_tier") or "daily",
                "status": status,
                "last_success": state.last_success_at.isoformat() if state and state.last_success_at else None,
                "next_due": state.next_due_at.isoformat() if state and state.next_due_at else None,
                "failures": state.consecutive_failures if state else 0,
                "error": state.last_error if state else None,
                "records": counts.get(source_id, 0),
            }
        )
    return {"records": records, "sources": sources, "states": len(states)}


@st.cache_data(ttl=20, show_spinner=False)
def load_quality() -> dict[str, Any]:
    path = Path("data/quality/latest.json")
    if not path.exists():
        return {"date": "暂无数据", "collected": 0, "extraction_success_rate": 0, "matches": 0, "failures": 0, "source_stats": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"date": "暂无数据", "collected": 0, "extraction_success_rate": 0, "matches": 0, "failures": 0, "source_stats": {}}
    return data if isinstance(data, dict) else {}


@st.cache_data(ttl=20, show_spinner=False)
def load_backups() -> list[dict[str, Any]]:
    directory = Path("data/backups")
    if not directory.exists():
        return []
    values = []
    for path in sorted(directory.glob("beacon-*.db"), reverse=True):
        stat = path.stat()
        values.append({"name": path.name, "size": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()})
    return values[:30]


@st.cache_data(ttl=20, show_spinner=False)
def load_heat_topics() -> list[dict[str, Any]]:
    with Database("data/beacon.db") as database:
        return database.list_heat_topics(limit=50)


@st.cache_data(ttl=20, show_spinner=False)
def load_graph_summary() -> dict[str, Any]:
    with Database("data/beacon.db") as database:
        return graph_summary(database)


def inject_styles() -> None:
    theme_id = st.session_state.get("theme", default_theme())
    theme_css = get_theme_css(theme_id)
    active_view = st.session_state.get("active_view", "总览")
    # Material Symbols font for st.button(icon=":material/name:"). Without it the
    # ligature falls back to plain text and buttons read "dashboard总览".
    st.markdown(
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        "<link href='https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded"
        ":opsz,wght,FILL,GRAD@24,400,0,0' rel='stylesheet'>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        {theme_css}

        /* ═══════════════════════════════════════════════
           Agent 工作站 — 全局基础
           ═══════════════════════════════════════════════ */
        * {{ box-sizing:border-box; }}
        html {{ scroll-behavior:smooth; }}
        html {{ scroll-behavior:smooth; }}
        [data-testid="stAppViewContainer"] {{
          background:var(--ground); color:var(--ink); position:relative;
          font-family:'Manrope','SF Pro Display',system-ui,sans-serif;
          min-height:100vh;
        }}
        /* 中性深色底 + 微弱径向光，保持桌面整洁 */
        [data-testid="stAppViewContainer"]::before {{
          content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
          background:
            radial-gradient(ellipse 70% 45% at 12% 0%, var(--shadow-glow), transparent 55%),
            radial-gradient(ellipse 55% 40% at 88% 100%, rgba(var(--accent-2_rgb),0.05), transparent 60%);
        }}

        /* 隐藏 Streamlit 原生 UI */
        [data-testid="stToolbar"], [data-testid="stAppToolbar"],
        [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
        [data-testid="stStatusWidget"], #MainMenu, footer {{
          display:none !important; visibility:hidden !important;
        }}
        [data-testid="stHeader"] {{ display:none !important; }}

        /* 主容器 */
        .block-container {{
          max-width:1600px; padding:.5rem 1.2rem 3rem; position:relative; z-index:1;
        }}
        h1,h2,h3,h4,p,label,button,input,textarea {{
          font-family:'Manrope','SF Pro Display',system-ui,sans-serif;
        }}
        h1 {{ font-size:1.8rem !important; letter-spacing:-.03em; margin:0 !important; font-weight:800; }}
        h2 {{ font-size:1.1rem !important; letter-spacing:-.02em; margin:0 0 .6rem !important; font-weight:700; }}
        h3 {{ font-size:.95rem !important; font-weight:700; }}
        p,label {{ color:var(--muted); }}
        .mono {{ font-family:'DM Mono','JetBrains Mono',monospace; }}

        /* ═══════════════════════════════════════════════
           四层玻璃质感系统 — 立体透明层次
           ═══════════════════════════════════════════════ */
        /* Ultra-Light — 极透浮层（徽标、内嵌标签） */
        .glass-ultra {{
          background:var(--panel-glass-light);
          backdrop-filter:blur(6px) saturate(1.1);
          -webkit-backdrop-filter:blur(6px) saturate(1.1);
          border:1px solid var(--line); border-radius:.5rem;
          box-shadow:0 1px 4px var(--shadow-deep);
        }}
        /* Light — 轻透浮层（筛选条、操作条） */
        .glass-light {{
          background:var(--panel-glass-light);
          backdrop-filter:blur(10px) saturate(1.2);
          -webkit-backdrop-filter:blur(10px) saturate(1.2);
          border:1px solid var(--line); border-radius:.6rem;
          box-shadow:0 2px 10px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.02);
        }}
        /* Medium — 标准玻璃卡（KPI、记录卡、面板） */
        .glass {{
          background:var(--panel-glass);
          backdrop-filter:blur(16px) saturate(1.35);
          -webkit-backdrop-filter:blur(16px) saturate(1.35);
          border:1px solid var(--line); border-radius:.8rem;
          box-shadow:
            0 4px 24px var(--shadow-deep),
            0 1px 0 rgba(255,255,255,0.03) inset,
            0 -1px 0 rgba(0,0,0,0.2) inset;
          transition:all 0.35s cubic-bezier(0.4,0,0.2,1);
        }}
        .glass:hover {{
          border-color:var(--line-glow);
          box-shadow:
            0 10px 40px var(--shadow-deep),
            0 0 20px var(--shadow-glow),
            0 1px 0 rgba(255,255,255,0.06) inset,
            0 -1px 0 rgba(0,0,0,0.2) inset;
          transform:translateY(-3px);
        }}
        /* Deep — 深沉底座（顶栏、侧栏容器） */
        .glass-deep {{
          background:var(--panel-glass-deep);
          backdrop-filter:blur(24px) saturate(1.5);
          -webkit-backdrop-filter:blur(24px) saturate(1.5);
          border:1px solid var(--line); border-radius:.9rem;
          box-shadow:
            0 8px 36px var(--shadow-deep),
            0 1px 0 rgba(255,255,255,0.04) inset,
            0 -1px 0 rgba(0,0,0,0.3) inset;
        }}

        /* ═══════════════════════════════════════════════
           3D 数据卡片系统 — 透视立体质感
           ═══════════════════════════════════════════════ */
        .card-3d {{
          background:var(--panel-glass);
          backdrop-filter:blur(16px) saturate(1.35);
          -webkit-backdrop-filter:blur(16px) saturate(1.35);
          border:1px solid var(--line); border-radius:.8rem;
          box-shadow:
            0 4px 24px var(--shadow-deep),
            0 1px 0 rgba(255,255,255,0.03) inset,
            0 -1px 0 rgba(0,0,0,0.2) inset;
          transition:all 0.4s cubic-bezier(0.34,1.56,0.64,1);
          position:relative; overflow:hidden;
          transform-style:preserve-3d;
        }}
        /* 卡片顶部渐变光带 */
        .card-3d::before {{
          content:''; position:absolute; top:0; left:0; right:0; height:2px;
          background:linear-gradient(90deg, var(--accent), var(--accent-2), transparent);
          opacity:0.5; transition:all 0.3s;
        }}
        /* 卡片内发光（悬浮时） */
        .card-3d::after {{
          content:''; position:absolute; inset:0; border-radius:inherit;
          background:radial-gradient(circle at 50% 0%, rgba(var(--accent_rgb),0.08), transparent 60%);
          opacity:0; transition:opacity 0.3s; pointer-events:none;
        }}
        .card-3d:hover {{
          border-color:var(--line-glow);
          box-shadow:
            0 14px 48px var(--shadow-deep),
            0 0 24px var(--shadow-glow),
            0 1px 0 rgba(255,255,255,0.06) inset,
            0 -1px 0 rgba(0,0,0,0.2) inset;
          transform:translateY(-4px) scale(1.01);
        }}
        .card-3d:hover::before {{ opacity:0.9; height:3px; }}
        .card-3d:hover::after {{ opacity:1; }}

        /* 卡片式菜单 — 模块入口卡片 */
        .module-card {{
          background:var(--panel-glass);
          backdrop-filter:blur(14px) saturate(1.3);
          -webkit-backdrop-filter:blur(14px) saturate(1.3);
          border:1px solid var(--line); border-radius:.8rem;
          padding:1rem 1.1rem; cursor:pointer;
          transition:all 0.4s cubic-bezier(0.34,1.56,0.64,1);
          box-shadow:0 4px 20px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.03);
          position:relative; overflow:hidden;
          display:flex; flex-direction:column; gap:.5rem;
          min-height:7rem;
        }}
        .module-card::before {{
          content:''; position:absolute; top:0; left:0; right:0; height:2px;
          background:linear-gradient(90deg, var(--accent), var(--accent-2));
          opacity:0.4; transition:all 0.3s;
        }}
        .module-card::after {{
          content:''; position:absolute; inset:0; border-radius:inherit;
          background:radial-gradient(circle at 30% 0%, rgba(var(--accent_rgb),0.06), transparent 50%);
          opacity:0; transition:opacity 0.3s; pointer-events:none;
        }}
        .module-card:hover {{
          border-color:var(--line-glow);
          box-shadow:0 12px 40px var(--shadow-deep), 0 0 20px var(--shadow-glow), inset 0 1px 0 rgba(255,255,255,0.05);
          transform:translateY(-4px) scale(1.02);
        }}
        .module-card:hover::before {{ opacity:0.9; height:3px; }}
        .module-card:hover::after {{ opacity:1; }}
        .module-card .module-icon {{
          width:2.2rem; height:2.2rem; border:1px solid var(--line-glow); border-radius:.55rem;
          display:grid; place-items:center; color:var(--accent);
          box-shadow:0 0 12px var(--accent-glow), 0 2px 8px var(--shadow-deep);
          font-size:1rem;
        }}
        .module-card .module-title {{ color:var(--ink-bright); font-weight:700; font-size:.9rem; }}
        .module-card .module-desc {{ color:var(--muted); font-size:.72rem; line-height:1.4; }}
        .module-card .module-stat {{
          display:flex; align-items:center; gap:.4rem; margin-top:auto;
          color:var(--accent); font-size:.7rem; font-weight:600;
        }}

        /* ═══════════════════════════════════════════════
           顶部命令栏
           ═══════════════════════════════════════════════ */
        .cmd-bar {{
          display:flex; justify-content:space-between; align-items:center; gap:1rem;
          padding:.6rem 1.1rem; margin-bottom:.8rem;
          background:var(--panel-glass-deep);
          backdrop-filter:blur(24px) saturate(1.5);
          -webkit-backdrop-filter:blur(24px) saturate(1.5);
          border:1px solid var(--line); border-radius:.8rem;
          box-shadow:0 6px 28px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.04), inset 0 -1px 0 rgba(0,0,0,0.3);
          position:relative;
        }}
        .cmd-bar::after {{
          content:''; position:absolute; left:1rem; right:1rem; bottom:0; height:1px;
          background:linear-gradient(90deg, transparent, var(--accent), var(--accent-2), transparent);
          opacity:0.5;
        }}
        .cmd-brand {{ display:flex; align-items:center; gap:.7rem; }}
        .cmd-brand .brand-mark {{
          width:2.2rem; height:2.2rem; border:1px solid var(--accent); border-radius:.55rem;
          display:grid; place-items:center; color:var(--accent);
          box-shadow:0 0 12px var(--accent-glow), 0 2px 8px var(--shadow-deep);
          background:rgba(var(--accent_rgb),0.05);
        }}
        .cmd-brand .brand-mark svg {{ width:60%; height:60%; display:block; }}
        .cmd-brand .brand-name {{ color:var(--ink-bright); font-weight:800; font-size:.95rem; }}
        .cmd-brand .brand-sub {{ color:var(--muted); font-size:.68rem; }}
        .cmd-status {{ display:flex; align-items:center; gap:.8rem; }}
        .cmd-status .badge {{ font-size:.68rem; }}
        .cmd-clock {{
          font-family:'DM Mono','JetBrains Mono',monospace;
          color:var(--accent); font-size:.75rem; font-weight:600;
          text-shadow:0 0 6px var(--accent-glow);
        }}

        /* ═══════════════════════════════════════════════
           统一工具带（顶栏）— 全宽一条，工具元素一个整体
           ═══════════════════════════════════════════════ */
        .topbar {{
          display:flex; justify-content:space-between; align-items:center; gap:1rem;
          padding:.55rem 1rem; margin-bottom:.6rem;
          background:var(--panel-glass-deep);
          backdrop-filter:blur(18px) saturate(1.3);
          -webkit-backdrop-filter:blur(18px) saturate(1.3);
          border:1px solid var(--line); border-radius:.65rem;
          box-shadow:0 4px 18px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.03);
        }}
        .topbar-left {{ display:flex; align-items:center; min-width:0; }}
        .topbar-crumb {{ color:var(--muted); font-size:.76rem; font-weight:600; white-space:nowrap; }}
        .topbar-sep {{ width:1px; height:.85rem; background:var(--line); display:inline-block; margin:0 .75rem; }}
        .topbar-page {{ color:var(--ink-bright); font-size:.98rem; font-weight:800; letter-spacing:.02em; white-space:nowrap; }}
        .topbar-right {{ display:flex; align-items:center; gap:.7rem; flex-shrink:0; }}
        .topbar-note {{ color:var(--muted); font-size:.68rem; }}

        /* ═══════════════════════════════════════════════
           控制轨（常驻窄列）— 菜单/右栏 折叠开关 + 个人中心入口
           ═══════════════════════════════════════════════ */
/* ═══════════════════════════════════════════════
           左侧栏 — 拼色实色块（品牌 + 菜单 + 个人中心）
           ═══════════════════════════════════════════════ */
        /* 左侧功能栏。样式挂在 .st-key-side 容器上，而不是一个只包住品牌区的
           空 div —— 之前 .side-panel 自带 min-height:100vh，而菜单是它的兄弟
           节点，于是 800px 高的空壳把菜单顶出视口，用户只能看到空框。 */
        .st-key-side, .st-key-aux,.st-key-side::-webkit-scrollbar, .st-key-aux::-webkit-scrollbar,
.st-key-side::-webkit-scrollbar-thumb, .st-key-aux::-webkit-scrollbar-thumb,
.side-group {{
          color:var(--muted); font-size:.62rem; letter-spacing:.12em; font-weight:700;
          text-transform:uppercase; margin:.5rem 0 .15rem .3rem;
        }}
        .side-stats {{ display:flex; flex-direction:column; }}
        .nav-group {{
          display:flex; align-items:center; justify-content:space-between;
          margin:.55rem .3rem .2rem; padding-bottom:.2rem;
          border-bottom:1px dashed var(--line);
        }}
        .nav-group-name {{ color:var(--muted); font-size:.66rem; font-weight:700; letter-spacing:.06em; }}
        .nav-group-count {{
          font-size:.6rem; color:var(--accent); border:1px solid rgba(var(--accent_rgb),.35);
          border-radius:999px; padding:0 .4rem; line-height:1.3;
        }}
        /* st.button has a native icon= parameter (Material Icons). The glyph font is
           loaded from Google's CDN below; without it the ligature renders as the
           literal text "dashboard总览". */
        .st-key-side [data-testid="stButton"] button {{
          justify-content:flex-start; text-align:left; font-size:.78rem;
          padding:.3rem .55rem; min-height:2.1rem; width:100%;
        }}
        .st-key-side [data-testid="stIconMaterial"] {{
          font-family:'Material Symbols Rounded','Material Icons',sans-serif;
          font-size:1.05rem; margin-right:.45rem; line-height:1;
          direction:ltr; display:inline-block; white-space:nowrap;
          word-wrap:normal; font-weight:normal; font-style:normal;
          font-feature-settings:'liga'; -webkit-font-smoothing:antialiased;
        }}
.side-panel {{
          background:var(--side-bg);
          border:1px solid rgba(255,255,255,0.08); border-radius:.65rem;
          padding:.75rem .65rem .7rem;
          display:block;
        }}
        .side-brand {{
          display:flex; align-items:center; gap:.55rem;
          padding:.15rem .25rem .6rem;
          border-bottom:1px solid var(--side-line); margin-bottom:.55rem;
        }}
        .side-brand .brand-mark {{
          width:1.9rem; height:1.9rem; border-radius:.45rem;
          background:rgba(255,255,255,0.16); display:grid; place-items:center; overflow:hidden;
        }}
        .side-brand .brand-mark svg {{ width:60%; height:60%; display:block; }}
        .side-title {{ color:var(--side-ink); font-weight:800; font-size:.9rem; letter-spacing:.02em; line-height:1.25; }}
        .side-sub {{ color:var(--side-muted); font-size:.62rem; }}
        .side-online {{
          display:flex; align-items:center; gap:.4rem;
          padding:.1rem .3rem .55rem; color:var(--side-muted); font-size:.66rem;
        }}
        .side-online .pulse-dot {{ background:var(--side-ink); box-shadow:0 0 0 .18rem rgba(255,255,255,0.14); }}

        /* 导航（radio）— 色块上的扁平菜单项，隐藏圆点 */
        .side-panel [data-testid="stRadio"] > div {{ background:transparent; }}
        .side-panel [data-testid="stRadio"] label > div:first-child {{ display:none; }}
        .side-panel [data-testid="stRadio"] label {{
          border:none; background:transparent; box-shadow:none;
          color:var(--side-muted); padding:.45rem .65rem; border-radius:.45rem;
          font-size:.83rem; font-weight:600; margin:.1rem 0;
          position:relative; overflow:hidden;
          transition:all 0.2s cubic-bezier(0.4,0,0.2,1);
        }}
        .side-panel [data-testid="stRadio"] label::before {{
          content:''; position:absolute; left:0; top:22%; bottom:22%; width:3px;
          border-radius:2px; background:transparent; opacity:1;
        }}
        .side-panel [data-testid="stRadio"] label[data-checked="true"] {{
          background:var(--side-active); color:var(--side-ink);
          font-weight:800; text-shadow:none; border:none;
          box-shadow:inset 0 1px 0 rgba(255,255,255,0.06);
        }}
        .side-panel [data-testid="stRadio"] label[data-checked="true"]::before {{ background:var(--side-ink); opacity:.9; }}
        .side-panel [data-testid="stRadio"] label:hover {{
          background:rgba(255,255,255,0.08); color:var(--side-ink);
          transform:none; box-shadow:none; border:none;
        }}

        /* 个人中心 — 色块底部 */
        .user-card {{ margin-top:auto; padding-top:.55rem; border-top:1px solid var(--side-line); }}
        .user-row {{ display:flex; align-items:center; gap:.5rem; padding:.25rem .2rem .55rem; }}
        .user-avatar {{
          width:2rem; height:2rem; border-radius:50%;
          background:rgba(255,255,255,0.18); color:var(--side-ink);
          display:grid; place-items:center; font-weight:800; font-size:.82rem;
        }}
        .user-name {{ color:var(--side-ink); font-size:.8rem; font-weight:700; line-height:1.3; }}
        .user-role {{ color:var(--side-muted); font-size:.62rem; }}
        .user-card .stButton > button {{
          background:rgba(255,255,255,0.1); border:1px solid var(--side-line);
          color:var(--side-ink); font-size:.72rem; height:1.9rem;
        }}
        .user-card .stButton > button:hover {{
          background:rgba(255,255,255,0.2); border-color:rgba(255,255,255,0.32);
          color:#ffffff; box-shadow:none; transform:none; text-shadow:none;
        }}

        /* ═══════════════════════════════════════════════
           右侧栏 — 备用功能区（主题 / 状态 / 快捷操作 / 筛选）
           ═══════════════════════════════════════════════ */
        .aux-panel {{
          background:var(--panel-glass-deep);
          backdrop-filter:blur(20px) saturate(1.35);
          -webkit-backdrop-filter:blur(20px) saturate(1.35);
          border:1px solid var(--line); border-radius:.65rem;
          padding:.75rem .65rem .7rem;
          box-shadow:0 6px 24px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.03);
          display:block;
        }}
        .aux-title {{
          color:var(--ink-bright); font-size:.8rem; font-weight:800;
          padding:.1rem .2rem .5rem; border-bottom:1px solid var(--line); margin-bottom:.55rem;
        }}
        .aux-label {{ color:var(--muted); font-size:.66rem; font-weight:700; margin:.55rem 0 .35rem; }}
        .aux-panel [data-testid="stRadio"] label > div:first-child {{ display:none; }}
        .aux-panel [data-testid="stRadio"] label {{
          border:1px solid transparent; background:transparent; box-shadow:none;
          color:var(--muted); padding:.3rem .5rem; border-radius:.4rem;
          font-size:.75rem; font-weight:600; margin:.05rem 0;
          transition:all 0.2s;
        }}
        .aux-panel [data-testid="stRadio"] label[data-checked="true"] {{
          border-color:var(--line-glow); background:rgba(var(--accent_rgb),0.08);
          color:var(--ink-bright); text-shadow:none; box-shadow:none;
        }}
        .aux-panel [data-testid="stRadio"] label:hover {{
          background:rgba(var(--accent_rgb),0.05); transform:none; box-shadow:none;
        }}
        .aux-panel .stExpander > details {{ background:var(--panel-glass-light) !important; }}

        /* 主题拼色预览块 */
        .theme-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:.35rem; margin-bottom:.3rem; }}
        .theme-chip {{
          display:flex; align-items:center; gap:.35rem;
          padding:.28rem .4rem; border:1px solid var(--line); border-radius:.45rem;
          background:var(--panel-glass-light);
        }}
        .theme-chip .chip-side {{ width:.62rem; height:.62rem; border-radius:2px; }}
        .theme-chip .chip-top {{ width:.62rem; height:.62rem; border-radius:2px; margin-left:-.3rem; }}
        .theme-chip .chip-name {{ color:var(--muted); font-size:.66rem; white-space:nowrap; }}

        /* 辅助面板小状态 */
        .aux-stat {{
          display:flex; justify-content:space-between; align-items:center;
          padding:.28rem .2rem; font-size:.7rem; color:var(--muted);
          border-bottom:1px dashed var(--line);
        }}
        .aux-stat:last-child {{ border-bottom:none; }}
        .aux-stat b {{ color:var(--accent); font-weight:700; }}

        /* ═══════════════════════════════════════════════
           KPI 指标卡
           ═══════════════════════════════════════════════ */
        .metric-row {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.7rem; margin-bottom:.8rem; }}
        .metric {{
          background:var(--panel-glass);
          backdrop-filter:blur(16px) saturate(1.35);
          -webkit-backdrop-filter:blur(16px) saturate(1.35);
          border:1px solid var(--line); border-radius:.75rem; padding:.9rem 1rem; min-height:5.8rem;
          transition:all 0.35s cubic-bezier(0.34,1.56,0.64,1);
          box-shadow:0 4px 20px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.03), inset 0 -1px 0 rgba(0,0,0,0.2);
          position:relative; overflow:hidden;
        }}
        .metric::before {{
          content:''; position:absolute; top:0; left:0; right:0; height:2px;
          background:linear-gradient(90deg, var(--accent), var(--accent-2));
          opacity:0.5; transition:all 0.3s;
        }}
        .metric::after {{
          content:''; position:absolute; inset:0; border-radius:inherit;
          background:radial-gradient(circle at 50% 0%, rgba(var(--accent_rgb),0.05), transparent 60%);
          opacity:0; transition:opacity 0.3s; pointer-events:none;
        }}
        .metric:hover {{
          border-color:var(--line-glow);
          box-shadow:0 10px 32px var(--shadow-deep), 0 0 16px var(--shadow-glow), inset 0 1px 0 rgba(255,255,255,0.05);
          transform:translateY(-3px) scale(1.01);
        }}
        .metric:hover::before {{ opacity:0.9; height:3px; }}
        .metric:hover::after {{ opacity:1; }}
        .metric-label {{ color:var(--muted); font-size:.72rem; font-weight:600; }}
        .metric-value {{
          color:var(--ink-bright); font-size:1.6rem; font-weight:800; letter-spacing:-.03em;
          margin-top:.3rem; text-shadow:0 0 12px var(--accent-glow);
          font-family:'DM Mono','JetBrains Mono',monospace;
        }}
        .metric-note {{ color:var(--muted-2); font-size:.68rem; margin-top:.2rem; }}
        /* 迷你进度条 */
        .metric-bar {{
          height:3px; border-radius:2px; margin-top:.5rem;
          background:rgba(var(--accent_rgb),0.1); overflow:hidden;
        }}
        .metric-bar-fill {{
          height:100%; border-radius:2px;
          background:linear-gradient(90deg, var(--accent), var(--accent-2));
          box-shadow:0 0 6px var(--accent-glow);
          transition:width 0.6s cubic-bezier(0.4,0,0.2,1);
        }}

        /* ═══════════════════════════════════════════════
           状态条 + 管道步骤
           ═══════════════════════════════════════════════ */
        .status-strip {{
          display:flex; align-items:center; justify-content:space-between; gap:1rem;
          padding:.8rem 1.1rem; margin-bottom:.8rem;
          background:var(--panel-glass);
          backdrop-filter:blur(14px) saturate(1.3);
          -webkit-backdrop-filter:blur(14px) saturate(1.3);
          border:1px solid var(--line); border-radius:.75rem;
          box-shadow:0 4px 20px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.03);
        }}
        .pipeline-strip {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:.5rem; margin-bottom:.8rem; }}
        .pipeline-step {{
          position:relative; padding:.75rem .85rem; min-height:4.2rem;
          background:var(--panel-glass);
          backdrop-filter:blur(12px) saturate(1.2);
          -webkit-backdrop-filter:blur(12px) saturate(1.2);
          border:1px solid var(--line); border-radius:.65rem;
          transition:all 0.35s cubic-bezier(0.34,1.56,0.64,1);
          box-shadow:0 3px 14px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.02);
        }}
        .pipeline-step:hover {{
          border-color:var(--line-glow);
          box-shadow:0 8px 24px var(--shadow-deep), 0 0 12px var(--shadow-glow);
          transform:translateY(-3px);
        }}
        .pipeline-step:not(:last-child)::after {{
          content:''; position:absolute; right:-.42rem; top:50%; transform:translateY(-50%);
          width:.42rem; height:.42rem; border-top:2px solid var(--line-glow); border-right:2px solid var(--line-glow);
          transform:translateY(-50%) rotate(45deg); z-index:1;
        }}
        .pipeline-step strong {{ display:block; color:var(--ink); font-size:.78rem; }}
        .pipeline-step span {{ display:block; color:var(--muted); font-size:.66rem; margin-top:.2rem; }}

        /* ═══════════════════════════════════════════════
           状态指示灯
           ═══════════════════════════════════════════════ */
        .status-dot {{
          width:.5rem; height:.5rem; border-radius:50%; display:inline-block; margin-right:.4rem;
          background:var(--green); box-shadow:0 0 0 .2rem rgba(var(--green_rgb),.12);
          animation:dot-pulse 2s ease-in-out infinite;
        }}
        .status-dot.warn {{ background:var(--amber); box-shadow:0 0 0 .2rem rgba(var(--amber_rgb),.12); }}
        .status-dot.bad {{ background:var(--red); box-shadow:0 0 0 .2rem rgba(var(--red_rgb),.12); }}
        @keyframes dot-pulse {{
          0%,100% {{ box-shadow:0 0 0 .2rem rgba(var(--green_rgb),.12); }}
          50% {{ box-shadow:0 0 0 .35rem rgba(var(--green_rgb),.06); }}
        }}
        .pulse-dot {{
          width:8px; height:8px; border-radius:50%;
          background:var(--accent); animation:dot-pulse 2s ease-in-out infinite;
          box-shadow:0 0 0 .2rem rgba(var(--accent_rgb),.12);
        }}

        /* ═══════════════════════════════════════════════
           徽标
           ═══════════════════════════════════════════════ */
        .badge {{
          display:inline-flex; align-items:center; padding:.2rem .45rem; border-radius:.35rem;
          font-size:.66rem; font-weight:700; border:1px solid var(--line); color:var(--muted);
        }}
        .badge.ok {{
          color:var(--accent); border-color:rgba(var(--accent_rgb),.3);
          background:rgba(var(--accent_rgb),.08); text-shadow:0 0 4px var(--accent-glow);
        }}
        .badge.warn {{
          color:var(--amber); border-color:rgba(var(--amber_rgb),.3);
          background:rgba(var(--amber_rgb),.08);
        }}
        .badge.bad {{
          color:var(--red); border-color:rgba(var(--red_rgb),.3);
          background:rgba(var(--red_rgb),.08);
        }}

        /* ═══════════════════════════════════════════════
           通用面板 + 区段标签
           ═══════════════════════════════════════════════ */
        .panel {{
          background:var(--panel-glass);
          backdrop-filter:blur(14px) saturate(1.3);
          -webkit-backdrop-filter:blur(14px) saturate(1.3);
          border:1px solid var(--line); border-radius:.75rem; padding:.9rem 1rem;
          box-shadow:0 4px 20px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.03);
        }}
        .section-label {{
          color:var(--muted); font-size:.68rem; letter-spacing:.1em; text-transform:uppercase;
          font-weight:700; margin-bottom:.5rem;
        }}
        .eyebrow {{
          color:var(--accent); font-size:.62rem; letter-spacing:.14em; text-transform:uppercase;
          font-weight:700; margin-bottom:.2rem; text-shadow:0 0 4px var(--accent-glow);
        }}
        .subline {{ color:var(--muted); font-size:.75rem; margin-top:.25rem; }}

        /* ═══════════════════════════════════════════════
           记录卡 + 热频卡
           ═══════════════════════════════════════════════ */
        .record-card {{
          background:var(--panel-glass);
          backdrop-filter:blur(12px) saturate(1.25);
          -webkit-backdrop-filter:blur(12px) saturate(1.25);
          border:1px solid var(--line); border-radius:.75rem; padding:.9rem 1rem; margin:.45rem 0;
          transition:all 0.35s cubic-bezier(0.34,1.56,0.64,1);
          box-shadow:0 3px 14px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.02);
          position:relative; overflow:hidden;
        }}
        .record-card::before {{
          content:''; position:absolute; top:0; left:0; bottom:0; width:2px;
          background:linear-gradient(180deg, var(--accent), transparent);
          opacity:0.4; transition:all 0.3s;
        }}
        .record-card:hover {{
          border-color:var(--line-glow);
          box-shadow:0 8px 28px var(--shadow-deep), 0 0 14px var(--shadow-glow);
          transform:translateY(-3px);
        }}
        .record-card:hover::before {{ opacity:0.8; width:3px; }}
        .record-title {{ color:var(--ink-bright); font-size:.95rem; font-weight:800; line-height:1.4; }}
        .record-meta {{ color:var(--muted); font-size:.72rem; margin-top:.3rem; }}
        .record-points {{ color:var(--muted); font-size:.76rem; margin-top:.55rem; line-height:1.6; }}

        .heat-card {{
          background:var(--panel-glass);
          backdrop-filter:blur(12px) saturate(1.25);
          -webkit-backdrop-filter:blur(12px) saturate(1.25);
          border:1px solid var(--line); border-radius:.75rem; padding:.9rem 1rem; margin:.5rem 0;
          transition:all 0.35s cubic-bezier(0.34,1.56,0.64,1);
          box-shadow:0 3px 14px var(--shadow-deep);
          position:relative; overflow:hidden;
        }}
        .heat-card::before {{
          content:''; position:absolute; top:0; left:0; right:0; height:2px;
          background:linear-gradient(90deg, var(--amber), var(--accent-3));
          opacity:0.4; transition:all 0.3s;
        }}
        .heat-card:hover {{
          border-color:rgba(var(--amber_rgb),0.3);
          box-shadow:0 8px 28px var(--shadow-deep), 0 0 14px rgba(var(--amber_rgb),0.2);
          transform:translateY(-3px);
        }}
        .heat-card:hover::before {{ opacity:0.8; height:3px; }}
        .heat-card h3 {{ color:var(--ink-bright); margin:.3rem 0 .4rem; }}
        .heat-score {{ color:var(--amber); font-size:1.2rem; font-weight:800; text-shadow:0 0 6px rgba(var(--amber_rgb),0.4); }}
        .heat-meta, .heat-sources {{ color:var(--muted); font-size:.72rem; display:flex; gap:.6rem; flex-wrap:wrap; }}
        .heat-sources {{ margin:.45rem 0; }}
        .heat-card a {{ color:var(--accent); font-size:.74rem; text-decoration:none; }}

        /* ═══════════════════════════════════════════════
           空状态
           ═══════════════════════════════════════════════ */
        .empty {{
          padding:2rem 1rem; text-align:center; color:var(--muted);
          border:1px dashed var(--line); border-radius:.7rem;
          background:rgba(var(--accent_rgb),0.02);
        }}

        /* ═══════════════════════════════════════════════
           Streamlit 原生控件玻璃化
           ═══════════════════════════════════════════════ */
        div[data-testid="stMetric"] {{
          background:var(--panel-glass);
          backdrop-filter:blur(12px) saturate(1.2);
          -webkit-backdrop-filter:blur(12px) saturate(1.2);
          border:1px solid var(--line); padding:.7rem .9rem; border-radius:.7rem;
          box-shadow:0 3px 14px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.02);
        }}
        div[data-testid="stMetricValue"] {{ color:var(--ink-bright); text-shadow:0 0 8px var(--accent-glow); }}
        div[data-testid="stMetricLabel"] {{ color:var(--muted); }}
        .stButton > button, .stDownloadButton > button {{
          border:1px solid var(--line); border-radius:.55rem;
          background:var(--panel-glass); color:var(--ink);
          backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
          transition:all 0.25s cubic-bezier(0.4,0,0.2,1); font-weight:600; font-size:.8rem;
          box-shadow:0 2px 8px var(--shadow-deep);
        }}
        .stButton > button:hover, .stDownloadButton > button:hover {{
          border-color:var(--accent); color:var(--accent);
          box-shadow:0 0 14px var(--accent-glow), 0 4px 12px var(--shadow-deep);
          background:rgba(var(--accent_rgb),0.06);
          transform:translateY(-1px);
        }}
        .stButton > button:active, .stDownloadButton > button:active {{
          transform:scale(0.97);
          box-shadow:0 0 16px var(--accent-glow), inset 0 0 6px rgba(var(--accent_rgb),0.15);
        }}
        div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {{
          background:var(--panel-glass) !important;
          backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
          border-color:var(--line) !important;
        }}
        .stTabs [data-baseweb="tab-list"] {{ gap:.8rem; border-bottom:1px solid var(--line); }}
        .stTabs [data-baseweb="tab"] {{ color:var(--muted); font-size:.82rem; }}
        .stTabs [aria-selected="true"] {{ color:var(--accent); text-shadow:0 0 4px var(--accent-glow); }}

        /* Expander 玻璃化 */
        .stExpander > details {{
          background:var(--panel-glass) !important;
          backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
          border:1px solid var(--line) !important; border-radius:.65rem !important;
          box-shadow:0 3px 14px var(--shadow-deep) !important;
        }}
        .stExpander > details > summary {{ color:var(--accent) !important; text-shadow:0 0 3px var(--accent-glow); }}

        /* Divider 霓虹化 */
        hr {{ border-color:var(--line) !important; position:relative; }}
        hr::after {{
          content:''; position:absolute; left:10%; right:10%; top:-1px; height:1px;
          background:linear-gradient(90deg, transparent, var(--accent), transparent);
          opacity:0.3;
        }}

        /* 表格行悬浮 */
        .stDataFrame [data-testid="stDataFrame"] tbody tr:hover {{
          background:rgba(var(--accent_rgb),0.04) !important;
        }}

        /* ═══════════════════════════════════════════════
           主题色块预览
           ═══════════════════════════════════════════════ */
        .theme-swatch-row {{ display:flex; gap:.25rem; margin-left:.4rem; }}
        .theme-swatch {{ width:9px; height:9px; border-radius:2px; box-shadow:0 0 3px currentColor; }}

        /* ═══════════════════════════════════════════════
           全局交互特效
           ═══════════════════════════════════════════════ */
        ::selection {{ background:rgba(var(--accent_rgb),0.25); color:var(--ink-bright); text-shadow:0 0 6px var(--accent-glow); }}
        ::-moz-selection {{ background:rgba(var(--accent_rgb),0.25); color:var(--ink-bright); }}
        * {{ cursor:default; }}
        a, button, [role="button"], .stButton > button, .stDownloadButton > button,
        label[data-checked], [data-testid="stRadio"] label {{ cursor:pointer; }}
        a:hover, .stButton > button:hover, .stDownloadButton > button:hover {{
          text-shadow:0 0 6px var(--accent-glow);
        }}
        input:focus, textarea:focus {{
          border-color:var(--accent) !important;
          box-shadow:0 0 0 2px var(--accent-glow), 0 0 12px var(--accent-glow) !important;
          outline:none !important;
        }}
        div[data-baseweb="select"]:focus-within {{
          box-shadow:0 0 0 2px var(--accent-glow) !important;
        }}

        /* ═══════════════════════════════════════════════
           全局动态视觉效果（收敛版）
           ═══════════════════════════════════════════════ */

        /* 入场动画 */
        @keyframes entrance-fade {{
          from {{ opacity:0; transform:translateY(8px); }}
          to {{ opacity:1; transform:translateY(0); }}
        }}
        .entrance-fade {{ animation:entrance-fade 0.5s ease-out both; }}
        .entrance-fade:nth-child(1) {{ animation-delay:0.03s; }}
        .entrance-fade:nth-child(2) {{ animation-delay:0.06s; }}
        .entrance-fade:nth-child(3) {{ animation-delay:0.09s; }}
        .entrance-fade:nth-child(4) {{ animation-delay:0.12s; }}
        .entrance-fade:nth-child(5) {{ animation-delay:0.15s; }}
        .entrance-fade:nth-child(6) {{ animation-delay:0.18s; }}

        /* 标题文字（静态辉光，保持整洁） */
        .neon-text {{ text-shadow:0 0 8px var(--accent-glow); }}

        /* 数据流光带 */
        @keyframes data-flow {{
          0% {{ transform:translateX(-100%); }}
          100% {{ transform:translateX(100%); }}
        }}
        .data-flow-bar {{
          position:relative; height:2px; overflow:hidden;
          background:rgba(var(--accent_rgb),0.08); border-radius:1px;
        }}
        .data-flow-bar::after {{
          content:''; position:absolute; inset:0;
          background:linear-gradient(90deg, transparent, var(--accent), transparent);
          animation:data-flow 3s linear infinite;
        }}

        /* ═══════════════════════════════════════════════
           统一尺寸系统 — 所有卡片与按钮同一套规格
           ═══════════════════════════════════════════════ */
        :root {{
          --radius: .65rem;
          --card-pad-x: 1rem;
          --card-pad-y: .85rem;
          --btn-h: 2.15rem;
          --ctl-gap: .6rem;
        }}
        /* 卡片统一：圆角 / 内距 / 边框 / 底色 */
        .metric, .panel, .record-card, .heat-card, .module-card,
        .pipeline-step, .status-strip, .empty, div[data-testid="stMetric"] {{
          border-radius:var(--radius);
          padding:var(--card-pad-y) var(--card-pad-x);
          border:1px solid var(--line);
          background:var(--panel-glass);
        }}
        .empty {{ background:rgba(var(--accent_rgb),0.02); }}
        /* 按钮统一：高度 / 圆角 / 字号 */
        .stButton > button, .stDownloadButton > button {{
          height:var(--btn-h); min-height:var(--btn-h);
          padding:0 1rem; border-radius:calc(var(--radius) - 2px);
          font-size:.8rem; font-weight:600; justify-content:center;
        }}
        .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
          background:var(--side-bg); border-color:transparent; color:#ffffff;
        }}
        .stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {{
          background:var(--side-bg-2); color:#ffffff; border-color:transparent;
        }}
        /* 卡片间距统一 */
        .metric-row, .pipeline-strip {{ gap:var(--ctl-gap); }}
        .record-card, .heat-card {{ margin:.4rem 0; }}

        /* ═══════════════════════════════════════════════
           响应式自适应
           ═══════════════════════════════════════════════ */
        @media (max-width:1200px) {{
          .metric-row {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
          .pipeline-strip {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
          .pipeline-step:nth-child(3)::after {{ display:none; }}
        }}
        @media (max-width:800px) {{
          .block-container {{ padding:.5rem .8rem 2rem; }}
          .cmd-bar {{ flex-direction:column; align-items:flex-start; }}
          .status-strip {{ flex-direction:column; align-items:flex-start; }}
          .pipeline-strip {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
          .pipeline-step:not(:last-child)::after {{ display:none; }}
          .metric-row {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
          .metric {{ min-height:4.5rem; padding:.65rem; }}
          .metric-value {{ font-size:1.2rem; }}
          h1 {{ font-size:1.4rem !important; }}
        }}
        @media (max-width:480px) {{
          .metric-row {{ grid-template-columns:1fr; }}
          .pipeline-strip {{ grid-template-columns:1fr; }}
        }}

        /* Streamlit 自带侧栏隐藏（我们用 columns 自建） */
        [data-testid="stSidebar"] {{ display:none !important; }}
        [data-testid="stSidebarCollapsedControl"] {{ display:none !important; }}

        </style>
        """,
        unsafe_allow_html=True,
    )


def status_label(status: str) -> str:
    return {"ok": "正常", "error": "失败", "pending": "待审核", "idle": "待运行"}.get(status, status or "未知")


def format_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%m-%d %H:%M")
    except ValueError:
        return value


def render_topbar(active_view: str) -> None:
    """统一工具带：全宽一条，面包屑 + 当前页面 + 状态，视觉上为一个整体。"""
    st.markdown(
        f"<div class='topbar entrance-fade'>"
        f"<div class='topbar-left'>"
        f"<span class='topbar-crumb'>{BRAND_NAME}</span>"
        f"<span class='topbar-sep'></span>"
        f"<span class='topbar-page'>{html.escape(active_view)}</span>"
        f"</div>"
        f"<div class='topbar-right'>"
        f"<span class='badge ok'>系统在线</span>"
        f"<span class='topbar-note'>{BRAND_TAGLINE}</span>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_pipeline_strip() -> None:
    st.markdown("<div class='pipeline-strip'><div class='pipeline-step'><strong>公开来源</strong><span>白名单 · robots · 频率</span></div><div class='pipeline-step'><strong>结构化抽取</strong><span>字段 · 证据 · 复核</span></div><div class='pipeline-step'><strong>规则匹配</strong><span>硬条件 · 可解释理由</span></div><div class='pipeline-step'><strong>知识图谱</strong><span>节点 · 关系 · 证据</span></div><div class='pipeline-step'><strong>服务输出</strong><span>接口服务 · 工作台 · 通知</span></div></div>", unsafe_allow_html=True)


def export_payload(records: list[dict[str, Any]]) -> str:
    fields = ("id", "source_id", "title", "issuing_body", "gov_level", "region_province", "region_city", "region_county", "category_l1", "category_l2", "target_group", "target_industry", "amount", "deadline", "publish_date", "source_url", "needs_review")
    return json.dumps([{key: record.get(key) for key in fields} for record in records], ensure_ascii=False, indent=2)


def render_quick_actions(records: list[dict[str, Any]]) -> None:
    action_col, refresh_col, export_col = st.columns([1, 1, 1.4], gap="small")
    if action_col.button("运行一次采集", type="primary", use_container_width=True, key="run_collection_once"):
        with st.spinner("正在按来源频率运行采集任务…"):
            try:
                completed = subprocess.run([sys.executable, str(Path(__file__).parent / "scheduler.py"), "--once"], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=900, check=False)
                if completed.returncode == 0:
                    load_snapshot.clear()
                    load_quality.clear()
                    load_backups.clear()
                    load_graph_summary.clear()
                    st.success("采集任务已完成。")
                    st.rerun()
                else:
                    st.error(completed.stderr.strip() or "采集任务未完成，请查看失败源日报。")
            except subprocess.TimeoutExpired:
                st.error("采集任务超过 15 分钟，已停止等待。")
            except OSError as exc:
                st.error(f"无法启动采集任务：{exc}")
    if refresh_col.button("刷新视图", use_container_width=True, key="refresh_dashboard"):
        load_snapshot.clear()
        load_quality.clear()
        load_backups.clear()
        load_graph_summary.clear()
        st.rerun()
    export_col.download_button("导出公告数据", data=export_payload(records), file_name="beacon-announcements.json", mime="application/json", use_container_width=True, key="export_announcements")


def render_overview(records: list[dict[str, Any]], sources: list[dict[str, Any]], quality: dict[str, Any], backups: list[dict[str, Any]], graph: dict[str, Any]) -> None:
    enabled = [item for item in sources if item.get("status") == "ok"]
    failed = [item for item in sources if item.get("status") == "error"]
    pending = [item for item in sources if item.get("status") not in {"ok", "error"}]
    rate = float(quality.get("extraction_success_rate") or 0)
    health = "链路稳定" if not failed else "存在失败源"
    tone = "" if not failed else " warn"
    st.markdown(f"<div class='status-strip'><div><span class='status-dot{tone}'></span><strong>{health}</strong><div class='subline'>{len(enabled)} 个来源正常 · {len(failed)} 个失败 · {len(pending)} 个待审核</div></div><div style='text-align:right'><div class='subline'>最近质量报告</div><strong>{html.escape(str(quality.get('date') or '暂无数据'))}</strong></div></div>", unsafe_allow_html=True)
    render_pipeline_strip()
    render_quick_actions(records)
    st.markdown("<div class='metric-row'><div class='metric'><div class='metric-label'>本地公告</div><div class='metric-value'>{:,}</div><div class='metric-note'>已入库公开信息</div></div><div class='metric'><div class='metric-label'>匹配结果</div><div class='metric-value'>{:,}</div><div class='metric-note'>当前用户条件</div></div><div class='metric'><div class='metric-label'>抽取成功率</div><div class='metric-value'>{:.1%}</div><div class='metric-note'>结构化字段通过</div></div><div class='metric'><div class='metric-label'>备份文件</div><div class='metric-value'>{}</div><div class='metric-note'>保留 30 天</div></div></div>".format(len(records), int(quality.get("matches") or 0), rate, len(backups)), unsafe_allow_html=True)
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.markdown("<div class='section-label'>链路状态</div>", unsafe_allow_html=True)
        rows = []
        for item in sources[:8]:
            rows.append({"来源": item["name"], "状态": status_label(str(item.get("status") or "pending")), "入库": item.get("records", 0), "最近成功": format_time(item.get("last_success"))})
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.markdown("<div class='empty'>暂无来源状态，先运行一次采集任务。</div>", unsafe_allow_html=True)
    with right:
        st.markdown("<div class='section-label'>今日质量</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='panel'><div class='metric-label'>采集量</div><div class='metric-value'>{int(quality.get('collected') or 0):,}</div><div class='metric-note'>失败源次数 {int(quality.get('failures') or 0)} · 运行次数 {int(quality.get('runs') or 0)}</div></div>", unsafe_allow_html=True)
        st.markdown("<div class='section-label' style='margin-top:1rem'>图谱状态</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='panel'><div class='metric-label'>节点 / 关系</div><div class='metric-value'>{int(graph.get('nodes') or 0):,} / {int(graph.get('edges') or 0):,}</div><div class='metric-note'>证据 {int(graph.get('evidence') or 0):,} · 待复核 {int(graph.get('needs_review') or 0):,}</div></div>", unsafe_allow_html=True)
    st.markdown("<div class='section-label' style='margin-top:1.25rem'>最近公告</div>", unsafe_allow_html=True)
    for record in records[:5]:
        render_record(record, compact=True)


def render_record(record: dict[str, Any], compact: bool = False) -> None:
    points = record.get("key_points") or []
    meta = " · ".join(str(value) for value in [record.get("issuing_body"), record.get("publish_date"), record.get("gov_level"), record.get("category_l1")] if value)
    with st.container():
        st.markdown(f"<div class='record-card'><div class='record-title'>{html.escape(str(record.get('title') or '未命名公告'))}</div><div class='record-meta'>{html.escape(meta)}</div>", unsafe_allow_html=True)
        if points and not compact:
            st.markdown(f"<div class='record-points'>{html.escape('；'.join(str(point) for point in points[:3]))}</div>", unsafe_allow_html=True)
        badges = []
        if record.get("focus_tracked"):
            badges.append("<span class='badge ok'>重点跟踪</span>")
        if record.get("needs_review"):
            badges.append("<span class='badge warn'>待复核</span>")
        if badges:
            st.markdown("<div style='margin-top:.55rem'>" + " ".join(badges) + "</div>", unsafe_allow_html=True)
        if record.get("source_url"):
            st.link_button("打开原文", str(record["source_url"]), key=f"source-{record.get('id')}-{int(compact)}")
        st.markdown("</div>", unsafe_allow_html=True)


def render_announcements(records: list[dict[str, Any]], rules: dict[str, Any], filters: dict[str, Any]) -> None:
    st.markdown("<div class='section-label'>公告检索</div>", unsafe_allow_html=True)
    st.markdown("### 找到与你有关的重要公开信息")
    user = {
        "region": filters.get("region", ""),
        "levels": filters.get("levels", []),
        "categories": filters.get("categories", []),
        "target_group": filters.get("target_group", ""),
        "target_industry": filters.get("target_industry", ""),
        "keywords": filters.get("keywords", []),
        "industry_keys": filters.get("industry_keys", []),
        "industry_focus_only": filters.get("industry_focus_only", False),
        "exclude_review": True,
    }
    matches = match_announcements(records, user, rules)
    left, right = st.columns([2.2, 1], gap="large")
    with left:
        if not matches:
            st.markdown("<div class='empty'>当前条件下没有匹配结果。试试放宽地区或分类。</div>", unsafe_allow_html=True)
            return
        for record in matches:
            render_record(record)
    with right:
        st.markdown("<div class='panel'><div class='section-label'>当前结果</div><div class='metric-value'>{:,}</div><div class='metric-note'>条匹配公告</div></div>".format(len(matches)), unsafe_allow_html=True)
        st.download_button("导出筛选结果", data=export_payload(matches), file_name="beacon-matches.json", mime="application/json", use_container_width=True, key="export_matches")
        st.markdown("#### 匹配说明")
        st.caption("结果只使用已入库字段和现有规则，不生成模型理由。")
        if matches:
            selected = st.selectbox("查看公告", [str(item["id"]) for item in matches], format_func=lambda value: next((str(item.get("title") or "未命名公告") for item in matches if str(item["id"]) == value), value), key="announcement_detail")
            record = next(item for item in matches if str(item["id"]) == selected)
            st.markdown(f"**{record.get('title') or '未命名公告'}**")
            st.caption(" · ".join(str(value) for value in [record.get("issuing_body"), record.get("publish_date")] if value))
            for label, key in [("适用人群", "target_group"), ("适用行业", "target_industry"), ("金额", "amount"), ("截止日期", "deadline")]:
                if record.get(key):
                    st.write(f"{label}：{record[key]}")
            if record.get("reasons"):
                st.caption("匹配理由：" + "；".join(record["reasons"]))


def render_sources(sources: list[dict[str, Any]]) -> None:
    st.markdown("<div class='section-label'>来源健康</div>", unsafe_allow_html=True)
    st.markdown("### 每个来源都有自己的节奏")
    filter_status = st.selectbox("状态筛选", ["全部", "正常", "失败", "待审核/待运行"], key="source_status_filter")
    mapping = {"正常": "ok", "失败": "error", "待审核/待运行": "pending"}
    filtered = sources if filter_status == "全部" else [item for item in sources if item.get("status") == mapping.get(filter_status, item.get("status"))]
    rows = []
    for item in filtered:
        rows.append({"来源": item["name"], "source_id": item["source_id"], "层级": item.get("level") or "—", "频率": item.get("cadence") or "—", "状态": status_label(str(item.get("status") or "pending")), "入库": item.get("records", 0), "连续失败": item.get("failures", 0), "最近成功": format_time(item.get("last_success")), "下次到期": format_time(item.get("next_due"))})
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.markdown("<div class='empty'>暂无符合条件的来源。</div>", unsafe_allow_html=True)
    failed = [item for item in sources if item.get("status") == "error"]
    if failed:
        st.markdown("#### 需要关注")
        for item in failed[:5]:
            st.error(f"{item['name']}：{item.get('error') or '采集失败'}")


def render_quality(quality: dict[str, Any], backups: list[dict[str, Any]]) -> None:
    st.markdown("<div class='section-label'>质量与备份</div>", unsafe_allow_html=True)
    st.markdown("### 让数据质量可见、可追踪")
    metric_col, backup_col = st.columns(2, gap="large")
    with metric_col:
        rate = float(quality.get("extraction_success_rate") or 0)
        st.markdown(f"<div class='panel'><div class='metric-label'>抽取成功率</div><div class='metric-value'>{rate:.1%}</div><div class='metric-note'>采集量 {int(quality.get('collected') or 0):,} · 匹配量 {int(quality.get('matches') or 0):,}</div></div>", unsafe_allow_html=True)
        st.markdown("#### 来源质量")
        stats = quality.get("source_stats") or {}
        rows = [{"来源": key, "运行": value.get("runs", 0), "采集": value.get("collected", 0), "失败": value.get("failures", 0)} for key, value in stats.items()]
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption("等待第一次调度任务生成质量报告。")
    with backup_col:
        st.markdown("<div class='panel'><div class='metric-label'>SQLite 备份</div><div class='metric-value'>{}</div><div class='metric-note'>保留最近 30 天</div></div>".format(len(backups)), unsafe_allow_html=True)
        if st.button("立即创建备份", use_container_width=True, key="create_backup"):
            result = backup_database("data/beacon.db", "data/backups", 30)
            if result.get("created"):
                st.success("备份已创建，清理了过期备份。")
                load_backups.clear()
                st.rerun()
            else:
                st.warning("暂未找到可备份的数据库。")
        if backups:
            st.dataframe([{"文件": item["name"], "大小": f"{item['size'] / 1024:.1f} KB", "更新时间": format_time(item["modified"])} for item in backups[:10]], use_container_width=True, hide_index=True)
        else:
            st.caption("暂无备份文件。")


def render_graph() -> None:
    summary = load_graph_summary()
    st.markdown("<div class='section-label'>知识图谱</div>", unsafe_allow_html=True)
    st.markdown("### 把公告变成可追溯的关系")
    st.markdown("<div class='metric-row'><div class='metric'><div class='metric-label'>节点</div><div class='metric-value'>{:,}</div><div class='metric-note'>实体与公告</div></div><div class='metric'><div class='metric-label'>关系</div><div class='metric-value'>{:,}</div><div class='metric-note'>结构化关联</div></div><div class='metric'><div class='metric-label'>证据</div><div class='metric-value'>{:,}</div><div class='metric-note'>原文哈希与字段</div></div><div class='metric'><div class='metric-label'>待复核</div><div class='metric-value'>{:,}</div><div class='metric-note'>不直接对外展示</div></div></div>".format(int(summary.get("nodes") or 0), int(summary.get("edges") or 0), int(summary.get("evidence") or 0), int(summary.get("needs_review") or 0)), unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### 节点类型")
        node_types = summary.get("node_types") or {}
        st.dataframe([{"类型": key, "数量": value} for key, value in node_types.items()], use_container_width=True, hide_index=True) if node_types else st.caption("运行采集后自动生成图谱。")
    with right:
        st.markdown("#### 关系类型")
        edge_types = summary.get("edge_types") or {}
        st.dataframe([{"关系": key, "数量": value} for key, value in edge_types.items()], use_container_width=True, hide_index=True) if edge_types else st.caption("暂无关系数据。")


def render_heat_topics() -> None:
    topics = load_heat_topics()
    st.markdown("<div class='section-label'>热频话题</div>", unsafe_allow_html=True)
    st.markdown("### 公共政策与民生事件热度")
    if not topics:
        st.markdown("<div class='empty'>暂无热频话题。媒体源审核并产生多源报道后自动生成。</div>", unsafe_allow_html=True)
        return
    top_score = max(float(item.get("heat_score") or 0) for item in topics)
    official = sum(1 for item in topics if "官方" in str(item.get("heat_reason") or ""))
    st.markdown(f"<div class='metric-row'><div class='metric'><div class='metric-label'>话题数</div><div class='metric-value'>{len(topics)}</div><div class='metric-note'>近期待处理</div></div><div class='metric'><div class='metric-label'>最高热度</div><div class='metric-value'>{top_score:.1f}</div><div class='metric-note'>规则计算</div></div><div class='metric'><div class='metric-label'>官方源话题</div><div class='metric-value'>{official}</div><div class='metric-note'>交叉信源</div></div><div class='metric'><div class='metric-label'>统计口径</div><div class='metric-value'>规则</div><div class='metric-note'>非点击量</div></div></div>", unsafe_allow_html=True)
    for topic in topics:
        title = html.escape(str(topic.get("title") or "未命名话题"))
        category = html.escape(str(topic.get("category_l1") or "其他"))
        source_names = html.escape(" · ".join(str(item) for item in topic.get("source_names") or []))
        reason = html.escape(str(topic.get("heat_reason") or ""))
        primary_url = str((topic.get("article_urls") or ["#"])[0])
        if not primary_url.startswith(("http://", "https://")):
            primary_url = "#"
        st.markdown(f"<div class='heat-card'><div class='heat-score'>{float(topic.get('heat_score') or 0):.1f}</div><h3>{title}</h3><div class='heat-meta'><span>{category}</span><span>{int(topic.get('source_count') or 0)}家媒体报道</span><span>{html.escape(str(topic.get('latest_publish') or ''))}</span></div><div class='heat-sources'>{source_names}</div><div class='subline'>{reason}</div><a href='{html.escape(primary_url, quote=True)}' target='_blank' rel='noreferrer'>查看详情</a></div>", unsafe_allow_html=True)




def render_side_panel(active_view: str, right_open: bool = False) -> None:
    """左侧栏：品牌 + 全部功能菜单 + 运行状态 + 个人中心。

    菜单放在 st.container(key="side") 内，CSS 才能把菜单和品牌收进同一个框；
    之前菜单是 .side-panel 的兄弟节点，被 100vh 的空壳顶到视口之外。
    右侧备用区的开关也放在这里：原来它在最左控制轨上，那条轨已整体删除。
    """
    logo = load_logo_svg()
    with st.container(key="side"):
        st.markdown(
            f"<div class='side-brand entrance-fade'>"
            f"<div class='brand-mark'>{logo}</div>"
            f"<div><div class='side-title'>{BRAND_NAME}</div>"
            f"<div class='side-sub'>{BRAND_EYEBROW}</div></div>"
            f"</div>"
            f"<div class='side-online'><span class='pulse-dot'></span><span>系统在线 · 本地运行</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div class='side-group'>功能模块</div>", unsafe_allow_html=True)
        index = 0
        # Grouped buttons instead of a radio: the radio renders as one flat option
        # list with no room for section headers, and one radio per group kept a
        # second navigation state that fought active_view and drove st.rerun() in a
        # loop. Every button writes the same single key, so there is no ambiguity.
        for group_name, members in NAV_GROUPS:
            st.markdown(
                f"<div class='nav-group'><span class='nav-group-name'>{html.escape(group_name)}</span>"
                f"<span class='nav-group-count'>{len(members)}</span></div>",
                unsafe_allow_html=True,
            )
            for name in members:
                is_active = name == active_view
                if st.button(
                    name,
                    key=f"nav_btn_{index}",
                    icon=NAV_ICONS.get(name, ":material/circle:"),
                    help=name,
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    st.session_state["active_view"] = name
                    st.rerun()
                index += 1
        st.markdown("<div class='side-group'>运行状态</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='side-stats'>"
            f"<div class='aux-stat'><span>当前页面</span><b>{html.escape(active_view)}</b></div>"
            f"<div class='aux-stat'><span>功能模块</span><b>{len(NAV_VIEWS)}</b></div>"
            f"<div class='aux-stat'><span>数据源</span><b>{len(load_snapshot('data/beacon.db')['sources'])}</b></div>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            "收备用区" if right_open else "备用区",
            key="side_aux_toggle",
            icon=":material/vertical_split:",
            use_container_width=True,
            help="右侧备用功能区：主题拼色 / 快捷操作 / 公告筛选",
        ):
            st.session_state["right_open"] = not right_open
            st.rerun()
        st.markdown(
            "<div class='user-card'>"
            "<div class='user-row'>"
            "<div class='user-avatar'>运</div>"
            "<div><div class='user-name'>运营员</div><div class='user-role'>管理员 · 本地账户</div></div>"
            "</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        col_set, col_out = st.columns(2, gap="small")
        if col_set.button("账户设置", use_container_width=True, key="user_settings_btn"):
            st.toast("账户设置：本地单用户模式，暂无可配置项")
        if col_out.button("退出登录", use_container_width=True, key="user_logout_btn"):
            st.toast("本地工作台无需登录退出")


def render_aux_panel() -> dict[str, Any]:
    """右侧栏：备用功能区 — 主题拼色 / 系统状态 / 快捷操作 / 公告筛选。

    与左栏同理：控件放进 st.container(key="aux")，否则 .aux-panel 的 100vh 高度
    只会撑出一个空壳，真实控件全部掉到框外。
    """
    filters: dict[str, Any] = {}
    with st.container(key="aux"):
        st.markdown("<div class='aux-title entrance-fade'>备用功能区</div>", unsafe_allow_html=True)

    # ── 主题拼色 ──
    st.markdown("<div class='aux-label'>拼色主题</div>", unsafe_allow_html=True)
    chips = "".join(
        "<div class='theme-chip'>"
        f"<span class='chip-side' style='background:{colors[0]}'></span>"
        f"<span class='chip-top' style='background:{colors[1]}'></span>"
        f"<span class='chip-name'>{THEME_LABELS[tid]}</span>"
        "</div>"
        for tid, colors in THEME_SWATCHES.items()
    )
    st.markdown(f"<div class='theme-grid'>{chips}</div>", unsafe_allow_html=True)
    current_theme = st.session_state.get("theme", default_theme())
    selected_theme = st.radio(
        "主题",
        THEME_ORDER,
        format_func=lambda tid: THEME_LABELS[tid],
        index=THEME_ORDER.index(current_theme) if current_theme in THEME_ORDER else 0,
        key="theme_selector",
        horizontal=True,
        label_visibility="collapsed",
    )
    if selected_theme != current_theme:
        st.session_state["theme"] = selected_theme
        st.rerun()

    # ── 系统状态 ──
    st.markdown("<div class='aux-label'>系统状态</div>", unsafe_allow_html=True)
    db_path = Path("data/beacon.db")
    db_size = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0
    try:
        snapshot = load_snapshot("data/beacon.db")
        quality = load_quality()
        ok_sources = sum(1 for item in snapshot["sources"] if item.get("status") == "ok")
        bad_sources = sum(1 for item in snapshot["sources"] if item.get("status") == "error")
        records_count = len(snapshot["records"])
    except Exception:
        ok_sources = bad_sources = records_count = 0
        quality = {}
    st.markdown(
        f"<div class='aux-stat'><span>数据库</span><b>{db_size:.1f} MB</b></div>"
        f"<div class='aux-stat'><span>公告入库</span><b>{records_count:,}</b></div>"
        f"<div class='aux-stat'><span>来源正常</span><b>{ok_sources}</b></div>"
        f"<div class='aux-stat'><span>来源失败</span><b>{bad_sources}</b></div>"
        f"<div class='aux-stat'><span>最近质量</span><b>{html.escape(str(quality.get('date') or '暂无'))}</b></div>",
        unsafe_allow_html=True,
    )

    # ── 快捷操作 ──
    st.markdown("<div class='aux-label'>快捷操作</div>", unsafe_allow_html=True)
    if st.button("运行采集", use_container_width=True, key="sidebar_collect", help="运行一次采集任务"):
        with st.spinner("正在运行采集…"):
            try:
                completed = subprocess.run([sys.executable, str(Path(__file__).parent / "scheduler.py"), "--once"], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=900, check=False)
                if completed.returncode == 0:
                    load_snapshot.clear()
                    load_quality.clear()
                    load_backups.clear()
                    load_graph_summary.clear()
                    load_heat_topics.clear()
                    st.success("采集完成")
                    st.rerun()
                else:
                    st.error(completed.stderr.strip() or "采集未完成")
            except subprocess.TimeoutExpired:
                st.error("采集超时（超过15分钟）")
            except OSError as exc:
                st.error(f"无法启动：{exc}")
    if st.button("立即备份", use_container_width=True, key="sidebar_backup", help="立即创建数据库备份"):
        result = backup_database("data/beacon.db", "data/backups", 30)
        if result.get("created"):
            load_backups.clear()
            st.success("备份已创建")
            st.rerun()
        else:
            st.warning("暂无可备份数据库")
    if st.button("刷新数据", use_container_width=True, key="sidebar_refresh"):
        load_snapshot.clear()
        load_quality.clear()
        load_backups.clear()
        load_graph_summary.clear()
        load_heat_topics.clear()
        st.rerun()

    # ── 公告筛选 ──
    with st.expander("公告筛选"):
        filters["levels"] = st.multiselect("行政层级", LEVEL_OPTIONS, key="filter_levels")
        filters["region"] = st.text_input("地区", key="filter_region").strip()
        filters["categories"] = st.multiselect("分类", CATEGORY_OPTIONS, key="filter_categories")
        filters["target_group"] = st.text_input("适用人群", key="filter_target_group").strip()
        filters["target_industry"] = st.text_input("适用行业", key="filter_target_industry").strip()
        filters["keywords"] = [item for item in re.split(r"[\s,，]+", st.text_input("关键词", help="空格或逗号分隔", key="filter_keywords")) if item]
        filters["industry_keys"] = st.multiselect("重点产业", INDUSTRY_OPTIONS, key="filter_industries")
        filters["industry_focus_only"] = st.checkbox("仅看年度重点产业", key="filter_industry_focus")
    return filters


def main() -> None:
    # 处理从首页卡片点击传来的导航指令
    nav_view = st.query_params.get("nav_view")
    if nav_view:
        if nav_view in NAV_VIEWS:
            st.session_state["active_view"] = nav_view
        st.query_params.clear()
        st.rerun()

    inject_styles()
    active_view = st.session_state.get("active_view", "总览")
    right_open = st.session_state.get("right_open", False)

    # ── 统一工具带（全宽一条：面包屑 + 页面名 + 状态） ──
    render_topbar(active_view)

    # ── 两栏布局：左侧功能栏常驻 + 主区（右侧备用区按需） ──
    # 原来最左边还有一条 148px 的控制轨，只放"收起/备用"两个按钮，而 13 个模块
    # 都在左侧栏里，那条轨纯属浪费横向空间，已整体删除；右栏开关移入左侧栏。
    filters: dict[str, Any] = {}
    if right_open:
        col_side, col_main, col_aux = st.columns([1.3, 4.4, 1.3], gap="small")
        with col_side:
            render_side_panel(active_view, right_open)
        with col_aux:
            filters = render_aux_panel()
        main_container = col_main
    else:
        col_side, col_main = st.columns([1.3, 5.7], gap="small")
        with col_side:
            render_side_panel(active_view, right_open)
        main_container = col_main

    with main_container:
        try:
            snapshot = load_snapshot("data/beacon.db")
            quality = load_quality()
            backups = load_backups()
        except Exception as exc:
            st.error(f"工作台数据加载失败：{exc}")
            st.stop()
        rules = load_rules()
        theme_id = st.session_state.get("theme", default_theme())
        if active_view == "总览":
            render_home(snapshot["records"], snapshot["sources"], quality, load_graph_summary(), theme_id)
        elif active_view == "中国地图":
            render_china_map(snapshot["records"], theme_id=theme_id)
        elif active_view == "公告检索":
            render_announcements(snapshot["records"], rules, filters)
        elif active_view == "来源健康":
            render_sources(snapshot["sources"])
        elif active_view == "知识图谱":
            render_graph()
        elif active_view == "热频话题":
            render_heat_topics()
        elif active_view == "进化仪表盘":
            render_evolution_dashboard("data/beacon.db")
        elif active_view == "对比工作台":
            render_compare_workbench("data/beacon.db")
        elif active_view == "关联确认":
            render_relations_review("data/beacon.db")
        elif active_view == "字段复核":
            render_fields_review("data/beacon.db")
        elif active_view == "订阅中心":
            render_subscriptions("data/beacon.db")
        elif active_view == "采集运维":
            render_collector_ops("data/beacon.db")
        else:
            render_quality(quality, backups)


main()
