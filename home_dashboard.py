"""科技感首页仪表盘 — 3D地球为中心 + 环绕可视化模块卡片。

首页设计理念：
- 中央：3D旋转地球 + 飞线 + 数据节点（Three.js）
- 环绕：6个可点击模块卡片（分类雷达、省份热力、年度趋势、来源健康、知识图谱、实时脉搏）
- 顶部：KPI数据条（总公告、监测省份、政策类、民生类）
- 底部：数据管道状态条

点击模块卡片 → 通过 Streamlit session_state 切换到对应详情页。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from branding import BRAND_NAME, load_logo_svg
from theme_config import get_theme_js_colors

ROOT = Path(__file__).resolve().parent
VENDOR_DIR = ROOT / "assets" / "vendor"


def _compute_home_stats(records: list[dict[str, Any]], sources: list[dict[str, Any]], quality: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    """计算首页所需的统计数据。"""
    province_counts = Counter()
    category_counts = Counter()
    year_counts = Counter()
    policy_count = 0
    livelihood_count = 0
    recent_count = 0
    now = datetime.now()

    for record in records:
        prov = record.get("region_province")
        if prov:
            province_counts[prov] += 1
        cat = record.get("category_l1") or "其他"
        category_counts[cat] += 1
        date_str = str(record.get("publish_date") or "")
        if date_str[:4].isdigit():
            year_counts[date_str[:4]] += 1
        if cat in ("行业政策", "行业补贴", "人才政策", "养老与社区"):
            policy_count += 1
        if cat in ("活动通知", "招生信息", "公招信息", "公务员考试", "军队招录"):
            livelihood_count += 1
        last_seen = record.get("last_seen_at")
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if (now - dt.replace(tzinfo=None)).total_seconds() <= 86400:
                    recent_count += 1
            except (ValueError, TypeError):
                pass

    ok_sources = sum(1 for s in sources if s.get("status") == "ok")
    failed_sources = sum(1 for s in sources if s.get("status") == "error")
    pending_sources = sum(1 for s in sources if s.get("status") not in ("ok", "error"))

    cat_data = [{"name": cat, "value": count} for cat, count in category_counts.most_common(12)]
    prov_data = [{"name": prov, "value": count} for prov, count in province_counts.most_common(10)]
    year_data = [{"year": yr, "count": count} for yr, count in sorted(year_counts.items())]

    return {
        "total": len(records),
        "provinces": len(province_counts),
        "policy": policy_count,
        "livelihood": livelihood_count,
        "recent": recent_count,
        "ok_sources": ok_sources,
        "failed_sources": failed_sources,
        "pending_sources": pending_sources,
        "extraction_rate": float(quality.get("extraction_success_rate") or 0),
        "graph_nodes": int(graph.get("nodes") or 0),
        "graph_edges": int(graph.get("edges") or 0),
        "cat_data": cat_data,
        "prov_data": prov_data,
        "year_data": year_data,
    }


def _home_html(stats: dict[str, Any], theme_id: str) -> str:
    """生成首页完整HTML。"""
    tc = get_theme_js_colors(theme_id)
    three_source = (VENDOR_DIR / "three.min.js").read_text(encoding="utf-8")
    echarts_source = (VENDOR_DIR / "echarts.min.js").read_text(encoding="utf-8")

    stats_json = json.dumps(stats, ensure_ascii=False)
    theme_colors_json = json.dumps(tc, ensure_ascii=False)
    logo_svg = load_logo_svg()

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
:root {{
  --tc-ground: {tc['ground']};
  --tc-panel: {tc['panel']};
  --tc-line: {tc['line']};
  --tc-ink: {tc['ink']};
  --tc-muted: {tc['muted']};
  --tc-accent: {tc['accent']};
  --tc-amber: {tc['amber']};
  --tc-red: {tc['red']};
}}
* {{box-sizing:border-box;margin:0;padding:0}}
html,body {{
  background:var(--tc-ground);color:var(--tc-ink);
  font-family:Inter,Segoe UI,Microsoft YaHei,sans-serif;
  overflow-x:hidden;overflow-y:auto;
  scrollbar-width:none;-ms-overflow-style:none;
}}
html::-webkit-scrollbar,body::-webkit-scrollbar,.hud::-webkit-scrollbar {{width:0 !important;height:0 !important;display:none !important}}

body::before {{
  content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(ellipse 80% 50% at 50% 0%, rgba(255,255,255,.025), transparent),
    radial-gradient(ellipse 60% 40% at 20% 100%, rgba(255,255,255,.015), transparent),
    linear-gradient(rgba(255,255,255,.012) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.012) 1px,transparent 1px);
  background-size:100% 100%,100% 100%,28px 28px,28px 28px;
}}

.hud {{max-width:1640px;margin:0 auto;padding:8px 12px}}

/* === 品牌 Logo 栏 === */
.brand-bar {{
  display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:8px 14px 8px 12px;
  background:var(--tc-panel);
  backdrop-filter:blur(20px) saturate(1.4);-webkit-backdrop-filter:blur(20px) saturate(1.4);
  border:1px solid var(--tc-line);border-radius:6px;
  margin-bottom:8px;position:relative;overflow:hidden;
  box-shadow:0 4px 20px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.03);
}}
.brand-bar::after {{
  content:'';position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,var(--tc-accent),transparent);
  box-shadow:0 0 12px var(--tc-accent);
}}
.brand-left {{display:flex;align-items:center;gap:10px;padding-left:4px;min-width:0}}
.brand-mark {{
  width:30px;height:30px;flex:0 0 auto;border:1px solid var(--tc-accent);border-radius:6px;
  display:grid;place-items:center;color:var(--tc-accent);
  box-shadow:0 0 10px rgba(255,255,255,.06),inset 0 1px 0 rgba(255,255,255,.04);
  background:rgba(255,255,255,.02);
}}
.brand-mark svg {{width:62%;height:62%;display:block}}
.brand-name {{font-size:14px;font-weight:800;color:var(--tc-ink);letter-spacing:.02em;line-height:1.2;white-space:nowrap}}
.brand-sub {{font-size:9px;color:var(--tc-accent);letter-spacing:.18em;text-transform:uppercase;margin-top:1px;font-weight:600}}
.brand-right {{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--tc-muted);white-space:nowrap}}
.brand-right .live-dot {{
  width:6px;height:6px;border-radius:50%;background:var(--tc-accent);
  box-shadow:0 0 0 3px rgba(255,255,255,.04),0 0 8px var(--tc-accent);
  animation:pulse 2s infinite;
}}

/* === KPI 数据条 === */
.kpi-strip {{
  display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:8px;
}}
.kpi-card {{
  background:var(--tc-panel);
  backdrop-filter:blur(16px) saturate(1.35);-webkit-backdrop-filter:blur(16px) saturate(1.35);
  border:1px solid var(--tc-line);border-radius:6px;
  padding:10px 12px;position:relative;overflow:hidden;
  box-shadow:0 3px 14px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.02);
  transition:all .3s cubic-bezier(0.34,1.56,0.64,1);
}}
.kpi-card:hover {{
  border-color:var(--tc-accent);
  transform:translateY(-2px);
  box-shadow:0 6px 20px rgba(0,0,0,.3),0 0 16px rgba(255,255,255,.04);
}}
.kpi-card::before {{
  content:'';position:absolute;top:0;left:0;width:100%;height:1px;
  background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);opacity:.5;
}}
.kpi-card::after {{
  content:'';position:absolute;bottom:0;left:10%;right:10%;height:1px;
  background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);opacity:.15;
}}
.kpi-card .label {{font-size:9px;color:var(--tc-muted);letter-spacing:.08em;text-transform:uppercase;font-weight:600}}
.kpi-card .value {{font-size:24px;font-weight:800;color:var(--tc-accent);margin-top:3px;letter-spacing:-.02em;line-height:1.1;text-shadow:0 0 12px rgba(255,255,255,.08)}}
.kpi-card .note {{font-size:9px;color:var(--tc-muted);margin-top:2px}}
.kpi-card .mini-bar {{
  position:absolute;bottom:0;left:0;height:2px;
  background:linear-gradient(90deg,var(--tc-accent),transparent);
  border-radius:0 2px 0 0;opacity:.6;
}}

/* === 主布局：地球 + 模块卡片 === */
.main-grid {{
  display:grid;grid-template-columns:1fr 1fr;gap:8px;align-items:stretch;
}}

/* 地球面板 */
.globe-panel {{
  background:var(--tc-panel);
  backdrop-filter:blur(20px) saturate(1.4);-webkit-backdrop-filter:blur(20px) saturate(1.4);
  border:1px solid var(--tc-line);border-radius:6px;
  position:relative;overflow:hidden;display:flex;flex-direction:column;min-height:320px;
  box-shadow:0 6px 28px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.03);
}}
.globe-panel::before,.globe-panel::after {{
  content:'';position:absolute;width:12px;height:12px;border:2px solid var(--tc-accent);
  z-index:5;pointer-events:none;
}}
.globe-panel::before {{top:0;left:0;border-right:0;border-bottom:0}}
.globe-panel::after {{bottom:0;right:0;border-left:0;border-top:0}}
.globe-corner-tr,.globe-corner-bl {{
  position:absolute;width:12px;height:12px;border:2px solid var(--tc-accent);z-index:5;pointer-events:none;
}}
.globe-corner-tr {{top:0;right:0;border-left:0;border-bottom:0}}
.globe-corner-bl {{bottom:0;left:0;border-right:0;border-top:0}}
.globe-scan {{
  position:absolute;left:0;right:0;height:2px;top:0;
  background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);
  box-shadow:0 0 12px var(--tc-accent);opacity:.4;z-index:4;
  animation:scan 8s linear infinite;pointer-events:none;
}}
.globe-grid {{
  position:absolute;inset:0;
  background-image:linear-gradient(rgba(255,255,255,.02) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.02) 1px,transparent 1px);
  background-size:40px 40px;z-index:3;pointer-events:none;opacity:.5;
}}
#globe-canvas {{flex:1 1 auto;width:100%;min-height:0;display:block;position:relative;z-index:1}}
.globe-label {{
  position:absolute;top:8px;left:8px;z-index:6;pointer-events:none;
}}
.globe-label .kicker {{font-size:8px;color:var(--tc-accent);letter-spacing:.2em;text-transform:uppercase;font-weight:600}}
.globe-label .title {{font-size:12px;color:var(--tc-ink);margin-top:1px;font-weight:600}}

/* 模块卡片网格 */
.module-grid {{
  display:grid;grid-template-columns:repeat(2,1fr);gap:8px;
}}
.module-card {{
  background:var(--tc-panel);
  backdrop-filter:blur(16px) saturate(1.35);-webkit-backdrop-filter:blur(16px) saturate(1.35);
  border:1px solid var(--tc-line);border-radius:6px;
  padding:10px 12px;position:relative;overflow:hidden;cursor:pointer;
  transition:all .3s cubic-bezier(0.34,1.56,0.64,1);
  min-height:152px;display:flex;flex-direction:column;
  box-shadow:0 3px 14px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.02);
  transform-style:preserve-3d;
}}
.module-card:hover {{
  border-color:var(--tc-accent);
  box-shadow:0 8px 24px rgba(0,0,0,.3),0 0 20px rgba(255,255,255,.05),inset 0 1px 0 rgba(255,255,255,.04);
  transform:translateY(-3px) scale(1.01);
}}
.module-card::before {{
  content:'';position:absolute;top:0;left:0;width:100%;height:1px;
  background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);opacity:.3;
  transition:opacity .3s;
}}
.module-card:hover::before {{opacity:.8}}
.module-card::after {{
  content:'';position:absolute;bottom:0;left:0;width:3px;height:0;
  background:var(--tc-accent);border-radius:0 0 0 3px;
  transition:height .3s cubic-bezier(0.34,1.56,0.64,1);
  box-shadow:0 0 8px var(--tc-accent);
}}
.module-card:hover::after {{height:100%}}
.module-card .mod-header {{
  display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;
}}
.module-card .mod-icon {{
  width:26px;height:26px;border:1px solid var(--tc-accent);border-radius:5px;
  display:grid;place-items:center;color:var(--tc-accent);font-size:13px;
  box-shadow:0 0 6px rgba(255,255,255,.04);background:rgba(255,255,255,.02);
  transition:all .3s;
}}
.module-card:hover .mod-icon {{
  box-shadow:0 0 12px var(--tc-accent);
  transform:scale(1.05);
}}
.module-card .mod-title {{font-size:13px;color:var(--tc-ink);font-weight:700}}
.module-card .mod-subtitle {{font-size:8px;color:var(--tc-muted);letter-spacing:.12em;text-transform:uppercase}}
.module-card .mod-chart {{width:100%;flex:1;min-height:80px}}
.module-card .mod-footer {{
  display:flex;justify-content:space-between;align-items:center;
  margin-top:2px;font-size:9px;color:var(--tc-muted);
}}
.module-card .mod-enter {{
  color:var(--tc-accent);font-size:9px;letter-spacing:.04em;
  opacity:0;transition:opacity .3s;
}}
.module-card:hover .mod-enter {{opacity:1}}

/* === 底部状态条 === */
.status-bar {{
  display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;
  background:var(--tc-panel);
  backdrop-filter:blur(16px) saturate(1.35);-webkit-backdrop-filter:blur(16px) saturate(1.35);
  border:1px solid var(--tc-line);border-radius:6px;
  margin-top:8px;font-size:10px;
  box-shadow:0 3px 14px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.02);
  position:relative;overflow:hidden;
}}
.status-bar::before {{
  content:'';position:absolute;top:0;left:0;width:100%;height:1px;
  background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);opacity:.3;
}}
.status-bar .left {{display:flex;align-items:center;gap:12px}}
.status-bar .item {{display:flex;align-items:center;gap:4px;color:var(--tc-muted)}}
.status-bar .item b {{color:var(--tc-ink)}}
.status-bar .item .dot {{
  width:5px;height:5px;border-radius:50%;background:var(--tc-accent);
  box-shadow:0 0 0 3px rgba(255,255,255,.03),0 0 6px var(--tc-accent);
}}
.status-bar .right {{color:var(--tc-muted)}}
.status-bar .flow-bar {{
  position:absolute;bottom:0;left:0;right:0;height:2px;overflow:hidden;
  background:rgba(255,255,255,.02);
}}
.status-bar .flow-bar::after {{
  content:'';position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,var(--tc-accent),transparent);
  animation:data-flow 3s linear infinite;
}}

/* === 动画 === */
@keyframes pulse {{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
@keyframes scan {{0%{{top:0}}100%{{top:100%}}}}
@keyframes data-flow {{0%{{transform:translateX(-100%)}}100%{{transform:translateX(100%)}}}}
@keyframes entrance-fade {{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:translateY(0)}}}}
.entrance-fade {{animation:entrance-fade .5s ease-out both}}
.entrance-fade:nth-child(1) {{animation-delay:.03s}}
.entrance-fade:nth-child(2) {{animation-delay:.06s}}
.entrance-fade:nth-child(3) {{animation-delay:.09s}}
.entrance-fade:nth-child(4) {{animation-delay:.12s}}

/* === 响应式 === */
@media(max-width:900px) {{
  .main-grid {{grid-template-columns:1fr}}
  .kpi-strip {{grid-template-columns:repeat(2,1fr)}}
  .module-grid {{grid-template-columns:1fr}}
  .globe-panel {{min-height:280px}}
  .brand-right {{display:none}}
}}
</style></head>
<body>
<div class="hud">

  <!-- 品牌 Logo 栏 -->
  <header class="brand-bar entrance-fade">
    <div class="brand-left">
      <div class="brand-mark">{logo_svg}</div>
      <div>
        <div class="brand-name">{BRAND_NAME}</div>
        <div class="brand-sub">运营中心</div>
      </div>
    </div>
    <div class="brand-right">
      <span class="live-dot"></span>
      <span>数据管道在线</span>
      <span>·</span>
      <span>{datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
    </div>
  </header>

  <!-- KPI 数据条 -->
  <div class="kpi-strip">
    <div class="kpi-card entrance-fade">
      <div class="label">信息总量</div>
      <div class="value">{stats['total']}</div>
      <div class="note">已入库公开信息</div>
      <div class="mini-bar" style="width:85%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">监测省份</div>
      <div class="value">{stats['provinces']}</div>
      <div class="note">覆盖行政区域</div>
      <div class="mini-bar" style="width:65%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">政策类</div>
      <div class="value">{stats['policy']}</div>
      <div class="note">行业政策/补贴/人才/养老</div>
      <div class="mini-bar" style="width:45%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">民生类</div>
      <div class="value">{stats['livelihood']}</div>
      <div class="note">活动/招生/公招</div>
      <div class="mini-bar" style="width:30%"></div>
    </div>
  </div>

  <!-- 主布局：地球 + 模块卡片 -->
  <div class="main-grid">

    <!-- 3D 地球面板 -->
    <section class="globe-panel">
      <div class="globe-corner-tr"></div>
      <div class="globe-corner-bl"></div>
      <div class="globe-scan"></div>
      <div class="globe-grid"></div>
      <div class="globe-label">
        <div class="kicker">全球视角</div>
        <div class="title">中国公共政策信息节点</div>
      </div>
      <canvas id="globe-canvas"></canvas>
    </section>

    <!-- 模块卡片网格 -->
    <div class="module-grid">

      <!-- 模块1：分类雷达 -->
      <div class="module-card" data-nav="公告检索">
        <div class="mod-header">
          <div class="mod-icon">类</div>
          <div>
            <div class="mod-title">分类雷达</div>
            <div class="mod-subtitle">分类导航</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-radar"></div>
        <div class="mod-footer">
          <span>12 个分类维度</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

      <!-- 模块2：省份热力 -->
      <div class="module-card" data-nav="中国地图">
        <div class="mod-header">
          <div class="mod-icon">图</div>
          <div>
            <div class="mod-title">省份热力</div>
            <div class="mod-subtitle">地域分布</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-province"></div>
        <div class="mod-footer">
          <span>{stats['provinces']} 个省份有数据</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

      <!-- 模块3：年度趋势 -->
      <div class="module-card" data-nav="公告检索">
        <div class="mod-header">
          <div class="mod-icon">势</div>
          <div>
            <div class="mod-title">年度趋势</div>
            <div class="mod-subtitle">时间走势</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-year"></div>
        <div class="mod-footer">
          <span>5 年历史数据</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

      <!-- 模块4：来源健康 -->
      <div class="module-card" data-nav="来源健康">
        <div class="mod-header">
          <div class="mod-icon">源</div>
          <div>
            <div class="mod-title">来源健康</div>
            <div class="mod-subtitle">来源状态</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-source"></div>
        <div class="mod-footer">
          <span>{stats['ok_sources']} 正常 · {stats['failed_sources']} 失败</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

      <!-- 模块5：知识图谱 -->
      <div class="module-card" data-nav="知识图谱">
        <div class="mod-header">
          <div class="mod-icon">谱</div>
          <div>
            <div class="mod-title">知识图谱</div>
            <div class="mod-subtitle">关联网络</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-graph"></div>
        <div class="mod-footer">
          <span>{stats['graph_nodes']} 节点 · {stats['graph_edges']} 关系</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

      <!-- 模块6：实时脉搏 -->
      <div class="module-card" data-nav="质量与备份">
        <div class="mod-header">
          <div class="mod-icon">脉</div>
          <div>
            <div class="mod-title">实时脉搏</div>
            <div class="mod-subtitle">质量动态</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-pulse"></div>
        <div class="mod-footer">
          <span>抽取成功率 {stats['extraction_rate']:.0%}</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

    </div>
  </div>

  <!-- 底部状态条 -->
  <div class="status-bar">
    <div class="left">
      <div class="item"><span class="dot"></span><b>链路稳定</b></div>
      <div class="item">{stats['ok_sources']} 来源正常</div>
      <div class="item">{stats['failed_sources']} 失败</div>
      <div class="item">{stats['pending_sources']} 待审核</div>
    </div>
    <div class="right">
      {BRAND_NAME} · 规则驱动 · 合规来源 · {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </div>
    <div class="flow-bar"></div>
  </div>

</div>

<script>{three_source}</script>
<script>{echarts_source}</script>
<script>
const STATS = {stats_json};
const TC = {theme_colors_json};

// === 3D 地球 ===
(function initGlobe() {{
  if (!window.THREE) return;
  const canvas = document.getElementById('globe-canvas');
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 1, 1000);
  camera.position.z = 310;
  const renderer = new THREE.WebGLRenderer({{canvas, antialias:true, alpha:true}});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(TC.ground, 0);

  const group = new THREE.Group();
  scene.add(group);

  // 程序化地球纹理
  const earthCanvas = document.createElement('canvas');
  earthCanvas.width = 1024; earthCanvas.height = 512;
  const ectx = earthCanvas.getContext('2d');
  ectx.fillStyle = TC.ocean; ectx.fillRect(0, 0, 1024, 512);
  ectx.fillStyle = TC.land;
  ectx.beginPath(); ectx.ellipse(560, 180, 200, 80, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(540, 300, 70, 90, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(220, 170, 120, 70, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(280, 340, 50, 80, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(800, 340, 60, 35, 0, 0, Math.PI*2); ectx.fill();
  ectx.fillStyle = TC.china_highlight;
  ectx.beginPath(); ectx.ellipse(680, 170, 60, 40, 0, 0, Math.PI*2); ectx.fill();

  const earthTexture = new THREE.CanvasTexture(earthCanvas);
  const accentHex = parseInt(TC.accent.replace('#',''), 16);
  const globe = new THREE.Mesh(
    new THREE.SphereGeometry(100, 64, 32),
    new THREE.MeshPhongMaterial({{map: earthTexture, color:0xffffff, emissive:0x03131f, emissiveIntensity:0.8, shininess:15, transparent:true, opacity:.95}})
  );
  group.add(globe);

  // 线框层
  const wire = new THREE.Mesh(
    new THREE.SphereGeometry(100.5, 36, 18),
    new THREE.MeshBasicMaterial({{color: accentHex, wireframe:true, transparent:true, opacity:.18}})
  );
  group.add(wire);

  // 大气层
  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(104, 48, 24),
    new THREE.MeshBasicMaterial({{color: accentHex, transparent:true, opacity:.06, side:THREE.BackSide}})
  );
  group.add(atmosphere);

  // 星空
  const starGeometry = new THREE.BufferGeometry();
  const starPositions = [];
  const starColors = [];
  const sc = TC.star_colors;
  for (let i=0; i<500; i++) {{
    const theta = (i * 2.39996) % (Math.PI * 2);
    const phi = Math.acos(1 - 2 * ((i + .5) / 500));
    const radius = 180 + (i % 9) * 4;
    starPositions.push(radius * Math.sin(phi) * Math.cos(theta), radius * Math.cos(phi), radius * Math.sin(phi) * Math.sin(theta));
    const colorHex = sc[i % sc.length];
    const cv = parseInt(colorHex.replace('#',''), 16);
    starColors.push((cv >> 16 & 255)/255, (cv >> 8 & 255)/255, (cv & 255)/255);
  }}
  starGeometry.setAttribute('position', new THREE.Float32BufferAttribute(starPositions, 3));
  starGeometry.setAttribute('color', new THREE.Float32BufferAttribute(starColors, 3));
  scene.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({{size:1.4, transparent:true, opacity:.7, vertexColors:true}})));

  // 灯光
  scene.add(new THREE.AmbientLight(0x4a7080, 0.6));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight.position.set(200, 100, 200);
  scene.add(dirLight);

  const resize = () => {{
    const parent = canvas.parentElement;
    const width = (parent ? parent.clientWidth : canvas.clientWidth) || 600;
    const height = (parent ? parent.clientHeight : canvas.clientHeight) || 460;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }};

  // 面板尺寸变化时自适应，保证地球始终居中
  if (window.ResizeObserver) {{
    new ResizeObserver(() => resize()).observe(canvas.parentElement || canvas);
  }}
  window.addEventListener('resize', resize);

  let animating = true;
  const animate = () => {{
    if (!animating) {{ requestAnimationFrame(animate); return; }}
    group.rotation.y += .0012;
    group.rotation.x = Math.sin(Date.now() * .00012) * .03;
    resize();
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
  }};
  resize();
  animate();
}})();

// === ECharts 模块卡片图表 ===
(function initCharts() {{
  if (!window.echarts) return;
  const accent = TC.accent;
  const amber = TC.amber;
  const red = TC.red;
  const muted = TC.muted;
  const ink = TC.ink;
  const line = TC.line;

  // 1. 分类雷达
  const radarChart = echarts.init(document.getElementById('chart-radar'));
  const catNames = STATS.cat_data.map(d => d.name);
  const catValues = STATS.cat_data.map(d => d.value);
  const maxCat = Math.max(1, ...catValues);
  radarChart.setOption({{
    backgroundColor: 'transparent',
    radar: {{
      indicator: catNames.map(n => ({{name: n.length > 4 ? n.slice(0,3)+'…' : n, max: maxCat}})),
      shape: 'polygon',
      radius: '60%',
      center: ['50%', '52%'],
      splitNumber: 3,
      axisName: {{color: muted, fontSize: 7}},
      splitLine: {{lineStyle: {{color: line, opacity:.5}}}},
      splitArea: {{areaStyle: {{color: ['transparent', 'transparent']}}}},
      axisLine: {{lineStyle: {{color: line, opacity:.5}}}}
    }},
    series: [{{
      type: 'radar',
      data: [{{value: catValues, name: '分类分布', areaStyle: {{color: accent + '30'}}, lineStyle: {{color: accent, width:1.5}}, itemStyle: {{color: accent}}}}]
    }}]
  }});

  // 2. 省份热力（横向柱状图）
  const provChart = echarts.init(document.getElementById('chart-province'));
  const provNames = STATS.prov_data.map(d => d.name);
  const provValues = STATS.prov_data.map(d => d.value);
  provChart.setOption({{
    backgroundColor: 'transparent',
    grid: {{left: 45, right: 18, top: 4, bottom: 4}},
    xAxis: {{type: 'value', show: false, max: Math.max(1, ...provValues)}},
    yAxis: {{
      type: 'category', data: provNames, inverse: true,
      axisLabel: {{color: muted, fontSize: 8}},
      axisLine: {{show: false}}, axisTick: {{show: false}}
    }},
    series: [{{
      type: 'bar', data: provValues,
      itemStyle: {{color: accent, borderRadius: [0, 3, 3, 0]}},
      barWidth: 6,
      label: {{show: true, position: 'right', color: ink, fontSize: 8}}
    }}]
  }});

  // 3. 年度趋势（折线图）
  const yearChart = echarts.init(document.getElementById('chart-year'));
  const years = STATS.year_data.map(d => d.year);
  const yearCounts = STATS.year_data.map(d => d.count);
  yearChart.setOption({{
    backgroundColor: 'transparent',
    grid: {{left: 22, right: 12, top: 8, bottom: 18}},
    xAxis: {{
      type: 'category', data: years,
      axisLabel: {{color: muted, fontSize: 8}},
      axisLine: {{lineStyle: {{color: line}}}}
    }},
    yAxis: {{type: 'value', show: false}},
    series: [{{
      type: 'line', data: yearCounts, smooth: true,
      lineStyle: {{color: accent, width: 2}},
      itemStyle: {{color: accent}},
      areaStyle: {{color: accent + '20'}},
      symbol: 'circle', symbolSize: 4
    }}]
  }});

  // 4. 来源健康（环形图）
  const sourceChart = echarts.init(document.getElementById('chart-source'));
  sourceChart.setOption({{
    backgroundColor: 'transparent',
    series: [{{
      type: 'pie', radius: ['42%', '68%'], center: ['50%', '50%'],
      data: [
        {{name: '正常', value: STATS.ok_sources, itemStyle: {{color: accent}}}},
        {{name: '失败', value: STATS.failed_sources, itemStyle: {{color: red}}}},
        {{name: '待审核', value: STATS.pending_sources, itemStyle: {{color: amber}}}}
      ],
      label: {{color: muted, fontSize: 8, formatter: '{{b}} {{c}}'}},
      labelLine: {{lineStyle: {{color: line}}}}
    }}]
  }});

  // 5. 知识图谱（关系简图）
  const graphChart = echarts.init(document.getElementById('chart-graph'));
  const graphNodes = Math.max(1, STATS.graph_nodes);
  const nodeCount = Math.min(12, graphNodes);
  const fakeNodes = [];
  for (let i = 0; i < nodeCount; i++) {{
    const angle = (i / nodeCount) * Math.PI * 2;
    const r = 35;
    fakeNodes.push({{
      name: 'N' + i,
      x: 50 + r * Math.cos(angle),
      y: 50 + r * Math.sin(angle),
      symbolSize: Math.max(4, 10 - i * 0.5),
      itemStyle: {{color: i === 0 ? accent : (i % 3 === 0 ? amber : accent + '80')}}
    }});
  }}
  const fakeLinks = [];
  for (let i = 1; i < nodeCount; i++) {{
    fakeLinks.push({{source: 'N0', target: 'N' + i, lineStyle: {{color: accent + '40', width: 0.5}}}});
  }}
  graphChart.setOption({{
    backgroundColor: 'transparent',
    series: [{{
      type: 'graph', layout: 'none',
      data: fakeNodes, links: fakeLinks,
      label: {{show: false}},
      lineStyle: {{opacity: 0.4}}
    }}]
  }});

  // 6. 实时脉搏（仪表盘）
  const pulseChart = echarts.init(document.getElementById('chart-pulse'));
  const rate = Math.round(STATS.extraction_rate * 100);
  pulseChart.setOption({{
    backgroundColor: 'transparent',
    series: [{{
      type: 'gauge', radius: '80%', center: ['50%', '55%'],
      startAngle: 200, endAngle: -20,
      min: 0, max: 100,
      axisLine: {{lineStyle: {{width: 5, color: [[rate/100, accent], [1, line]]}}}},
      pointer: {{itemStyle: {{color: accent}}, length: '60%', width: 3}},
      axisTick: {{show: false}},
      splitLine: {{show: false}},
      axisLabel: {{show: false}},
      detail: {{
        valueAnimation: true, formatter: '{{value}}%',
        color: ink, fontSize: 14, offsetCenter: [0, '30%']
      }},
      data: [{{value: rate}}]
    }}]
  }});

  // 响应式
  window.addEventListener('resize', () => {{
    [radarChart, provChart, yearChart, sourceChart, graphChart, pulseChart].forEach(c => c.resize());
  }});
}})();

// === 模块卡片点击导航 ===
document.querySelectorAll('.module-card').forEach(card => {{
  card.addEventListener('click', () => {{
    const nav = card.dataset.nav;
    try {{
      const url = new URL(window.parent.location.href);
      url.searchParams.set('nav_view', nav);
      window.parent.location.href = url.toString();
    }} catch(e) {{
      window.parent.postMessage({{type: 'navigate', view: nav}}, '*');
    }}
  }});
}});
</script>
</body></html>"""


def render_home(records: list[dict[str, Any]], sources: list[dict[str, Any]], quality: dict[str, Any], graph: dict[str, Any], theme_id: str = "cyber_neon") -> None:
    """渲染科技感首页。"""
    stats = _compute_home_stats(records, sources, quality, graph)
    html_content = _home_html(stats, theme_id)
    components.html(html_content, height=860, scrolling=True)
