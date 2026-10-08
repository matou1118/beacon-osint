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
from branding import BRAND_NAME, BRAND_TAGLINE, load_logo_svg
from admin_fields_view import render as render_fields_review
from admin_relations_view import render as render_relations_review
from collector_ops_view import render as render_collector_ops
from lighthouse_view import render as render_lighthouse
from compare_view import render as render_compare_workbench
from evolution_view import render as render_evolution_dashboard
from subscription_view import render as render_subscriptions
from publication_view import render as render_publications
from command_center import render as render_command_center
from graph_service import graph_summary, graph_sample
from chart_helpers import render_echart
from home_dashboard import render_home
from map_dashboard import render_china_map
from rules.engine import match_announcements
from storage.db import Database
from storage.models import SourceSyncState
from theme_config import THEME_LABELS, THEME_ORDER, THEME_SWATCHES, default_theme, get_theme_css

st.set_page_config(page_title=f"{BRAND_NAME} · 运营工作台", layout="wide", initial_sidebar_state="collapsed")

def _load_category_options() -> list[str]:
    """从 config/categories.yaml 动态加载一级分类，保持与枚举/订阅匹配一致。"""
    path = Path(_PROJECT_ROOT) / "config" / "categories.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list((data.get("l1") or {}).keys())


CATEGORY_OPTIONS = _load_category_options()
LEVEL_OPTIONS = ["国家部委", "省级", "市级", "县级"]
INDUSTRY_OPTIONS = ["新能源", "人工智能", "高端制造", "数字经济", "教育与就业"]


# 分组：侧栏按职能分段展示，13 个模块全部可点选。NAV_VIEWS 的顺序必须与
# NAV_GROUPS 一致，否则组标题会插在错误的位置。
NAV_GROUPS = [
    ("指挥", ["指挥中心"]),
    ("情报产品", ["成刊", "公告检索", "热频话题", "知识图谱", "中国地图"]),
    ("校验与复核", ["对比工作台", "关联确认", "字段复核"]),
    ("系统运维", ["采集运维", "灯塔采集", "来源健康", "进化仪表盘", "质量与备份"]),
    ("我的设置", ["总览", "订阅中心"]),
]
NAV_VIEWS = [view for _name, members in NAV_GROUPS for view in members]

# 侧栏与折叠态的图标：Material Icons 短名，交给 st.button(icon=) 原生渲染。
# 必须带 ":material/" 前缀，否则 Streamlit 抛 StreamlitAPIException 并让整页崩溃
# （曾导致侧栏之后的全部内容不渲染）。图标在按钮盒内，不需要任何覆盖层。
NAV_ICONS = {
    "指挥中心": ":material/space_dashboard:",
    "总览": ":material/dashboard:",
    "中国地图": ":material/public:",
    "热频话题": ":material/local_fire_department:",
    "成刊": ":material/newspaper:",
    "公告检索": ":material/search:",
    "采集运维": ":material/sync:",
    "灯塔采集": ":material/bedtime:",
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
        # 摘要落在 extra.summary；为空时带丢弃原因，供详情面板说明原因。
        "summary": (item.extra or {}).get("summary") or "",
        "summary_drop_reason": (item.extra or {}).get("summary_drop_reason") or "",
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


@st.cache_data(ttl=60, show_spinner=False)
def load_graph_sample() -> dict[str, Any]:
    with Database("data/beacon.db") as database:
        return graph_sample(database, max_nodes=250, max_edges=400)


@st.cache_data(ttl=300, show_spinner=False)
def load_health_summary() -> dict[str, Any]:
    """体检要扫全库，总览每次 rerender 都算一遍太贵；5 分钟缓存。"""
    import collector_ops as ops

    health = ops.read_health() or {}
    findings = health.get("findings") or []
    return {"findings": findings, "ok": sum(1 for item in findings if item.get("ok")), "total": len(findings)}


@st.cache_data(ttl=300, show_spinner=False)
def load_flag_counts() -> dict[str, int]:
    import quality_marks

    return (quality_marks.quality_flags(limit=1).get("counts") or {})


@st.cache_data(ttl=60, show_spinner=False)
def load_attachment_overview(limit: int = 400) -> dict[str, Any]:
    from sqlalchemy import func

    from storage.models import Attachment

    with Database("data/beacon.db") as database:
        with database.session() as session:
            total = int(session.query(func.count(Attachment.id)).scalar() or 0)
            status_rows = (
                session.query(Attachment.extract_status, func.count(Attachment.id))
                .group_by(Attachment.extract_status)
                .all()
            )
            type_rows = (
                session.query(Attachment.file_type, func.count(Attachment.id))
                .group_by(Attachment.file_type)
                .all()
            )
            recent = session.query(Attachment).order_by(Attachment.id.desc()).limit(limit).all()
            items = [
                {
                    "id": item.id,
                    "announcement_id": item.announcement_id,
                    "file_type": item.file_type,
                    "extract_status": item.extract_status,
                    "text_length": item.text_length,
                }
                for item in recent
            ]
    return {
        "total": total,
        "by_status": {str(key or "未知"): int(value) for key, value in status_rows},
        "by_type": {str(key or "未知"): int(value) for key, value in type_rows},
        "items": items,
    }


@st.cache_data(ttl=60, show_spinner=False)
def load_feedback_overview(limit: int = 100) -> dict[str, Any]:
    from storage.models import Feedback

    with Database("data/beacon.db") as database:
        with database.session() as session:
            total = int(session.query(Feedback).count())
            rows = session.query(Feedback).order_by(Feedback.created_at.desc()).limit(limit).all()
            items = [
                {
                    "id": item.id,
                    "announcement_id": item.announcement_id,
                    "useful": bool(item.useful),
                    "comment": item.comment or "",
                    "created_at": item.created_at.isoformat() if item.created_at else "",
                }
                for item in rows
            ]
    return {"total": total, "items": items}


def _load_theme_pref() -> str:
    """从本地文件读取上次选择的主题，不存在则返回默认。"""
    try:
        pref_path = Path("data/theme_pref.json")
        if pref_path.exists():
            tid = json.loads(pref_path.read_text(encoding="utf-8")).get("theme", "")
            if tid in THEME_ORDER:
                return tid
    except Exception:
        pass
    return default_theme()


def _save_theme_pref(theme_id: str) -> None:
    """把主题选择写入本地文件，刷新后仍保持。"""
    try:
        pref_path = Path("data/theme_pref.json")
        pref_path.parent.mkdir(parents=True, exist_ok=True)
        pref_path.write_text(json.dumps({"theme": theme_id}), encoding="utf-8")
    except Exception:
        pass


def inject_styles() -> None:
    if "theme" not in st.session_state:
        st.session_state["theme"] = _load_theme_pref()
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
        html {{ scroll-behavior:smooth; overflow-y:auto !important; }}
        body {{ overflow-y:visible !important; height:auto !important; }}

        /* ═══════════════════════════════════════════════
           滚动根因修复：html/body 作为滚动容器，
           所有 Streamlit 中间容器放行 overflow:visible 让内容穿透到 body
           ═══════════════════════════════════════════════ */
        html, body {{
          overflow-y:auto !important;
          overflow-x:hidden !important;
          height:auto !important;
          max-height:none !important;
        }}
        [data-testid="stApp"],
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        section[data-testid="stMain"],
        [data-testid="stHorizontalBlock"],
        [data-testid="stVerticalBlock"],
        [data-testid="stPageContainer"],
        div[data-testid="stColumn"],
        .block-container,
        .st-key-main-content,
        div.st-key-main-content > div {{
          overflow:visible !important;
          height:auto !important;
          max-height:none !important;
        }}

        /* 图表容器不能用 height:auto，否则 bar_chart iframe 无限撑高 */
        [data-testid="stVegaLiteChart"],
        [data-testid="stVegaLiteChartContainer"],
        [data-testid="stChartContainer"],
        .stVegaLiteChart,
        .stVegaLiteChartContainer,
        .stChartContainer {{
          overflow:hidden !important;
          max-height:450px !important;
        }}
        [data-testid="stVegaLiteChart"] iframe,
        [data-testid="stVegaLiteChartContainer"] iframe,
        [data-testid="stChartContainer"] iframe,
        .stVegaLiteChart iframe,
        .stVegaLiteChartContainer iframe,
        .stChartContainer iframe {{
          max-height:400px !important;
        }}

        [data-testid="stAppViewContainer"] {{
          background:var(--ground); color:var(--ink); position:relative;
          font-family:'Manrope','SF Pro Display',system-ui,sans-serif;
          min-height:100vh;
        }}
        /* 列容器不拉伸侧栏高度 */
        [data-testid="stHorizontalBlock"] {{
          align-items:flex-start !important;
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
          max-width:1600px; padding:.5rem 1.2rem .6rem; position:relative; z-index:1;
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
          position:relative; overflow:hidden;
          display:flex; justify-content:space-between; align-items:center; gap:1rem;
          padding:.62rem 1.1rem; margin-bottom:.7rem;
          background:var(--panel-glass-deep);
          backdrop-filter:blur(18px) saturate(1.3);
          -webkit-backdrop-filter:blur(18px) saturate(1.3);
          border:1px solid var(--line); border-radius:.7rem;
          box-shadow:0 6px 22px var(--shadow-deep), inset 0 1px 0 rgba(255,255,255,0.04);
        }}
        /* 左侧一道极细的强调色渐变，给整条栏一个视觉锚点。
           overflow:hidden + inset 的圆角让渐变贴着圆角收边，不会溢出成方角。 */
        .topbar::before {{
          content:''; position:absolute; left:0; top:0; bottom:0; width:2px;
          background:linear-gradient(180deg,var(--accent),var(--accent-2));
          opacity:.7; pointer-events:none;
        }}
        .topbar-left {{ display:flex; align-items:center; gap:.6rem; min-width:0; }}
        /* 「返回默认」用和 .badge.ok 同一套视觉语言（细描边 + 淡底 + 微光），
           只是它是可点的。只用 var(--accent*) 系列，所以六套主题自动跟随。 */
        .topbar-reset {{
          display:inline-flex; align-items:center; gap:.4rem;
          padding:.3rem .74rem; border-radius:.4rem;
          border:1px solid rgba(var(--accent_rgb),.34);
          background:rgba(var(--accent_rgb),.10);
          color:var(--accent); font-size:.72rem; font-weight:700; letter-spacing:.02em;
          white-space:nowrap; text-decoration:none; cursor:pointer;
          box-shadow:0 1px 0 rgba(255,255,255,0.03) inset;
          transition:background .18s, box-shadow .18s, transform .18s, border-color .18s;
        }}
        .topbar-reset:hover {{
          background:rgba(var(--accent_rgb),.20);
          border-color:rgba(var(--accent_rgb),.55);
          box-shadow:0 0 12px rgba(var(--accent_rgb),.38);
          transform:translateY(-1px);
        }}
        .topbar-reset:active {{ transform:translateY(0); }}
        .topbar-reset .ico {{ font-size:.86em; line-height:1; opacity:.9; }}
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
        .side-group {{
          color:var(--side-muted); font-size:.62rem; letter-spacing:.12em; font-weight:700;
          text-transform:uppercase; margin:.5rem 0 .15rem .3rem;
        }}
        .side-stats {{ display:flex; flex-direction:column; gap:.2rem; }}
        .st-key-side .aux-stat {{
          color:var(--side-muted); font-size:.66rem;
        }}
        .st-key-side .aux-stat b {{
          color:var(--side-ink); font-size:.74rem;
        }}
        /* 侧栏整体滚动：品牌/状态/用户卡固定，菜单区独立滚动。
           100vh 不够 —— 上方还有 56px 偏移 + 49px 工具带，侧栏一高就把整页顶出
           滚动条，底部露一条空白。跟主区面板同高，滚动条默认隐藏。 */
        /* 侧栏整体是 flex 列：品牌/状态/用户卡固定，只有中间的菜单区滚动。
           min-height:0 是 flex 子项能真正收缩的前提，缺了它 overflow 不会生效。 */
        .st-key-side {{
          display:flex; flex-direction:column;
          background:linear-gradient(180deg, var(--side-bg) 0%, var(--side-bg-2) 100%);
          color:var(--side-ink);
          border-right:1px solid rgba(var(--accent_rgb),0.34);
          border-radius:0 .5rem .5rem 0;
          padding:.5rem .45rem;
          box-shadow:2px 0 18px rgba(0,0,0,0.3);
          position:sticky; top:0;
          height:calc(100vh - 124px) !important; max-height:calc(100vh - 124px) !important;
          overflow:hidden !important;
        }}
        /* Streamlit 默认给侧栏每个元素 16px 间距，7 段就是 112px 白地，够把底下的
           用户卡顶出可视区。侧栏内统一压到最小。 */
        .st-key-side [data-testid="stVerticalBlock"],
        .st-key-side [data-testid="stLayoutWrapper"] {{ gap:.1rem; }}
        /* 菜单区自己滚动。用 max-height 而不是 flex:1 —— Streamlit 会在侧栏和菜单之间
           塞一层 stLayoutWrapper，flex 收缩传不进去，结果是菜单按内容高度长到 882px，
           把下面的运行状态/备用区/用户卡顶出侧栏被 overflow:hidden 裁掉。
           430px = 品牌 + 功能模块 + 运行状态 + 备用区 + 用户卡这些固定部分的高度。 */
        /* 菜单区自己滚动。不用 max-height 算像素 —— Streamlit 会在侧栏和菜单之间插一层
           stLayoutWrapper，flex 收缩传不进去，菜单只能按内容高度长出去，把底下的
           运行状态/备用区/用户卡顶出侧栏。display:contents 去掉这个中间盒子，菜单
           才真正成为侧栏的弹性子项，剩下的高度自动归它，不需要任何魔法数字。 */
        .st-key-side > [data-testid="stLayoutWrapper"]:has(.st-key-side_menu) {{
          display:contents;
        }}
        .st-key-side_menu {{
          flex:1 1 auto; min-height:0;
          max-height:none !important;
          overflow-y:auto !important; overflow-x:hidden;
          gap:.1rem; padding-right:.15rem;
        }}
        /* 品牌/状态/用户卡这些固定部分不许被压扁 */
        .st-key-side > *:not(.st-key-side_menu) {{ flex:0 0 auto; }}
        .st-key-side_menu::-webkit-scrollbar {{ width:6px; }}
        .st-key-side_menu::-webkit-scrollbar-thumb {{
          background:rgba(255,255,255,0.28); border-radius:3px;
        }}
        .st-key-side_menu::-webkit-scrollbar-track {{
          background:rgba(255,255,255,0.06); border-radius:3px;
        }}
        /* 标准属性一起给：::-webkit-scrollbar 在部分内核/无头环境下不画出来，
           菜单又高又没滚动条就退回"看不见下面还有按钮、点了没反应"。 */
        .st-key-side_menu {{
          scrollbar-width:thin;
          scrollbar-color:rgba(255,255,255,0.32) rgba(255,255,255,0.06);
        }}
        /* 去掉 Streamlit 自带的元素间距，这里自己控制节奏 */
        .st-key-side_menu [data-testid="stElementContainer"] {{ margin:0; gap:0; }}
        .st-key-side_menu [data-testid="stLayoutWrapper"] {{ margin:0; gap:.1rem; }}
        .st-key-side_menu [data-testid="stExpander"] > details > div {{ padding:.1rem 0 .15rem; }}
        /* Streamlit 自带的折叠箭头是 stIconMaterial（图标字体），它和下面 ::before
           画的 ▸ 会并排出现两个箭头。留一个自绘的，另一个藏掉。 */
        .st-key-side [data-testid="stExpander"] summary [data-testid="stIconMaterial"] {{
          display:none !important;
        }}
        /* st.expander 主题化 — 折叠分组融入侧栏拼色 */
        .st-key-side [data-testid="stExpander"] {{
          background:rgba(var(--accent_rgb),0.05) !important;
          border:1px solid rgba(var(--accent_rgb),0.28) !important;
          border-radius:.5rem !important;
          margin:0 0 .15rem !important;
          overflow:hidden;
        }}
        .st-key-side [data-testid="stExpander"]:hover {{
          border-color:rgba(var(--accent_rgb),0.5) !important;
        }}
        .st-key-side [data-testid="stExpander"] > details > summary {{
          color:var(--side-ink) !important;
          font-size:.72rem !important; font-weight:700 !important;
          letter-spacing:.03em; text-transform:none;
          padding:.25rem .5rem !important;
          background:transparent !important; border:none !important;
          list-style:none;
        }}
        .st-key-side [data-testid="stExpander"] > details > summary::-webkit-details-marker {{ display:none; }}
        .st-key-side [data-testid="stExpander"] > details > summary::before {{
          content:'▸'; color:var(--side-accent); margin-right:.4rem;
          font-size:.7rem; transition:transform .2s; display:inline-block;
        }}
        .st-key-side [data-testid="stExpander"] > details[open] > summary::before {{
          transform:rotate(90deg);
        }}
        .st-key-side [data-testid="stExpander"] > details[open] > summary {{
          color:var(--side-accent) !important;
          border-bottom:1px solid rgba(var(--accent_rgb),0.22);
        }}
        /* 折叠内容不再做淡入位移：侧栏每次 rerun 都会重放这段动画，按钮在点击落点
           底下平移 4px，手快的用户第一下就打偏。折叠本身已有 chevron 反馈。 */
        .st-key-side [data-testid="stExpander"] > details > div {{
          animation:none;
        }}
        /* 折叠头过渡 */
        .st-key-side [data-testid="stExpander"] > details > summary {{
          transition:color .2s, background .2s;
        }}
        .st-key-side [data-testid="stExpander"] > details > summary:hover {{
          background:rgba(255,255,255,0.05) !important;
        }}
        /* st.button has a native icon= parameter (Material Icons). The glyph font is
            loaded from Google's CDN below; without it the ligature renders as the
            literal text "dashboard总览". 行高压到 1.7rem：14 个模块 + 4 个分组头要能
            一次装进侧栏，装不下就得滚动，而滚动就会让点击落点漂移。 */
        .st-key-side [data-testid="stButton"] button {{
          justify-content:flex-start; text-align:left; font-size:.75rem;
          padding:.14rem .45rem; min-height:1.7rem; width:100%;
        }}
        /* 去掉 Streamlit 按钮自带的大外边距，这里自己控制节奏 */
        .st-key-side [data-testid="stButton"] {{ margin:0 0 .12rem; }}
        .st-key-side [data-testid="stExpander"] [data-testid="stButton"] {{ margin:0 .3rem .12rem; }}
        .st-key-side [data-testid="stIconMaterial"] {{
          font-family:'Material Symbols Rounded','Material Icons',sans-serif;
          font-size:.95rem; margin-right:.4rem; line-height:1;
          direction:ltr; display:inline-block; white-space:nowrap;
          word-wrap:normal; font-weight:normal; font-style:normal;
          font-feature-settings:'liga'; -webkit-font-smoothing:antialiased;
        }}
        /* 按钮涟漪动效 */
        .st-key-side [data-testid="stButton"] button {{
          position:relative;overflow:hidden;
        }}
        .st-key-side [data-testid="stButton"] button::after {{
          content:'';position:absolute;inset:0;border-radius:inherit;
          background:radial-gradient(circle at var(--ripple-x,50%) var(--ripple-y,50%),
            rgba(255,255,255,0.12) 0%,transparent 60%);
          opacity:0;transition:opacity .3s;
          pointer-events:none;
        }}
        .st-key-side [data-testid="stButton"]:active button::after {{
          opacity:1;transition:opacity .15s;
        }}
        /* 主内容区按钮动效 */
        .st-key-main-content [data-testid="stButton"] button {{
          transition:all .2s cubic-bezier(0.34,1.56,0.64,1);
        }}
        .st-key-main-content [data-testid="stButton"]:hover button {{
          transform:translateY(-1px);
          box-shadow:0 4px 14px rgba(var(--accent_rgb),0.2) !important;
        }}
        .st-key-main-content [data-testid="stButton"]:active button {{
          transform:scale(0.97);
        }}
        /* 顶部 KPI 卡片动效 */
        .kpi-card {{
          transition:all .3s cubic-bezier(0.34,1.56,0.64,1);
        }}
        .kpi-card:hover {{
          transform:translateY(-3px);
          box-shadow:0 8px 24px rgba(0,0,0,0.35),0 0 20px rgba(var(--accent_rgb),0.08) !important;
        }}
        /* 侧栏所有按钮 — 统一拼色主题 */
        .st-key-side [data-testid="stButton"] button {{
          background:transparent !important; border:none !important;
          box-shadow:none !important; color:var(--side-muted) !important;
          font-weight:600; transition:color .25s ease, background .25s ease, transform .15s ease, box-shadow .25s ease;
        }}
        .st-key-side [data-testid="stButton"]:hover button {{
          color:var(--side-ink) !important;
          background:rgba(255,255,255,0.06) !important;
          transform:translateY(-1px);
        }}
        .st-key-side [data-testid="stButton"]:active button {{
          transform:scale(0.96);
          transition:transform .08s ease;
        }}
        /* 活动导航项（type=primary）— 强调色高亮 */
        .st-key-side [data-testid="stButton"][kind="primary"] button {{
          color:var(--side-accent) !important;
          background:rgba(255,255,255,0.1) !important;
          font-weight:800 !important;
          box-shadow:inset 3px 0 0 var(--side-accent), 0 0 12px rgba(255,255,255,0.08) !important;
          transition:all .25s ease;
        }}
        .st-key-side [data-testid="stButton"][kind="primary"] [data-testid="stIconMaterial"] {{
          color:var(--side-accent) !important;
        }}
        .st-key-side [data-testid="stButton"][kind="primary"]:hover button {{
          background:rgba(255,255,255,0.14) !important;
          box-shadow:inset 3px 0 0 var(--side-accent), 0 0 16px rgba(255,255,255,0.12) !important;
          transform:translateY(-1px);
        }}
        /* 备用区开关 — 主题强调色描边按钮 */
        .st-key-side_aux_toggle [data-testid="stButton"] button {{
          color:var(--side-accent) !important; font-weight:700 !important;
          background:rgba(var(--accent_rgb),0.10) !important;
          border:1px solid rgba(var(--accent_rgb),0.45) !important;
          border-radius:.45rem; justify-content:center !important;
        }}
        .st-key-side_aux_toggle [data-testid="stIconMaterial"] {{ color:var(--side-accent) !important; }}
        .st-key-side_aux_toggle [data-testid="stButton"]:hover button {{
          background:rgba(var(--accent_rgb),0.20) !important;
          border-color:var(--side-accent) !important;
          box-shadow:0 0 12px rgba(var(--accent_rgb),0.30) !important;
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
          width:2.4rem; height:2.4rem; border-radius:.5rem;
          background:rgba(255,255,255,0.16); display:grid; place-items:center; overflow:hidden;
        }}
        .side-brand .brand-mark svg {{ width:62%; height:62%; display:block; color:var(--side-accent); filter:drop-shadow(0 0 4px var(--side-accent)); }}
        .side-title {{ color:var(--side-ink); font-weight:800; font-size:1.15rem; letter-spacing:.02em; line-height:1.2; white-space:nowrap; }}
        .side-title-accent {{ color:var(--side-accent); font-weight:800; }}
        .side-online {{
          display:flex; align-items:center; gap:.4rem;
          padding:.1rem .3rem .55rem; color:var(--side-muted); font-size:.66rem;
        }}
        .side-online .pulse-dot {{ background:var(--side-ink); box-shadow:0 0 0 .18rem rgba(255,255,255,0.14); }}
        .user-card {{ margin-top:auto; padding-top:.4rem; border-top:1px solid var(--side-line); }}
        .user-row {{ display:flex; align-items:center; gap:.5rem; padding:.2rem .2rem .4rem; }}
        .user-avatar {{
          width:2rem; height:2rem; border-radius:50%;
          background:rgba(255,255,255,0.18); color:var(--side-ink);
          display:grid; place-items:center; font-weight:800; font-size:.82rem;
        }}
        .user-name {{ color:var(--side-ink); font-weight:700; font-size:.82rem; }}
        .user-role {{ color:var(--side-muted); font-size:.62rem; }}

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
        .user-card {{ margin-top:auto; padding-top:.4rem; border-top:1px solid var(--side-line); }}
        .user-row {{ display:flex; align-items:center; gap:.5rem; padding:.2rem .2rem .4rem; }}
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
        /* 四问区（总览页）：指标卡按内容自适应列数，不再 5 张卡挤成 4+1 孤行。
           这里不再自带滚动框 —— 首页已经有外层 .st-key-page-scroll，再套一层就是
           三层嵌套滚动条，滚轮分不清该推动哪一层。 */
        .st-key-home-questions .metric-row {{ grid-template-columns:repeat(auto-fit,minmax(146px,1fr)); gap:.55rem; }}
        .st-key-home-questions .metric {{ height:5.2rem; min-height:5.2rem; display:flex; flex-direction:column; justify-content:center; }}

        /* ═══════════════════════════════════════════════
           内页统一滚动面板 — 13 个视图共用一个 key
           ═══════════════════════════════════════════════
           每页内容长短不一（公告检索 7600px、知识图谱 5400px），页面被越拉越长、
           找不到页脚。统一收进一个视口高度的滚动面板，滚动条走面板内部。
           高度扣掉 124px = 顶部 56px 偏移 + 工具带 49px + 间距 ~9px + 底部留白 10px，
           刚好占满视口，底部不再留一条空白。必须带 !important：上面
           [data-testid="stVerticalBlock"] 的兜底是 overflow:visible !important。 */
        /* 高度用 max-height + min-height 同值：Streamlit 的竖向块是 flex item，
           只写 height 会被 min-height:auto（内容高度）顶开；只写 max-height 又会让
           短页面底部留一大片空白。同值写法 = 长内容内部滚动、短内容撑满视口。 */
        .st-key-page-scroll {{ max-height:calc(100vh - 124px) !important; min-height:calc(100vh - 124px) !important; overflow-y:auto !important; overflow-x:hidden !important; padding-right:.35rem; }}
        /* 滚动条默认透明，鼠标移入面板才显形（CSS 版的自动隐藏） */
        .st-key-page-scroll::-webkit-scrollbar {{ width:4px; }}
        .st-key-page-scroll::-webkit-scrollbar-thumb {{ background:transparent; border-radius:2px; }}
        .st-key-page-scroll:hover::-webkit-scrollbar-thumb {{ background:rgba(var(--accent_rgb),.38); }}
        .st-key-page-scroll::-webkit-scrollbar-track {{ background:transparent; }}
        /* 摘要/要点限高：卡片等高，一列下来才整齐（超出部分裁掉，不无限往下长） */
        .st-key-page-scroll .record-summary {{ max-height:5.3rem; overflow:hidden; }}
        .st-key-page-scroll .record-points {{ max-height:2.8rem; overflow:hidden; }}
        /* 长文本预览：固定高度 + 内部滚动（滚动条同样默认隐藏） */
        .scroll-box {{ max-height:14rem; overflow-y:auto; padding-right:.3rem; font-size:.8rem; line-height:1.7; color:var(--ink); }}
        .scroll-box::-webkit-scrollbar {{ width:4px; }}
        .scroll-box::-webkit-scrollbar-thumb {{ background:transparent; border-radius:2px; }}
        .scroll-box:hover::-webkit-scrollbar-thumb {{ background:rgba(var(--accent_rgb),.32); }}
        /* 记录卡内部：徽标行 + 卡内"打开原文"链接（卡整体是一个盒子） */
        .record-badges {{ margin-top:.55rem; display:flex; gap:.4rem; }}
        .record-open {{
          display:inline-block; margin-top:.6rem; padding:.24rem .7rem; border-radius:.4rem;
          border:1px solid rgba(var(--accent_rgb),.45); background:rgba(var(--accent_rgb),.10);
          color:var(--accent); font-size:.72rem; font-weight:700; text-decoration:none;
        }}
        .record-open:hover {{ background:rgba(var(--accent_rgb),.2); }}
        /* 错误码块定高：来源健康页 5 个 st.code 原本高低差一大截 */
        .st-key-page-scroll [data-testid="stCode"] pre,
        .st-key-page-scroll [data-testid="stCode"] code {{ max-height:4.6rem; overflow:auto; }}
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
        .record-tag {{
          display:inline-block; margin-right:.4rem; padding:.02rem .38rem; border-radius:4px;
          font-size:.62rem; font-weight:700; letter-spacing:.04em; vertical-align:.08rem;
          color:var(--accent); background:rgba(var(--accent_rgb),0.14);
          border:1px solid rgba(var(--accent_rgb),0.30);
        }}
        .record-summary {{
          color:var(--ink); font-size:.8rem; line-height:1.72; margin-top:.6rem;
          padding:.55rem .7rem; border-radius:.5rem;
          background:rgba(var(--accent_rgb),0.05); border-left:2px solid rgba(var(--accent_rgb),0.45);
        }}
        .record-summary.empty {{ color:var(--muted); font-style:italic; }}


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

        /* ═══════════════════════════════════════════════
           动效增强：卡片悬停 + 滚动 reveal + 数字 countUp
           ═══════════════════════════════════════════════ */
        /* 卡片悬停上浮 */
        .record-card, .heat-card, .metric, .panel {{
          transition: transform 0.25s cubic-bezier(0.4,0,0.2,1),
                      box-shadow 0.25s ease, border-color 0.25s ease;
        }}
        .record-card:hover, .heat-card:hover, .metric:hover, .panel:hover {{
          transform: translateY(-3px);
          box-shadow: 0 8px 24px rgba(0,0,0,0.12), 0 2px 6px rgba(0,0,0,0.08);
          border-color: rgba(var(--accent_rgb),0.3);
          z-index: 2;
        }}
        /* 滚动 reveal 动画 */
        @keyframes reveal-up {{
          from {{ opacity:0; transform:translateY(20px); }}
          to {{ opacity:1; transform:translateY(0); }}
        }}
        .reveal {{ animation:reveal-up 0.4s ease-out both; }}
        .reveal-pending {{ opacity:0; }}
        /* 数字 countUp 容器 */
        .metric-value.counting {{ color:var(--accent); transition:color 0.3s; }}

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

    # JS 兜底：确保 html/body 可滚动，中间容器不截断
    st.markdown(
        r"""<script>
        (function() {
          // 只在值真的不对时才写样式：写同样的 !important 也会让整页重新布局，
          // 每 2 秒无条件重写一遍 = 持续强制回流，点击发木的元凶。
          function setIfDiff(el, prop, value, prio) {
            if (el.style.getPropertyValue(prop) !== value) el.style.setProperty(prop, value, prio);
          }
          function fixScroll() {
            setIfDiff(document.documentElement, 'overflow-y', 'auto', 'important');
            setIfDiff(document.documentElement, 'height', 'auto', 'important');
            setIfDiff(document.documentElement, 'max-height', 'none', 'important');
            setIfDiff(document.body, 'overflow-y', 'auto', 'important');
            setIfDiff(document.body, 'height', 'auto', 'important');
            setIfDiff(document.body, 'max-height', 'none', 'important');
            var sel = '[data-testid="stApp"],[data-testid="stAppViewContainer"],[data-testid="stMain"],section[data-testid="stMain"],[data-testid="stHorizontalBlock"],[data-testid="stVerticalBlock"],[data-testid="stPageContainer"],div[data-testid="stColumn"],.block-container,.st-key-main-content,div.st-key-main-content > div';
            document.querySelectorAll(sel).forEach(function(el) {
              // 跳过图表容器，否则 iframe 无限撑高
              if (el.closest('[data-testid="stVegaLiteChart"],[data-testid="stVegaLiteChartContainer"],[data-testid="stChartContainer"],.stVegaLiteChart,.stVegaLiteChartContainer,.stChartContainer')) return;
              setIfDiff(el, 'overflow', 'visible', 'important');
              setIfDiff(el, 'height', 'auto', 'important');
              setIfDiff(el, 'max-height', 'none', 'important');
            });
          }

          /* ── countUp：数字滚动动画 ── */
          function countUp(el) {
            if (el.dataset.counted) return;
            var raw = el.textContent.trim();
            var target = parseFloat(raw.replace(/,/g,''));
            if (isNaN(target) || raw.length < 2) return;
            // 只对纯数字做 countUp（跳过日期、百分比等）
            if (!/^\d[\d,.]*$/.test(raw.replace(/\s/g,''))) return;
            el.dataset.counted = '1';
            var suffix = '';
            var dur = 600, start = 0, t0 = null;
            function step(ts) {
              if (!t0) t0 = ts;
              var p = Math.min((ts - t0) / dur, 1);
              var eased = 1 - Math.pow(1 - p, 3);
              var val = Math.round(start + (target - start) * eased);
              el.textContent = val.toLocaleString() + suffix;
              if (p < 1) requestAnimationFrame(step);
              else el.textContent = target.toLocaleString() + suffix;
            }
            requestAnimationFrame(step);
          }

          /* ── reveal：滚动触发淡入 ── */
          var revealObserver = new IntersectionObserver(function(entries) {
            entries.forEach(function(e) {
              if (e.isIntersecting) {
                e.target.classList.add('reveal');
                e.target.classList.remove('reveal-pending');
                revealObserver.unobserve(e.target);
              }
            });
          }, {threshold: 0.1, rootMargin: '0px 0px -50px 0px'});

          function initEffects() {
            // countUp
            document.querySelectorAll('.metric-value').forEach(function(el) {
              if (!el.dataset.counted) countUp(el);
            });
            // reveal：给卡片加 reveal-pending，进入视口时触发
            document.querySelectorAll('.record-card, .heat-card, .metric, .panel').forEach(function(el) {
              if (!el.dataset.revealInit) {
                el.dataset.revealInit = '1';
                var r = el.getBoundingClientRect();
                if (r.top > window.innerHeight) {
                  el.classList.add('reveal-pending');
                  revealObserver.observe(el);
                }
              }
            });
          }

          if (document.readyState !== 'loading') { fixScroll(); initEffects(); }
          else document.addEventListener('DOMContentLoaded', function() { fixScroll(); initEffects(); });
          // 只盯子节点增删：连 style/class 属性一起盯会跟自己写的样式死循环，
          // 每帧都触发一轮全文档 querySelectorAll + 强制回流。
          var pending = false;
          function schedule() {
            if (pending) return;
            pending = true;
            requestAnimationFrame(function() { pending = false; fixScroll(); initEffects(); });
          }
          var mo = new MutationObserver(schedule);
          mo.observe(document.documentElement, {childList:true, subtree:true});
        })();
        </script>""",
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


def render_topbar() -> None:
    """统一工具带：全宽一条。首页第一栏。

    左侧是「返回默认」徽标按钮——和右侧「系统在线」用同一套视觉语言
    （细描边 + 淡底 + 微光），只是它可点。原先左侧的品牌文字和竖分隔线
    已按要求移除：第一栏要更干净，视觉重心交给动作和状态，标题交给侧栏和
    正文区，不在这一条里重复。
    """
    st.markdown(
        f"<div class='topbar entrance-fade'>"
        f"<div class='topbar-left'>"
        f"<a class='topbar-reset' href='?nav_view=总览&reset_theme=1' target='_parent'"
        f" title='重置到总览页 + 默认主题'>"
        f"<span class='ico'>↺</span><span>返回默认</span>"
        f"</a>"
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
    if action_col.button("运行一次采集", type="primary", width="stretch", key="run_collection_once"):
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
    if refresh_col.button("刷新视图", width="stretch", key="refresh_dashboard"):
        load_snapshot.clear()
        load_quality.clear()
        load_backups.clear()
        load_graph_summary.clear()
        st.rerun()
    export_col.download_button("导出公告数据", data=export_payload(records), file_name="beacon-announcements.json", mime="application/json", width="stretch", key="export_announcements")


SUMMARY_DROP_LABELS = {
    "chrome_only": "正文只剩页面框架，未提取到有效内容",
    "empty_body": "正文为空",
}


def summary_drop_label(reason: str) -> str:
    return SUMMARY_DROP_LABELS.get(reason, "未生成摘要")


def _clean_summary(text: str) -> str:
    """过滤摘要中的 404/错误页面文本"""
    if not text:
        return ""
    import re as _re
    # 检测 404 错误页面特征文本
    error_patterns = [
        r"404\s*Error.*请点击.*刷新",
        r"页面出错啦.*请点击.*返回",
        r"404.*页面出错",
    ]
    for pat in error_patterns:
        if _re.search(pat, text, _re.IGNORECASE):
            return ""
    return text.strip()


def _safe_link(url: Any) -> str:
    """只放行 http/https：手写 <a href> 时别把 javascript: 之类放进页面。"""
    text = str(url or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def render_record(record: dict[str, Any], compact: bool = False) -> None:
    points = record.get("key_points") or []
    summary = _clean_summary(str(record.get("summary") or ""))
    drop_reason = str(record.get("summary_drop_reason") or "").strip()
    meta = " · ".join(str(value) for value in [record.get("issuing_body"), record.get("publish_date"), record.get("gov_level"), record.get("category_l1")] if value)
    extras = []
    if record.get("amount"):
        extras.append(f"金额 {record['amount']}")
    if record.get("deadline"):
        extras.append(f"截止 {record['deadline']}")
    if extras:
        meta = f"{meta} · {' · '.join(extras)}" if meta else " · ".join(extras)

    # 整张卡拼成一次 markdown：早先拆成 4~5 次 st.markdown，浏览器会把未闭合的
    # .record-card 提前补上，标题/摘要/要点/按钮变成四个独立方块，高低不齐。
    parts = [
        f"<div class='record-title'>{html.escape(str(record.get('title') or '未命名公告'))}</div>",
        f"<div class='record-meta'>{html.escape(meta)}</div>",
    ]
    if summary:
        parts.append(
            "<div class='record-summary'><span class='record-tag'>摘要</span>"
            f"{html.escape(summary)}</div>"
        )
    elif not compact:
        parts.append(
            f"<div class='record-summary empty'>暂无摘要：{html.escape(summary_drop_label(drop_reason))}</div>"
        )
    if points and not compact:
        parts.append(
            "<div class='record-points'><span class='record-tag'>要点</span>"
            f"{html.escape('；'.join(str(point) for point in points[:3]))}</div>"
        )
    badges = []
    if record.get("focus_tracked"):
        badges.append("<span class='badge ok'>重点跟踪</span>")
    if record.get("needs_review"):
        badges.append("<span class='badge warn'>待复核</span>")
    if badges:
        parts.append(f"<div class='record-badges'>{''.join(badges)}</div>")
    href = _safe_link(record.get("source_url"))
    if href:
        parts.append(
            f"<a class='record-open' href='{html.escape(href, quote=True)}' target='_blank' rel='noreferrer'>打开原文</a>"
        )
    st.markdown(f"<div class='record-card'>{''.join(parts)}</div>", unsafe_allow_html=True)


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

    # ── 结果分布概览条：让用户快速感知匹配数据的结构 ──
    if matches:
        from collections import Counter as _Ctr
        _cat_dist = _Ctr(m.get("category_l1") or "其他" for m in matches).most_common(6)
        _prov_dist = _Ctr(m.get("region_province") or "未知" for m in matches).most_common(8)
        _dates = [str(m.get("publish_date") or "")[:10] for m in matches if m.get("publish_date")]
        _date_min = min(_dates) if _dates else "—"
        _date_max = max(_dates) if _dates else "—"
        ov1, ov2, ov3 = st.columns(3, gap="medium")
        with ov1:
            st.markdown(f"**{len(matches):,}** 条匹配 · 时间跨度 `{_date_min}` ~ `{_date_max}`")
        with ov2:
            if _cat_dist:
                render_echart({
                    "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
                    "series": [{
                        "type": "pie", "radius": ["30%", "60%"],
                        "data": [{"name": k, "value": v} for k, v in _cat_dist],
                        "label": {"fontSize": 9, "formatter": "{b}"},
                    }],
                }, height=140, key="ann_cat_overview")
        with ov3:
            if _prov_dist:
                render_echart({
                    "tooltip": {"trigger": "axis"},
                    "grid": {"left": 60, "right": 10, "top": 5, "bottom": 5},
                    "xAxis": {"type": "value", "splitLine": {"show": False}},
                    "yAxis": {"type": "category", "data": [p for p, _ in _prov_dist], "axisLabel": {"fontSize": 9}},
                    "series": [{"type": "bar", "data": [v for _, v in _prov_dist], "barWidth": 8, "itemStyle": {"color": "#5B8FF9"}}],
                }, height=140, key="ann_prov_overview")

    left, right = st.columns([2.2, 1], gap="large")
    with left:
        if not matches:
            st.markdown("<div class='empty'>当前条件下没有匹配结果。试试放宽地区或分类。</div>", unsafe_allow_html=True)
            return
        # 分页：每页 20 条，避免 DOM 爆炸
        PER_PAGE = 20
        total = len(matches)
        total_pages = (total + PER_PAGE - 1) // PER_PAGE
        page_num = st.session_state.get("ann_page", 1)
        if page_num > total_pages:
            page_num = 1
        start = (page_num - 1) * PER_PAGE
        page_items = matches[start:start + PER_PAGE]
        for record in page_items:
            render_record(record)
        # 分页控件
        if total_pages > 1:
            pc1, pc2, pc3 = st.columns([1, 2, 1])
            with pc1:
                if st.button("‹ 上一页", key="ann_prev", disabled=page_num <= 1, width="stretch"):
                    st.session_state["ann_page"] = page_num - 1
                    st.rerun()
            with pc2:
                st.markdown(f"<div style='text-align:center;padding:.5rem;color:var(--ink);opacity:.7'>第 <b>{page_num}</b> / {total_pages} 页 · 共 {total:,} 条</div>", unsafe_allow_html=True)
            with pc3:
                if st.button("下一页 ›", key="ann_next", disabled=page_num >= total_pages, width="stretch"):
                    st.session_state["ann_page"] = page_num + 1
                    st.rerun()
    with right:
        st.markdown("<div class='panel'><div class='section-label'>当前结果</div><div class='metric-value'>{:,}</div><div class='metric-note'>条匹配公告</div></div>".format(len(matches)), unsafe_allow_html=True)
        st.download_button("导出筛选结果", data=export_payload(matches), file_name="beacon-matches.json", mime="application/json", width="stretch", key="export_matches")
        st.markdown("#### 匹配说明")
        st.caption("结果只使用已入库字段和现有规则，不生成模型理由。")
        if matches:
            selected = st.selectbox("查看公告", [str(item["id"]) for item in matches], format_func=lambda value: next((str(item.get("title") or "未命名公告") for item in matches if str(item["id"]) == value), value), key="announcement_detail")
            record = next(item for item in matches if str(item["id"]) == selected)
            st.markdown(f"**{record.get('title') or '未命名公告'}**")
            header = " · ".join(str(value) for value in [record.get("issuing_body"), record.get("publish_date"), record.get("gov_level"), record.get("region_province")] if value)
            if header:
                st.caption(header)
            summary = str(record.get("summary") or "").strip()
            if summary:
                st.markdown("**摘要**")
                # 摘要越写越长，右栏不能被它顶着一路向下：固定高度 + 内部滚动
                st.markdown(
                    f"<div class='scroll-box'>{html.escape(summary)}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("暂无摘要：" + summary_drop_label(str(record.get("summary_drop_reason") or "")))
            points = record.get("key_points") or []
            if points:
                st.markdown("**要点**")
                st.markdown(
                    "<div class='scroll-box'>"
                    + "".join(f"<div>· {html.escape(str(point))}</div>" for point in points)
                    + "</div>",
                    unsafe_allow_html=True,
                )
            for label, key in [("适用人群", "target_group"), ("适用行业", "target_industry"), ("金额", "amount"), ("截止日期", "deadline")]:
                if record.get(key):
                    st.write(f"{label}：{record[key]}")
            if record.get("source_url"):
                st.link_button("打开原文", str(record["source_url"]), key=f"detail-source-{record.get('id')}")
            if record.get("reasons"):
                st.caption("匹配理由：" + "；".join(record["reasons"]))


def render_sources(sources: list[dict[str, Any]]) -> None:
    import collector_ops as ops

    st.markdown("<div class='section-label'>来源健康</div>", unsafe_allow_html=True)
    st.markdown("### 每个来源都有自己的节奏")

    # 顶部先答两个最要紧的问题：源头分布是否倾斜、哪些省有源却没启用。
    groups = ops.load_registry()
    registry = ops.summarize_registry(groups)
    warn = ops.skew_warning(registry)
    if warn:
        st.warning(warn)
    gaps = ops.provinces_without_enabled(groups)
    if gaps:
        st.caption(f"有源但未启用的省份（{len(gaps)}）：{'、'.join(gaps)}　—— 这些是下一批探测的优先目标。")

    failing = [item for item in sources if item.get("status") == "error"]
    worst = max((int(item.get("failures") or 0) for item in sources), default=0)
    upcoming = sorted((item for item in sources if item.get("next_due")), key=lambda item: str(item["next_due"]))
    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{len(sources)}</div>"
        f"<div class='metric-note'>有同步状态的源</div></div>"
        f"<div class='metric'><div class='metric-value'>{len(failing)}</div>"
        f"<div class='metric-note'>当前失败源</div></div>"
        f"<div class='metric'><div class='metric-value'>{worst}</div>"
        f"<div class='metric-note'>最大连续失败次数</div></div>"
        f"<div class='metric'><div class='metric-value'>{format_time(upcoming[0]['next_due']) if upcoming else '—'}</div>"
        f"<div class='metric-note'>最近一次到期</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # 成功率仪表盘 + 层级覆盖 + 失败排行
    total_sources = len(sources)
    ok_sources = sum(1 for item in sources if item.get("status") == "ok")
    failed_sources = sum(1 for item in sources if item.get("status") == "error")
    success_rate = round(ok_sources / total_sources * 100, 1) if total_sources else 0
    # 按行政层级统计
    level_counts: dict[str, int] = {}
    level_ok: dict[str, int] = {}
    for item in sources:
        lv = str(item.get("level") or "未知")
        level_counts[lv] = level_counts.get(lv, 0) + 1
        if item.get("status") == "ok":
            level_ok[lv] = level_ok.get(lv, 0) + 1
    level_order = ["national", "provincial", "municipal", "county", "未知"]
    level_labels = {"national": "国家", "provincial": "省级", "municipal": "市级", "county": "县级", "未知": "未知"}
    levels_present = [lv for lv in level_order if lv in level_counts]
    # 失败排行 top10
    fail_sorted = sorted(sources, key=lambda s: int(s.get("failures") or 0), reverse=True)[:10]
    fail_names = [str(s.get("name") or s.get("source_id") or "")[:14] for s in fail_sorted]
    fail_vals = [int(s.get("failures") or 0) for s in fail_sorted]

    col_g, col_l, col_f = st.columns(3, gap="medium")
    with col_g:
        st.markdown("#### 采集成功率")
        render_echart({
            "series": [{
                "type": "gauge",
                "startAngle": 200,
                "endAngle": -20,
                "min": 0,
                "max": 100,
                "radius": "90%",
                "progress": {"show": True, "width": 10, "itemStyle": {"color": "#5AD8A6" if success_rate >= 80 else "#F6BD16" if success_rate >= 50 else "#E8684A"}},
                "axisLine": {"lineStyle": {"width": 10, "color": [[1, "#e8e8e8"]]}},
                "pointer": {"show": False},
                "axisTick": {"show": False},
                "splitLine": {"show": False},
                "axisLabel": {"show": False},
                "detail": {
                    "valueAnimation": True,
                    "formatter": "{value}%",
                    "fontSize": 22,
                    "offsetCenter": [0, "10%"],
                },
                "title": {"show": True, "offsetCenter": [0, "40%"], "fontSize": 11, "color": "#999"},
                "data": [{"value": success_rate, "name": f"{ok_sources}/{total_sources} 源正常"}],
            }],
        }, height=200, key="src_gauge")
        st.caption(f"分母 = 全部 {total_sources} 个源（含禁用/待审），"
                   f"其中 {ok_sources} 正常、{failed_sources} 失败、"
                   f"{total_sources - ok_sources - failed_sources} 禁用/待审。")
    with col_l:
        st.markdown("#### 层级覆盖")
        render_echart({
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": 50, "right": 20, "top": 10, "bottom": 30},
            "xAxis": {"type": "category", "data": [level_labels.get(lv, lv) for lv in levels_present], "axisLabel": {"fontSize": 10}},
            "yAxis": {"type": "value", "splitLine": {"show": False}},
            "series": [
                {
                    "name": "总数",
                    "type": "bar",
                    "data": [level_counts.get(lv, 0) for lv in levels_present],
                    "itemStyle": {"color": "#5B8FF9", "borderRadius": [3, 3, 0, 0]},
                    "barWidth": "35%",
                },
                {
                    "name": "正常",
                    "type": "bar",
                    "data": [level_ok.get(lv, 0) for lv in levels_present],
                    "itemStyle": {"color": "#5AD8A6", "borderRadius": [3, 3, 0, 0]},
                    "barWidth": "35%",
                },
            ],
            "legend": {"data": ["总数", "正常"], "top": 0, "textStyle": {"fontSize": 10}},
        }, height=200, key="src_level")
    with col_f:
        st.markdown("#### 失败排行 TOP10")
        render_echart({
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": "50%", "right": 30, "top": 10, "bottom": 20},
            "xAxis": {"type": "value", "splitLine": {"show": False}},
            "yAxis": {"type": "category", "data": fail_names, "inverse": True, "axisLabel": {"fontSize": 9, "width": 70, "overflow": "truncate"}},
            "series": [{
                "type": "bar",
                "data": fail_vals,
                "itemStyle": {"color": "#E8684A", "borderRadius": [0, 3, 3, 0]},
            }],
        }, height=200, key="src_fail")

    # ── 省份健康矩阵：按省份聚合来源状态 ──
    prov_agg: dict[str, dict[str, int]] = {}
    for item in sources:
        prov = str(item.get("province") or item.get("jurisdiction") or "未知")
        if prov not in prov_agg:
            prov_agg[prov] = {"ok": 0, "err": 0, "total": 0}
        prov_agg[prov]["total"] += 1
        if item.get("status") == "ok":
            prov_agg[prov]["ok"] += 1
        elif item.get("status") == "error":
            prov_agg[prov]["err"] += 1
    prov_sorted = sorted(prov_agg.items(), key=lambda kv: kv[1]["total"], reverse=True)[:15]
    if prov_sorted:
        st.markdown("#### 省份来源健康矩阵（TOP 15）")
        st.caption("按来源数排序，绿色=正常，红色=异常；全异常省份需优先排查")
        prov_names = [p for p, _ in prov_sorted]
        ok_vals = [v["ok"] for _, v in prov_sorted]
        err_vals = [v["err"] for _, v in prov_sorted]
        render_echart({
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "legend": {"data": ["正常", "异常"], "top": 0, "textStyle": {"fontSize": 11}},
            "grid": {"left": 50, "right": 20, "top": 30, "bottom": 20},
            "xAxis": {"type": "category", "data": prov_names, "axisLabel": {"fontSize": 10, "rotate": 30}},
            "yAxis": {"type": "value", "splitLine": {"show": False}},
            "series": [
                {"name": "正常", "type": "bar", "stack": "total", "data": ok_vals, "itemStyle": {"color": "#5AD8A6"}, "barWidth": "40%"},
                {"name": "异常", "type": "bar", "stack": "total", "data": err_vals, "itemStyle": {"color": "#E8684A"}, "barWidth": "40%"},
            ],
        }, height=240, key="src_prov_matrix")

    filter_status = st.selectbox("状态筛选", ["全部", "正常", "失败", "待审核/待运行"], key="source_status_filter")
    mapping = {"正常": "ok", "失败": "error", "待审核/待运行": "pending"}
    filtered = sources if filter_status == "全部" else [item for item in sources if item.get("status") == mapping.get(filter_status, item.get("status"))]
    rows = []
    for item in filtered:
        rows.append({"来源": item["name"], "source_id": item["source_id"], "层级": item.get("level") or "—",
                     "频率": item.get("cadence") or "—", "状态": status_label(str(item.get("status") or "pending")),
                     "入库": item.get("records", 0), "连续失败": item.get("failures", 0),
                     "最近成功": format_time(item.get("last_success")), "下次到期": format_time(item.get("next_due")),
                     "错误摘要": (str(item.get("error") or "")[:120] or "—")})
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.markdown("<div class='empty'>暂无符合条件的来源。</div>", unsafe_allow_html=True)

    if failing:
        st.markdown("#### 需要关注")
        st.caption("错误原文放在代码块里，可直接选中复制。")
        for item in sorted(failing, key=lambda entry: -(int(entry.get("failures") or 0)))[:5]:
            st.markdown(f"**{item['name']}** · 连续失败 {int(item.get('failures') or 0)} 次 · 下次到期 {format_time(item.get('next_due'))}")
            st.code(str(item.get("error") or "采集失败（无错误详情）"), language="text")


def render_quality(quality: dict[str, Any], backups: list[dict[str, Any]], records: list[dict[str, Any]] | None = None) -> None:
    import quality_marks
    from chart_theme import status_color

    st.markdown("<div class='section-label'>质量与备份</div>", unsafe_allow_html=True)
    st.markdown("### 让数据质量可见、可追踪")

    counts = load_flag_counts()
    checked = counts.get("total") or 0

    # 顶部概览指标条（合并所有 section 的核心数字）
    attachments = load_attachment_overview()
    feedback = load_feedback_overview()
    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{checked:,}</div><div class='metric-note'>记录总数</div></div>"
        f"<div class='metric'><div class='metric-value'>{counts.get('clean', 0):,}</div><div class='metric-note'>无标记（可直接用）</div></div>"
        f"<div class='metric'><div class='metric-value'>{counts.get('uncategorized', 0):,}</div><div class='metric-note'>未分类</div></div>"
        f"<div class='metric'><div class='metric-value'>{attachments['total']:,}</div><div class='metric-note'>附件</div></div>"
        f"<div class='metric'><div class='metric-value'>{feedback['total']:,}</div><div class='metric-note'>反馈</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    tab_flags, tab_attach, tab_feedback, tab_backup = st.tabs([
        "质量标记", "附件提取", "反馈", "来源与备份",
    ])

    # ════════ Tab 1: 质量标记 ════════
    with tab_flags:
        # 标记分布环形图（替代纯表格）
        flag_data = [
            {"name": quality_marks.FLAG_LABELS[key], "value": counts.get(key, 0),
             "itemStyle": {"color": status_color("error" if counts.get(key, 0) > 0 else "ok")}}
            for key in quality_marks.FLAG_ORDER if counts.get(key, 0) > 0
        ]
        if flag_data:
            col_chart, col_table = st.columns([1, 1], gap="medium")
            with col_chart:
                render_echart({
                    "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                    "legend": {"bottom": 0, "textStyle": {"fontSize": 10}},
                    "series": [{
                        "type": "pie", "radius": ["40%", "65%"],
                        "data": flag_data,
                        "label": {"formatter": "{b}\n{c}", "fontSize": 9},
                    }],
                }, height=240, key="quality_flag_donut")
            with col_table:
                st.dataframe(
                    [{"标记": quality_marks.FLAG_LABELS[key], "条数": counts.get(key, 0),
                      "占比": f"{counts.get(key, 0) / checked:.1%}" if checked else "—"}
                     for key in quality_marks.FLAG_ORDER],
                    width="stretch", hide_index=True,
                )
        else:
            st.caption("暂无质量标记数据。")

        picked = st.multiselect(
            "筛出带这些标记的记录",
            options=list(quality_marks.FLAG_ORDER),
            format_func=lambda key: quality_marks.FLAG_LABELS.get(key, key),
            key="quality_flag_filter",
        )
        region = st.text_input("限定地区（省/市/县名子串，可留空）", key="quality_region_filter")
        if picked or region:
            matched = quality_marks.filter_records(records or [], flags=picked, region=region or None)
            st.caption(f"命中 {len(matched):,} 条（在当前已加载的 {len(records or []):,} 条内筛选）。")
            if matched:
                st.dataframe(
                    [{"ID": item.get("id"), "标题": item.get("title"), "来源": item.get("source_id"),
                      "地区": item.get("region_province") or "—"} for item in matched[:200]],
                    width="stretch", hide_index=True,
                )
            else:
                st.markdown("<div class='empty'>当前条件下没有命中记录。</div>", unsafe_allow_html=True)

    # ════════ Tab 2: 附件提取 ════════
    with tab_attach:
        if not attachments["total"]:
            st.markdown("<div class='empty'>库里还没有附件提取记录。采集到带附件的公告后，这里会按提取状态列出。</div>", unsafe_allow_html=True)
        else:
            status_counts = attachments["by_status"]
            st.markdown(
                f"<div class='metric-row'>"
                f"<div class='metric'><div class='metric-value'>{attachments['total']:,}</div><div class='metric-note'>附件总数</div></div>"
                f"<div class='metric'><div class='metric-value'>{status_counts.get('success', 0):,}</div><div class='metric-note'>提取成功</div></div>"
                f"<div class='metric'><div class='metric-value'>{status_counts.get('failed', 0):,}</div><div class='metric-note'>提取失败</div></div>"
                f"<div class='metric'><div class='metric-value'>{len(attachments['by_type'])}</div><div class='metric-note'>文件类型</div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            status_options = ["全部", "成功", "失败", "跳过"]
            status_map = {"成功": "success", "失败": "failed", "跳过": "skipped"}
            pick_status = st.selectbox("按提取状态筛选", status_options, key="attachment_status_filter")
            rows = attachments["items"]
            if pick_status != "全部":
                rows = [item for item in rows if item["extract_status"] == status_map[pick_status]]
            st.caption(f"显示最近 {len(rows)} 条（共 {attachments['total']:,} 条）。")
            if rows:
                st.dataframe(
                    [{"附件": item["id"], "公告": item["announcement_id"], "类型": item["file_type"],
                      "状态": item["extract_status"], "正文字数": item["text_length"]} for item in rows],
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("该状态下暂无附件。")
            st.caption(f"类型分布：{attachments['by_type']}")

    # ════════ Tab 3: 反馈 ════════
    with tab_feedback:
        if not feedback["total"]:
            st.markdown(
                "<div class='empty'>还没有任何反馈。下面记一条：哪条公告真的有用、哪条是噪音，"
                "后续调规则时会看这里。</div>",
                unsafe_allow_html=True,
            )
        else:
            useful = sum(1 for item in feedback["items"] if item["useful"])
            st.markdown(
                f"<div class='metric-row'>"
                f"<div class='metric'><div class='metric-value'>{feedback['total']:,}</div><div class='metric-note'>反馈总数</div></div>"
                f"<div class='metric'><div class='metric-value'>{useful:,}</div><div class='metric-note'>标记为有用</div></div>"
                f"<div class='metric'><div class='metric-value'>{len(feedback['items']) - useful:,}</div><div class='metric-note'>标记为噪音</div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.dataframe(
                [{"公告": item["announcement_id"], "有用": "是" if item["useful"] else "否",
                  "备注": item["comment"] or "—", "时间": format_time(item["created_at"])} for item in feedback["items"]],
                width="stretch", hide_index=True,
            )
        announcement_options = [str(item["id"]) for item in (records or [])[:200]]
        if announcement_options:
            titles = {str(item["id"]): str(item.get("title") or "未命名公告") for item in (records or [])[:200]}
            with st.form("feedback_form"):
                pick = st.selectbox("针对哪条公告", announcement_options,
                                    format_func=lambda value: f"{value} · {titles.get(value, '')}"[:80],
                                    key="feedback_announcement")
                verdict = st.radio("判断", ["有用", "噪音"], horizontal=True, key="feedback_useful")
                comment = st.text_area("备注（可留空）", key="feedback_comment", height=80)
                submitted = st.form_submit_button("记录反馈")
            if submitted:
                from storage.models import Feedback

                try:
                    with Database("data/beacon.db") as database:
                        with database.session() as session:
                            existing = (
                                session.query(Feedback)
                                .filter_by(user_id="workbench", announcement_id=int(pick))
                                .one_or_none()
                            )
                            if existing:
                                existing.useful = verdict == "有用"
                                existing.comment = comment
                            else:
                                session.add(Feedback(user_id="workbench", announcement_id=int(pick),
                                                     useful=verdict == "有用", comment=comment))
                    load_feedback_overview.clear()
                    st.success("反馈已记录。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001 - surface the reason, never die silently
                    st.error(f"反馈写入失败：{exc}")
        else:
            st.caption("暂无可评论的公告。")

    # ════════ Tab 4: 来源与备份 ════════
    with tab_backup:
        rate = float(quality.get("extraction_success_rate") or 0)
        st.markdown(
            f"<div class='metric-row'>"
            f"<div class='metric'><div class='metric-value'>{rate:.1%}</div><div class='metric-note'>抽取成功率</div></div>"
            f"<div class='metric'><div class='metric-value'>{int(quality.get('collected') or 0):,}</div><div class='metric-note'>采集量</div></div>"
            f"<div class='metric'><div class='metric-value'>{int(quality.get('matches') or 0):,}</div><div class='metric-note'>匹配量</div></div>"
            f"<div class='metric'><div class='metric-value'>{len(backups)}</div><div class='metric-note'>备份文件</div></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # 来源质量柱状图（替代纯表格）
        stats = quality.get("source_stats") or {}
        if stats:
            sorted_stats = sorted(stats.items(), key=lambda kv: kv[1].get("collected", 0), reverse=True)[:15]
            render_echart({
                "title": {"text": "来源采集量 TOP15", "left": "center", "top": 0,
                          "textStyle": {"fontSize": 12}},
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "legend": {"data": ["采集", "失败"], "top": 22, "textStyle": {"fontSize": 10}},
                "grid": {"left": 60, "right": 20, "top": 50, "bottom": 60},
                "xAxis": {"type": "category", "data": [k[:12] for k, _ in sorted_stats],
                          "axisLabel": {"fontSize": 9, "rotate": 35}},
                "yAxis": {"type": "value"},
                "series": [
                    {"name": "采集", "type": "bar", "stack": "total",
                     "data": [v.get("collected", 0) for _, v in sorted_stats]},
                    {"name": "失败", "type": "bar", "stack": "total",
                     "data": [v.get("failures", 0) for _, v in sorted_stats]},
                ],
            }, height=280, key="quality_source_bar")

        with st.expander("来源质量明细表", expanded=False):
            rows = [{"来源": key, "运行": value.get("runs", 0), "采集": value.get("collected", 0), "失败": value.get("failures", 0)} for key, value in stats.items()]
            if rows:
                st.dataframe(rows, width="stretch", hide_index=True)
            else:
                st.caption("等待第一次调度任务生成质量报告。")

        st.markdown("#### SQLite 备份")
        if st.button("立即创建备份", width="stretch", key="create_backup"):
            result = backup_database("data/beacon.db", "data/backups", 30)
            if result.get("created"):
                st.success(f"备份已创建：{result.get('path')}（清理过期 {result.get('removed', 0)} 个，保留 {result.get('retention_days', 30)} 天）")
                load_backups.clear()
                st.rerun()
            else:
                st.warning(f"暂未找到可备份的数据库（{result.get('reason') or '未知原因'}）。")
        if backups:
            st.dataframe([{"文件": item["name"], "大小": f"{item['size'] / 1024:.1f} KB", "更新时间": format_time(item["modified"])} for item in backups[:10]], width="stretch", hide_index=True)
        else:
            st.caption("暂无备份文件。")


def render_graph() -> None:
    summary = load_graph_summary()
    st.markdown("<div class='section-label'>知识图谱</div>", unsafe_allow_html=True)
    st.markdown("### 把公告变成可追溯的关系")

    # ── 合并指标为一行（7 个核心指标）──
    _gn = int(summary.get("nodes") or 0)
    _ge = int(summary.get("edges") or 0)
    _ev = int(summary.get("evidence") or 0)
    _nr = int(summary.get("needs_review") or 0)
    _density = round(2 * _ge / max(1, _gn * (_gn - 1)) * 100, 2) if _gn > 1 else 0
    _avg_deg = round(2 * _ge / max(1, _gn), 1) if _gn else 0
    try:
        import sqlite3 as _sq
        _con = _sq.connect("file:data/beacon.db?mode=ro", uri=True)
        _total_ann = int(_con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0])
        _con.close()
    except Exception:
        _total_ann = 0
    _ann_nodes = int((summary.get("node_types") or {}).get("announcement", 0))
    _coverage = round(_ann_nodes / max(1, _total_ann) * 100, 1) if _total_ann else 0

    st.markdown(
        f"<div class='metric-row'>"
        f"<div class='metric'><div class='metric-value'>{_gn:,}</div><div class='metric-note'>节点</div></div>"
        f"<div class='metric'><div class='metric-value'>{_ge:,}</div><div class='metric-note'>关系</div></div>"
        f"<div class='metric'><div class='metric-value'>{_coverage}%</div><div class='metric-note'>覆盖率</div></div>"
        f"<div class='metric'><div class='metric-value'>{_density}%</div><div class='metric-note'>图密度</div></div>"
        f"<div class='metric'><div class='metric-value'>{_avg_deg}</div><div class='metric-note'>平均度数</div></div>"
        f"<div class='metric'><div class='metric-value'>{_ev:,}</div><div class='metric-note'>证据</div></div>"
        f"<div class='metric'><div class='metric-value'>{_nr:,}</div><div class='metric-note'>待复核</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if _total_ann and _coverage < 50:
        st.warning(f"图谱覆盖率仅 {_coverage}%：{_total_ann} 条公告中只有 {_ann_nodes} 条进入图谱。")

    # ── 力导向关系网络图（ECharts）──
    sample = load_graph_sample()
    nodes = sample.get("nodes") or []
    edges = sample.get("edges") or []
    categories = sample.get("categories") or []
    if nodes:
        # 控制栏：布局选择 + 节点数
        ctrl_left, ctrl_right = st.columns([3, 2], gap="small")
        with ctrl_left:
            _layout = st.radio(
                "布局方式",
                options=["force", "circular", "none"],
                format_func=lambda x: {"force": "力导向", "circular": "环形", "none": "固定"}[x],
                horizontal=True,
                key="graph_layout",
            )
        with ctrl_right:
            _max_display = st.slider("显示节点数", 80, 300, 250, step=20, key="graph_max_nodes")

        # 按滑块截断节点和边
        _display_nodes = nodes[:_max_display]
        _display_ids = {n["id"] for n in _display_nodes}
        _display_edges = [e for e in edges if e["source"] in _display_ids and e["target"] in _display_ids][:400]

        st.markdown("#### 关系网络")
        # 节点类型配色
        palette = ["#5B8FF9", "#5AD8A6", "#5D7092", "#F6BD16", "#E8684A", "#6DC8EC"]
        cat_with_color = [
            {"name": c["name"], "itemStyle": {"color": palette[i % len(palette)]}}
            for i, c in enumerate(categories)
        ]

        _force_config = {
            "force": {"repulsion": 120, "edgeLength": 80, "gravity": 0.1, "layoutAnimation": True},
            "circular": {"rotate": True, "radius": "60%"},
            "none": {},
        }

        option = {
            "tooltip": {
                "trigger": "item",
                "formatter": (
                    "function(p){if(p.dataType==='edge'){return p.data.value||'关联';}"
                    "return p.data.name+'<br/>度数: '+(p.data.value||0);}"
                ),
            },
            "legend": [
                {
                    "data": [c["name"] for c in categories],
                    "top": 6,
                    "textStyle": {"fontSize": 11},
                    "itemWidth": 12,
                    "itemHeight": 8,
                }
            ],
            "series": [
                {
                    "type": "graph",
                    "layout": _layout,
                    "data": _display_nodes,
                    "links": _display_edges,
                    "categories": cat_with_color,
                    "roam": True,
                    "draggable": True,
                    "label": {"show": True, "position": "right", "fontSize": 10},
                    "lineStyle": {"color": "#aaa", "width": 1, "curveness": 0.1, "opacity": 0.6},
                    "emphasis": {
                        "focus": "adjacency",
                        "lineStyle": {"width": 3, "opacity": 1},
                        "label": {"show": True, "fontSize": 12},
                    },
                    **_force_config.get(_layout, {}),
                }
            ],
        }
        render_echart(option, height=420, key="graph_force")
    else:
        st.caption("运行采集后自动生成关系网络图。")

    # ── 节点/边分布（折叠，默认收起）──
    with st.expander("节点与关系分布明细", expanded=False):
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("##### 节点类型分布")
            node_types = summary.get("node_types") or {}
            if node_types:
                render_echart({
                    "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                    "series": [{
                        "type": "pie",
                        "radius": ["35%", "65%"],
                        "data": [{"name": k, "value": v} for k, v in node_types.items()],
                        "label": {"fontSize": 10, "formatter": "{b}\n{c}"},
                    }],
                }, height=220, key="graph_node_pie")
            else:
                st.caption("暂无数据。")
        with right:
            st.markdown("##### 关系类型分布")
            edge_types = summary.get("edge_types") or {}
            if edge_types:
                render_echart({
                    "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                    "series": [{
                        "type": "pie",
                        "radius": ["35%", "65%"],
                        "data": [{"name": k, "value": v} for k, v in edge_types.items()],
                        "label": {"fontSize": 10, "formatter": "{b}\n{c}"},
                    }],
                }, height=220, key="graph_edge_pie")
            else:
                st.caption("暂无数据。")


def render_heat_topics() -> None:
    topics = load_heat_topics()
    st.markdown("<div class='section-label'>热频话题</div>", unsafe_allow_html=True)
    st.markdown("### 公共政策与民生事件热度")
    if not topics:
        st.markdown("<div class='empty' style='padding:2rem;text-align:center'>暂无热频话题</div>", unsafe_allow_html=True)
        st.markdown("""
        <div style='max-width:480px;margin:1rem auto;padding:1.5rem;border:1px solid var(--line);border-radius:.65rem;background:var(--panel-glass)'>
        <h4 style='margin:0 0 .8rem'>热频话题如何产生？</h4>
        <ol style='margin:0;padding-left:1.2rem;line-height:1.8;color:var(--ink);opacity:.8'>
        <li>采集引擎从<strong>政府源</strong>抓取公开公告</li>
        <li>规则引擎做 <strong>simhash 主题聚类与热度计算</strong>（官方一手源权重 ×3）</li>
        <li>同一事件被<strong>多个独立域名</strong>发布时，标记为交叉印证</li>
        </ol>
        <div style='margin-top:1rem;padding:.6rem;border-radius:.4rem;background:rgba(var(--accent_rgb),0.06);font-size:.85rem'>
        <strong>为什么可能是空的：</strong>热度要求同一事件至少 2 个独立来源。
        政务公告各省市自己发，措辞不同就聚不到一起——真实产出通常是每天 1~2 个。
        堆数据请回填历史：<code>python scripts/backfill_postprocess.py</code>。
        </div>
        </div>
        """, unsafe_allow_html=True)
        col_a, col_b = st.columns(2)
        if col_a.button("去采集运维", key="heat_goto_collect", width="stretch", icon=":material/sync:"):
            st.session_state["active_view"] = "采集运营"
            st.rerun()
        if col_b.button("去来源健康", key="heat_goto_sources", width="stretch", icon=":material/monitor_heart:"):
            st.session_state["active_view"] = "来源健康"
            st.rerun()
        return
    top_score = max(float(item.get("heat_score") or 0) for item in topics)
    official = sum(1 for item in topics if "官方" in str(item.get("heat_reason") or ""))
    st.markdown(f"<div class='metric-row'><div class='metric'><div class='metric-label'>话题数</div><div class='metric-value'>{len(topics)}</div><div class='metric-note'>近期待处理</div></div><div class='metric'><div class='metric-label'>最高热度</div><div class='metric-value'>{top_score:.1f}</div><div class='metric-note'>规则计算</div></div><div class='metric'><div class='metric-label'>官方源话题</div><div class='metric-value'>{official}</div><div class='metric-note'>交叉信源</div></div><div class='metric'><div class='metric-label'>统计口径</div><div class='metric-value'>规则</div><div class='metric-note'>非点击量</div></div></div>", unsafe_allow_html=True)

    # 热度排行条形图 + 分类气泡图
    sorted_topics = sorted(topics, key=lambda t: float(t.get("heat_score") or 0), reverse=True)
    top15 = sorted_topics[:15]
    bar_data = [
        {"value": float(t.get("heat_score") or 0), "name": str(t.get("title") or "")[:18]}
        for t in top15
    ]
    # 按分类聚合气泡数据
    cat_map: dict[str, list] = {}
    for t in topics:
        cat = str(t.get("category_l1") or "其他")
        cat_map.setdefault(cat, []).append(t)
    scatter_data = []
    cat_names = sorted(cat_map.keys())
    for ci, cat in enumerate(cat_names):
        for t in cat_map[cat]:
            scatter_data.append({
                "value": [ci, float(t.get("heat_score") or 0), int(t.get("source_count") or 1)],
                "name": str(t.get("title") or "")[:20],
            })
    col_chart_a, col_chart_b = st.columns(2, gap="large")
    with col_chart_a:
        st.markdown("#### 热度排行 TOP15")
        render_echart({
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": "55%", "right": 20, "top": 10, "bottom": 20},
            "xAxis": {"type": "value", "max": top_score * 1.1, "splitLine": {"show": False}},
            "yAxis": {
                "type": "category",
                "data": [d["name"] for d in bar_data],
                "inverse": True,
                "axisLabel": {"fontSize": 10, "width": 80, "overflow": "truncate"},
            },
            "series": [{
                "type": "bar",
                "data": [d["value"] for d in bar_data],
                "itemStyle": {
                    "color": "#E8684A",
                    "borderRadius": [0, 3, 3, 0],
                },
                "label": {"show": True, "position": "right", "fontSize": 10, "formatter": "{c}"},
            }],
        }, height=420, key="heat_bar")
    with col_chart_b:
        st.markdown("#### 分类热度分布")
        render_echart({
            "tooltip": {
                "trigger": "item",
                "formatter": "function(p){return p.data.name+'<br/>热度: '+p.data.value[1]+'<br/>信源: '+p.data.value[2];}",
            },
            "grid": {"left": 50, "right": 20, "top": 20, "bottom": 60},
            "xAxis": {
                "type": "category",
                "data": cat_names,
                "axisLabel": {"fontSize": 10, "rotate": 30},
            },
            "yAxis": {"type": "value", "name": "热度", "splitLine": {"lineStyle": {"type": "dashed"}}},
            "series": [{
                "type": "scatter",
                "data": scatter_data,
                "symbolSize": "function(d){return Math.sqrt(d[2])*6+8;}",
                "itemStyle": {"color": "#5B8FF9", "opacity": 0.7},
                "emphasis": {"itemStyle": {"color": "#E8684A", "opacity": 1}},
            }],
        }, height=420, key="heat_scatter")

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
    """左侧栏：品牌 + 可折叠分组菜单（滚动）+ 运行状态 + 个人中心。

    菜单区用 .side-scroll 独立滚动，品牌/状态/用户卡固定；分组用 st.expander 折叠，
    避免 13 个模块把侧栏撑出视口。st.expander 原生支持折叠/展开，自带 chevron 动画
    和键盘可访问性，不需要自己写 CSS/JS 状态同步。
    """
    logo = load_logo_svg()

    with st.container(key="side"):
        st.markdown(
            f"<div class='side-brand entrance-fade'>"
            f"<div class='brand-mark'>{logo}</div>"
            f"<div><div class='side-title'>灯塔 <span class='side-title-accent'>Beacon OSINT</span></div></div>"
            f"</div>"
            f"<div class='side-online'><span class='pulse-dot'></span><span>系统在线 · 本地运行</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div class='side-group'>功能模块</div>", unsafe_allow_html=True)

        # 菜单区独立滚动。用 st.container(key=) 而不是 st.markdown("<div class='side-scroll'>")：
        # markdown 里的 div 会被 Streamlit 立即闭合成一个空元素，套不住任何控件，
        # 于是滚动落在整条侧栏上 —— 品牌/状态/用户卡跟着一起滚走，列表在光标底下
        # 平移，用户点空处，这就是"点选没反应、要反复点"的根因。
        with st.container(key="side_menu"):
            index = 0
            for group_name, members in NAV_GROUPS:
                # 记忆分组展开状态：当前页所在分组自动展开 + 用户手动展开的分组记住
                group_key = f"grp_{group_name}"
                if group_key not in st.session_state:
                    st.session_state[group_key] = active_view in members
                # 当前页所在分组强制展开
                is_current_group = active_view in members
                with st.expander(
                    html.escape(group_name),
                    expanded=is_current_group or st.session_state[group_key],
                ):
                    for name in members:
                        is_active = name == active_view
                        if st.button(
                            name,
                            key=f"nav_btn_{index}",
                            icon=NAV_ICONS.get(name, ":material/circle:"),
                            width="stretch",
                            type="primary" if is_active else "secondary",
                        ):
                            st.session_state["active_view"] = name
                            st.session_state[group_key] = True  # 记住这个分组是展开的
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
            width="stretch",
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
        if col_set.button("账户设置", width="stretch", key="user_settings_btn"):
            st.toast("账户设置：本地单用户模式，暂无可配置项")
        if col_out.button("退出登录", width="stretch", key="user_logout_btn"):
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
        _save_theme_pref(selected_theme)
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
    if st.button("运行采集", width="stretch", key="sidebar_collect", help="运行一次采集任务"):
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
    if st.button("立即备份", width="stretch", key="sidebar_backup", help="立即创建数据库备份"):
        result = backup_database("data/beacon.db", "data/backups", 30)
        if result.get("created"):
            load_backups.clear()
            st.success("备份已创建")
            st.rerun()
        else:
            st.warning("暂无可备份数据库")
    if st.button("刷新数据", width="stretch", key="sidebar_refresh"):
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
    reset_theme = st.query_params.get("reset_theme")
    if nav_view or reset_theme:
        if nav_view and nav_view in NAV_VIEWS:
            st.session_state["active_view"] = nav_view
        if reset_theme:
            st.session_state["theme"] = default_theme()
            _save_theme_pref(default_theme())
            st.session_state["right_open"] = False
        st.query_params.clear()
        st.rerun()

    inject_styles()
    active_view = st.session_state.get("active_view", "总览")
    right_open = st.session_state.get("right_open", False)

    # ── 统一工具带（全宽一条：面包屑 + 页面名 + 状态） ──
    render_topbar()

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
      with st.container(key="main-content"):
        try:
            snapshot = load_snapshot("data/beacon.db")
            quality = load_quality() if active_view in ("总览", "质量与备份") else {}
            backups = load_backups() if active_view == "质量与备份" else []
        except Exception as exc:
            st.error(f"工作台数据加载失败：{exc}")
            st.stop()
        rules = load_rules()
        theme_id = st.session_state.get("theme", default_theme())
        # 所有内页收进同一个固定高度滚动面板：页面不再被内容越拉越长
        with st.container(key="page-scroll"):
            if active_view == "指挥中心":
                render_command_center("data/beacon.db")
            elif active_view == "总览":
                render_home(snapshot["records"], snapshot["sources"], quality, load_graph_summary(), theme_id)
            elif active_view == "中国地图":
                render_china_map(snapshot["records"], theme_id=theme_id, height=780)
            elif active_view == "公告检索":
                render_announcements(snapshot["records"], rules, filters)
            elif active_view == "来源健康":
                render_sources(snapshot["sources"])
            elif active_view == "知识图谱":
                render_graph()
            elif active_view == "热频话题":
                render_heat_topics()
            elif active_view == "成刊":
                render_publications()
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
            elif active_view == "灯塔采集":
                render_lighthouse("data/beacon.db")
            else:
                render_quality(quality, backups, snapshot["records"])


main()
