"""科技感首页仪表盘 — 3D地球为中心 + 环绕可视化模块卡片。

首页设计理念：
- 中央：3D旋转地球 + 飞线 + 数据节点（Three.js）
- 环绕：6个可点击模块卡片（分类雷达、省份热力、年度趋势、来源健康、知识图谱、实时脉搏）
- 顶部：KPI数据条（总公告、监测省份、政策类、民生类）
- 底部：数据管道状态条

点击模块卡片 → 通过 Streamlit session_state 切换到对应详情页。
"""

from __future__ import annotations

import html
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from branding import BRAND_NAME, load_logo_svg
from extractors.category_rules import industry_l2_names
from theme_config import get_theme_js_colors

try:
    from map_dashboard import publish_geojson as _publish_geojson
except Exception:
    _publish_geojson = None

ROOT = Path(__file__).resolve().parent
# 浏览器直接拉 /app/static 下的 vendor 脚本（可缓存），不再把 1.8MB 内联进
# iframe srcdoc —— 每次 rerun 都重传+重解析是"切换场景卡顿"的主因。
VENDOR_URL = "/app/static/vendor"

# 图表用最近 N 条做样本（分类/地区/年度趋势看近期分布，够用且快），但**总量和
# 覆盖省份必须是真的**。原来两者都用样本算，于是首页顶上写「信息总量 1000
# 已入库公开信息」，而库里其实有 6403 条 —— 头条数字直接小了 6.4 倍，看着
# 就不直观、也不可信。样本范围在卡片脚注里写明。
SAMPLE_LABEL = "按最近 1000 条统计"

# 体检指标中文标签（与 collector_ops_view.METRIC_LABEL 对齐）
METRIC_LABEL_FAIL = {
    "median_content_chars": "正文中位字数", "under_200_chars_rate": "正文过短率",
    "chrome_residue_rate": "页面残留率", "amount_rate": "含金额比例",
    "date_rate": "含日期比例", "summary_rate": "摘要覆盖率",
    "publish_date_rate": "发布日期覆盖率", "region_coverage_rate": "地区归属覆盖率",
    "cross_level_programs": "跨层级专项数", "section_page_rate": "栏目页混入率",
    "history_year_coverage": "近10年年份覆盖", "current_tier_rate": "当期比例",
    "summary_compression_high": "摘要过压缩", "summary_compression_low": "摘要欠压缩",
    "summary_breadcrumb_rate": "摘要含面包屑", "summary_toolbar_rate": "摘要含工具条",
    "dropped_amount": "金额丢失率", "attachment_download_rate": "附件下载率",
    "compare_coverage_rate": "对比字段覆盖", "corroboration_pass_rate": "交叉印证通过率",
}

# 体检指标中文标签（与 collector_ops_view.METRIC_LABEL 对齐）
METRIC_LABEL_FAIL = {
    "median_content_chars": "正文中位字数", "under_200_chars_rate": "正文过短率",
    "chrome_residue_rate": "页面残留率", "amount_rate": "含金额比例",
    "date_rate": "含日期比例", "summary_rate": "摘要覆盖率",
    "publish_date_rate": "发布日期覆盖率", "region_coverage_rate": "地区归属覆盖率",
    "cross_level_programs": "跨层级专项数", "section_page_rate": "栏目页混入率",
    "history_year_coverage": "近10年年份覆盖", "current_tier_rate": "当期比例",
    "summary_compression_high": "摘要过压缩", "summary_compression_low": "摘要欠压缩",
    "summary_breadcrumb_rate": "摘要含面包屑", "summary_toolbar_rate": "摘要含工具条",
    "dropped_amount": "金额丢失率", "attachment_download_rate": "附件下载率",
    "compare_coverage_rate": "对比字段覆盖", "corroboration_pass_rate": "交叉印证通过率",
}


def db_facts(db_path: str = "data/beacon.db") -> dict[str, Any]:
    """全库真实总量与覆盖省份数。一条 SQL 拿两个值。"""
    path = ROOT / db_path
    if not path.exists():
        return {"total": 0, "provinces": 0}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            total = int(con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0])
            provinces = int(con.execute(
                "SELECT COUNT(DISTINCT region_province) FROM announcements "
                "WHERE region_province IS NOT NULL AND TRIM(region_province) != ''"
            ).fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return {"total": 0, "provinces": 0}
    return {"total": total, "provinces": provinces}


def db_distributions(db_path: str = "data/beacon.db") -> dict[str, Any]:
    """全库分布数据（分类/省份/年度），用 SQL 聚合而非样本。

    原来用最近 1000 条样本画分布图，但首页头条数字是全库真实值 ——
    口径不一致：头条 6992 条，分布图只反映最近 1000 条。
    改成全库 GROUP BY 聚合，一次查询拿全量分布。
    """
    path = ROOT / db_path
    empty = {"cat_data": [], "prov_data": [], "year_data": [],
             "ind_data": [], "prov_ind_data": []}
    if not path.exists():
        return empty
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cat_rows = con.execute(
                "SELECT COALESCE(NULLIF(TRIM(category_l1), ''), '其他') AS cat, COUNT(*) AS n "
                "FROM announcements GROUP BY cat ORDER BY n DESC LIMIT 12"
            ).fetchall()
            # ── 行业维度（2026-10-05 新增）─────────────────────
            #
            # 「分类雷达」原先排的是 category_l1，而它**混编了体裁和主题**：
            # 公示公告 / 政策法规 / 政策发布 是**文件长什么样**，
            # 行业政策 / 商业机会 / 人才政策 是**讲什么**。
            # 两者排在一起，等于把「公示公告（2777 条）」和「行业政策（3155 条）」
            # 放在同一张榜上——既不是行业分布，也不是体裁分布，什么都不是。
            #
            # 所以另起一个行业口径：从 category_l2 里取**父分类是行业政策/
            # 行业补贴**的那些子类（依据 config/category_rules.yaml 的父子结构，
            # 不手写白名单——手写会漏掉只在 fallback_categories 里的
            # 「商务贸易」「工业制造」）。
            ind_rows = con.execute(
                "SELECT category_l2 AS ind, COUNT(*) AS n "
                "FROM announcements WHERE category_l2 IN ({inds}) "
                "GROUP BY ind ORDER BY n DESC LIMIT 14".format(
                    inds=",".join("'%s'" % s for s in _industry_l2(con))
                )
            ).fetchall()
            prov_ind_rows = con.execute(
                "SELECT region_province AS prov, category_l2 AS ind, COUNT(*) AS n "
                "FROM announcements WHERE region_province IS NOT NULL "
                "AND TRIM(region_province) != '' AND category_l2 IN ({inds}) "
                "GROUP BY prov, ind".format(
                    inds=",".join("'%s'" % s for s in _industry_l2(con))
                )
            ).fetchall()
            prov_rows = con.execute(
                "SELECT region_province AS prov, COUNT(*) AS n "
                "FROM announcements WHERE region_province IS NOT NULL AND TRIM(region_province) != '' "
                "GROUP BY prov ORDER BY n DESC LIMIT 10"
            ).fetchall()
            year_rows = con.execute(
                "SELECT substr(publish_date, 1, 4) AS yr, COUNT(*) AS n "
                "FROM announcements WHERE publish_date IS NOT NULL AND substr(publish_date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]' "
                "GROUP BY yr ORDER BY yr"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return empty
    return {
        "cat_data": [{"name": cat, "value": n} for cat, n in cat_rows],
        "ind_data": [{"name": ind, "value": n} for ind, n in ind_rows],
        "prov_ind_data": [{"prov": p, "ind": i, "value": n}
                          for p, i, n in prov_ind_rows],
        "prov_data": [{"name": prov, "value": n} for prov, n in prov_rows],
        "year_data": [{"year": yr, "count": n} for yr, n in year_rows],
    }


def _industry_l2(con: sqlite3.Connection) -> list[str]:
    """行业门类子类名 —— 口径提炼在 extractors/category_rules.industry_l2_names()。"""
    return industry_l2_names()


PROVIND_JS = """  // 1b. 省份 × 行业：堆叠条形，每省一根，按总量降序。
  //
  // 这是「各省政策发展方向研究」的入口图：横轴是条数，颜色是行业。
  // 关键是**只保留有行业门类的记录** —— 体裁词（公示/名单/办法）不属于任何
  // 行业，混进来会让这张图退化成「各省发了多少公示」。
  (function () {
    var provInd = STATS.prov_ind_data || [];
    var el = document.getElementById('chart-provind');
    if (!window.echarts || !el || !provInd.length) return;

    var byProv = {}, names = [];
    provInd.forEach(function (d) {
      if (!byProv[d.prov]) { byProv[d.prov] = {total: 0, cells: {}}; names.push(d.prov); }
      byProv[d.prov].total += d.value;
      byProv[d.prov].cells[d.ind] = d.value;
    });

    // 行业按全省总量排序，只画前 6 —— 尾部行业每省 1~2 条，画出来是噪声。
    var sum = {};
    provInd.forEach(function (d) { sum[d.ind] = (sum[d.ind] || 0) + d.value; });
    var inds = Object.keys(sum).sort(function (a, b) { return sum[b] - sum[a]; }).slice(0, 6);
    var order = names.sort(function (a, b) { return byProv[b].total - byProv[a].total; }).slice(0, 8);

    var pal = [accent, amber, '#3fb9a5', '#8b7ff5', '#e8746a', '#d4a04e'];
    var chart = echarts.init(el);
    chart.setOption({
      backgroundColor: 'transparent',
      grid: {left: 62, right: 12, top: 20, bottom: 4},
      legend: {top: 0, right: 0, itemWidth: 8, itemHeight: 8,
               textStyle: {color: muted, fontSize: 8}},
      tooltip: {trigger: 'axis', axisPointer: {type: 'shadow'}},
      xAxis: {type: 'value', show: false},
      yAxis: {type: 'category', data: order, inverse: true,
              axisLabel: {color: muted, fontSize: 8},
              axisLine: {show: false}, axisTick: {show: false}},
      series: inds.map(function (ind, i) {
        return {
          name: ind, type: 'bar', stack: 'pi',
          data: order.map(function (pv) { return byProv[pv].cells[ind] || 0; }),
          itemStyle: {color: pal[i % pal.length]},
          barWidth: 7
        };
      })
    });
    if (window.ResizeObserver) new ResizeObserver(function () { chart.resize(); }).observe(el);
  })();
"""

def health_fails() -> list[dict[str, Any]]:
    """读取体检未达标指标，用于首页顶部红色告警条。"""
    try:
        import collector_ops as ops
        findings = (ops.read_health() or {}).get("findings") or []
        return [f for f in findings if not f.get("ok")]
    except Exception:
        return []


def _compute_home_stats(records: list[dict[str, Any]], sources: list[dict[str, Any]], quality: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    """计算首页所需的统计数据。

    口径分两套，别混：
      * total / provinces —— 全库真实值，走 SQL。首页头条数字必须是真的。
      * policy / livelihood / cat_data / prov_data / year_data —— 最近
        len(records) 条样本（app.py 的 load_snapshot 取 1000 条）。看近期分布
        够用，但不能当总量说。
    """
    facts = db_facts()
    dist = db_distributions()  # 全库 SQL 聚合，不再用样本画分布
    province_counts = Counter()
    category_counts = Counter()
    year_counts = Counter()
    policy_count = 0
    livelihood_count = 0
    recent_count = 0
    now = datetime.now()

    # 政策类 / 民生类口径：与 AGENTS.md §4.1 的 12 枚举对齐。
    #   政策类 = 行业政策 + 行业补贴 + 人才政策 + 养老与社区 + 招商
    #   民生类 = 活动通知 + 招生信息 + 公招信息 + 招聘信息
    #   招标信息 / 商业机会 / 其他 不归入这两个桶（它们是独立的商业/通用类）。
    policy_cats = {"行业政策", "行业补贴", "人才政策", "养老与社区", "招商"}
    livelihood_cats = {"活动通知", "招生信息", "公招信息", "招聘信息"}

    for record in records:
        prov = record.get("region_province")
        if prov:
            province_counts[prov] += 1
        cat = record.get("category_l1") or "其他"
        category_counts[cat] += 1
        date_str = str(record.get("publish_date") or "")
        if date_str[:4].isdigit():
            year_counts[date_str[:4]] += 1
        if cat in policy_cats:
            policy_count += 1
        if cat in livelihood_cats:
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

    cat_data = dist["cat_data"]
    prov_data = dist["prov_data"]
    year_data = dist["year_data"]
    ind_data = dist["ind_data"]
    prov_ind_data = dist["prov_ind_data"]

    # ── 质量指标：从全库直接查，不走样本 ──
    # policy/livelihood 也从全库算，否则 KPI 头条和分布图口径不一致。
    amount_count = 0
    needs_review_count = 0
    corroborated_count = 0
    focus_tracked_count = 0
    month_prev = 0
    month_curr = 0
    policy_count = 0
    livelihood_count = 0
    try:
        con = sqlite3.connect(f"file:{ROOT / 'data/beacon.db'}?mode=ro", uri=True)
        try:
            amount_count = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE amount IS NOT NULL AND TRIM(amount) != ''"
            ).fetchone()[0])
            needs_review_count = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE needs_review = 1"
            ).fetchone()[0])
            corroborated_count = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE corroboration_status = 'corroborated'"
            ).fetchone()[0])
            focus_tracked_count = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE focus_tracked = 1"
            ).fetchone()[0])
            # 政策类 / 民生类全库计数
            policy_count = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE category_l1 IN "
                "('行业政策','行业补贴','人才政策','养老与社区','招商')"
            ).fetchone()[0])
            livelihood_count = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE category_l1 IN "
                "('活动通知','招生信息','公招信息','招聘信息')"
            ).fetchone()[0])
            # 本月与上月对比
            now_dt = datetime.now()
            month_curr = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE strftime('%Y-%m', publish_date) = ?",
                (f"{now_dt.year}-{now_dt.month:02d}",),
            ).fetchone()[0])
            prev_dt = now_dt.replace(day=1) - timedelta(days=1)
            month_prev = int(con.execute(
                "SELECT COUNT(*) FROM announcements WHERE strftime('%Y-%m', publish_date) = ?",
                (f"{prev_dt.year}-{prev_dt.month:02d}",),
            ).fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        pass

    total_real = facts["total"] or 1
    month_delta = ((month_curr - month_prev) / month_prev * 100) if month_prev else 0

    # 年度跨度：用实际 year_data 的首尾年份算，不再硬编码"5 年"
    year_span_label = "年度趋势"
    if year_data and len(year_data) >= 2:
        yr_first = year_data[0]["year"]
        yr_last = year_data[-1]["year"]
        year_span_label = f"{yr_last} 年数据" if yr_first == yr_last else f"{yr_first}–{yr_last} 跨度"
    elif year_data:
        year_span_label = f"{year_data[0]['year']} 年数据"

    return {
        "total": facts["total"],
        "sample": len(records),
        "provinces": facts["provinces"],
        "policy": policy_count,
        "livelihood": livelihood_count,
        "recent": recent_count,
        "ok_sources": ok_sources,
        "failed_sources": failed_sources,
        "pending_sources": pending_sources,
        "extraction_rate": float(quality.get("extraction_success_rate") or 0),
        "graph_nodes": int(graph.get("nodes") or 0),
        "graph_edges": int(graph.get("edges") or 0),
        "graph_evidence": int(graph.get("evidence") or 0),
        "cat_data": cat_data,
        "ind_data": ind_data,
        "prov_ind_data": prov_ind_data,
        "prov_data": prov_data,
        "year_data": year_data,
        "year_span_label": year_span_label,
        # 质量指标
        "amount_count": amount_count,
        "amount_rate": round(amount_count / total_real * 100, 1),
        "needs_review": needs_review_count,
        "corroborated": corroborated_count,
        "corroboration_rate": round(corroborated_count / total_real * 100, 1),
        "focus_tracked": focus_tracked_count,
        "source_health_rate": round(ok_sources / max(1, ok_sources + failed_sources) * 100, 1),
        "month_curr": month_curr,
        "month_prev": month_prev,
        "month_delta": round(month_delta, 1),
    }


def _home_html(stats: dict[str, Any], theme_id: str, geo_base: str = "") -> str:
    """生成首页完整HTML。"""
    tc = get_theme_js_colors(theme_id)

    stats_json = json.dumps(stats, ensure_ascii=False)
    theme_colors_json = json.dumps(tc, ensure_ascii=False)
    brand_json = json.dumps(BRAND_NAME, ensure_ascii=False)
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
  padding:10px 16px 10px 14px;
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
        .brand-left {{display:flex;align-items:center;gap:12px;padding-left:4px;min-width:0}}
        .brand-mark {{
          width:48px;height:48px;flex:0 0 auto;border:2px solid var(--tc-accent);border-radius:10px;
          display:grid;place-items:center;color:var(--tc-accent);
          box-shadow:0 0 20px rgba(var(--accent_rgb),0.3),inset 0 1px 0 rgba(255,255,255,.06);
          background:rgba(255,255,255,.03);
          position:relative;overflow:hidden;
        }}
        .brand-mark::before {{
          content:'';position:absolute;inset:-2px;border-radius:12px;
          background:linear-gradient(135deg,transparent 40%,rgba(var(--accent_rgb),0.2),transparent 60%);
          animation:shimmer 3s ease-in-out infinite;
        }}
        @keyframes shimmer {{
          0% {{transform:translateX(-100%) rotate(45deg)}}
          50% {{transform:translateX(100%) rotate(45deg)}}
          100% {{transform:translateX(-100%) rotate(45deg)}}
        }}
        .brand-mark svg {{width:68%;height:68%;display:block;filter:drop-shadow(0 0 6px var(--tc-accent))}}
        .brand-sub {{font-size:11px;color:var(--tc-muted);letter-spacing:.2em;text-transform:uppercase;margin-top:2px;font-weight:700}}
.brand-right {{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--tc-muted);white-space:nowrap}}
.brand-right .live-dot {{
  width:6px;height:6px;border-radius:50%;background:var(--tc-accent);
  box-shadow:0 0 0 3px rgba(255,255,255,.04),0 0 8px var(--tc-accent);
  animation:pulse 2s infinite;
}}

/* === KPI 数据条 === */
.kpi-strip {{
  display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:8px;
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
  display:flex;justify-content:flex-start;align-items:center;gap:18px;
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
.status-bar .left {{display:flex;align-items:center;gap:14px}}
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
   .kpi-strip {{grid-template-columns:repeat(3,1fr)}}
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
        <div class="brand-sub">灯塔后端工作台 OSINT</div>
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
      <div class="value">{stats['total']:,}</div>
      <div class="note">全库已入库公告 · 本月 {stats['month_curr']:,} 条</div>
      <div class="mini-bar" style="width:100%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">监测省份</div>
      <div class="value">{stats['provinces']}</div>
      <div class="note">有数据行政区域</div>
      <div class="mini-bar" style="width:{min(100, stats['provinces'] / 34 * 100):.0f}%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">来源健康</div>
      <div class="value">{stats['source_health_rate']:.1f}%</div>
      <div class="note">{stats['ok_sources']} 正常 / {stats['failed_sources']} 异常</div>
      <div class="mini-bar" style="width:{stats['source_health_rate']:.0f}%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">金额抽取</div>
      <div class="value">{stats['amount_rate']:.1f}%</div>
      <div class="note">{stats['amount_count']} 条有金额 · 门槛 15%</div>
      <div class="mini-bar" style="width:{min(100, stats['amount_rate'] / 15 * 100):.0f}%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">交叉验证</div>
      <div class="value">{stats['corroboration_rate']:.1f}%</div>
      <div class="note">{stats['corroborated']} 条多源印证</div>
      <div class="mini-bar" style="width:{min(100, stats['corroboration_rate'] * 10):.0f}%"></div>
    </div>
    <div class="kpi-card entrance-fade">
      <div class="label">待复核</div>
      <div class="value">{stats['needs_review']}</div>
      <div class="note">needs_review · 重点跟踪 {stats['focus_tracked']}</div>
      <div class="mini-bar" style="width:{min(100, stats['needs_review'] / max(1, stats['total']) * 200):.0f}%"></div>
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

      <!-- 模块1：行业分布（2026-10-05 由「分类分布」改来） -->
      <div class="module-card" data-nav="公告检索">
        <div class="mod-header">
          <div class="mod-icon">行</div>
          <div>
            <div class="mod-title">行业分布</div>
            <div class="mod-subtitle">Top 8 行业门类</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-radar"></div>
        <div class="mod-footer">
          <span>{len(stats['ind_data'])} 个行业</span>
          <span class="mod-enter">进入模块</span>
        </div>
      </div>

      <!-- 模块1b：省份 × 行业（2026-10-05 新增） -->
      <div class="module-card" data-nav="中国地图">
        <div class="mod-header">
          <div class="mod-icon">矩</div>
          <div>
            <div class="mod-title">省份 × 行业</div>
            <div class="mod-subtitle">各省的政策侧重</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-provind"></div>
        <div class="mod-footer">
          <span>{len(stats['prov_ind_data'])} 个省份×行业组合</span>
          <span class="mod-enter">横向对比</span>
        </div>
      </div>

      <!-- 模块2：省份热力 -->
      <div class="module-card" data-nav="中国地图">
        <div class="mod-header">
          <div class="mod-icon">图</div>
          <div>
            <div class="mod-title">地区分布</div>
            <div class="mod-subtitle">中国地图</div>
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
          <span>{stats['year_span_label']}</span>
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

      <!-- 模块5：图谱摘要 -->
      <div class="module-card" data-nav="知识图谱">
        <div class="mod-header">
          <div class="mod-icon">谱</div>
          <div>
            <div class="mod-title">图谱摘要</div>
            <div class="mod-subtitle">节点·关系·证据</div>
          </div>
        </div>
        <div class="mod-chart" id="chart-graph"></div>
        <div class="mod-footer">
          <span>{stats['graph_nodes']} 节点 · {stats['graph_edges']} 边 · {stats['graph_evidence']} 证据</span>
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

<script src="{VENDOR_URL}/three.min.js"></script>
<script src="{VENDOR_URL}/echarts.min.js"></script>
<script>
const STATS = {stats_json};
const TC = {theme_colors_json};
const GEO_BASE = {json.dumps(geo_base)};

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

  // 程序化地球纹理（2048×1024 高分辨率 + 经纬网格）
  const earthCanvas = document.createElement('canvas');
  earthCanvas.width = 2048; earthCanvas.height = 1024;
  const ectx = earthCanvas.getContext('2d');
  ectx.fillStyle = TC.ocean; ectx.fillRect(0, 0, 2048, 1024);
  ectx.fillStyle = TC.land;
  // 欧亚大陆
  ectx.beginPath(); ectx.ellipse(1120, 360, 400, 160, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(1300, 320, 200, 100, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(1000, 400, 150, 80, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(1200, 450, 100, 60, 0, 0, Math.PI*2); ectx.fill();
  // 非洲
  ectx.beginPath(); ectx.ellipse(1080, 600, 140, 180, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(1100, 720, 80, 100, 0, 0, Math.PI*2); ectx.fill();
  // 北美
  ectx.beginPath(); ectx.ellipse(440, 340, 240, 140, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(350, 450, 100, 60, 0, 0, Math.PI*2); ectx.fill();
  // 南美
  ectx.beginPath(); ectx.ellipse(560, 680, 100, 160, 0, 0, Math.PI*2); ectx.fill();
  // 澳大利亚
  ectx.beginPath(); ectx.ellipse(1600, 680, 120, 70, 0, 0, Math.PI*2); ectx.fill();
  // 印度 + 东南亚
  ectx.beginPath(); ectx.ellipse(1300, 500, 60, 80, 0, 0, Math.PI*2); ectx.fill();
  ectx.beginPath(); ectx.ellipse(1450, 550, 80, 40, 0, 0, Math.PI*2); ectx.fill();
  // 中国高亮
  ectx.fillStyle = TC.china_highlight;
  ectx.beginPath(); ectx.ellipse(1360, 340, 120, 80, 0, 0, Math.PI*2); ectx.fill();
  // 经纬网格
  ectx.strokeStyle = TC.accent + '18';
  ectx.lineWidth = 1;
  for (let lon = 0; lon < 360; lon += 30) {{
    ectx.beginPath(); ectx.moveTo(lon * 2048 / 360, 0); ectx.lineTo(lon * 2048 / 360, 1024); ectx.stroke();
  }}
  for (let lat = -60; lat <= 60; lat += 30) {{
    ectx.beginPath(); ectx.moveTo(0, (90 - lat) * 1024 / 180); ectx.lineTo(2048, (90 - lat) * 1024 / 180); ectx.stroke();
  }}

  const earthTexture = new THREE.CanvasTexture(earthCanvas);
  const accentHex = parseInt(TC.accent.replace('#',''), 16);
  const amberHex = parseInt((TC.amber || TC.accent).replace('#',''), 16);
  const globe = new THREE.Mesh(
    new THREE.SphereGeometry(100, 64, 32),
    new THREE.MeshPhongMaterial({{map: earthTexture, color:0xffffff, emissive:0x03131f, emissiveIntensity:0.8, shininess:15, transparent:true, opacity:.95}})
  );
  group.add(globe);

  // 线框层
  const wire = new THREE.Mesh(
    new THREE.SphereGeometry(100.5, 36, 18),
    new THREE.MeshBasicMaterial({{color: accentHex, wireframe:true, transparent:true, opacity:.15}})
  );
  group.add(wire);

  // 多层大气辉光（3 层叠加，外层更柔）
  const atmos1 = new THREE.Mesh(
    new THREE.SphereGeometry(102, 48, 24),
    new THREE.MeshBasicMaterial({{color: accentHex, transparent:true, opacity:.10, side:THREE.BackSide}})
  );
  group.add(atmos1);
  const atmos2 = new THREE.Mesh(
    new THREE.SphereGeometry(106, 48, 24),
    new THREE.MeshBasicMaterial({{color: accentHex, transparent:true, opacity:.05, side:THREE.BackSide}})
  );
  group.add(atmos2);
  const atmos3 = new THREE.Mesh(
    new THREE.SphereGeometry(112, 48, 24),
    new THREE.MeshBasicMaterial({{color: accentHex, transparent:true, opacity:.02, side:THREE.BackSide}})
  );
  group.add(atmos3);

  // 地球上的一行字：Canvas 贴图 + Sprite，贴在球心、永远正对相机
  const labelCanvas = document.createElement('canvas');
  labelCanvas.width = 1024; labelCanvas.height = 128;
  const lctx = labelCanvas.getContext('2d');
  lctx.font = '700 62px "HarmonyOS Sans SC","Microsoft YaHei",sans-serif';
  lctx.textAlign = 'center'; lctx.textBaseline = 'middle';
  lctx.shadowColor = TC.accent; lctx.shadowBlur = 26;
  lctx.fillStyle = TC.accent;
  lctx.fillText({brand_json}, 512, 70);
  const label = new THREE.Sprite(new THREE.SpriteMaterial({{
    map: new THREE.CanvasTexture(labelCanvas), transparent:true, opacity:.92, depthTest:false
  }}));
  label.scale.set(190, 24, 1);
  label.position.set(0, 0, 0);
  label.renderOrder = 10;
  scene.add(label);

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

  // --- 中国城市标记 + 北京出发弧线 ---
  function latLonToVec3(lat, lon, r) {{
    const phi = (90 - lat) * Math.PI / 180;
    const theta = (lon + 180) * Math.PI / 180;
    return new THREE.Vector3(
      -r * Math.sin(phi) * Math.cos(theta),
      r * Math.cos(phi),
      r * Math.sin(phi) * Math.sin(theta)
    );
  }}
  const cities = [
    {{name:'北京', lat:39.9, lon:116.4}},
    {{name:'上海', lat:31.2, lon:121.5}},
    {{name:'广州', lat:23.1, lon:113.3}},
    {{name:'成都', lat:30.7, lon:104.1}},
    {{name:'武汉', lat:30.6, lon:114.3}},
    {{name:'西安', lat:34.3, lon:108.9}},
    {{name:'重庆', lat:29.6, lon:106.5}},
    {{name:'哈尔滨', lat:45.8, lon:126.5}},
    {{name:'昆明', lat:25.0, lon:102.7}},
    {{name:'拉萨', lat:29.7, lon:91.1}}
  ];
  const beijingVec = latLonToVec3(cities[0].lat, cities[0].lon, 100);
  const pulseRings = [];
  const arcLines = [];
  cities.forEach(function(city, idx) {{
    const pos = latLonToVec3(city.lat, city.lon, 100);
    // 标记点
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(idx === 0 ? 2.0 : 1.2, 12, 8),
      new THREE.MeshBasicMaterial({{color: idx === 0 ? amberHex : accentHex}})
    );
    dot.position.copy(pos);
    group.add(dot);
    // 脉冲环
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(2, 2.5, 16),
      new THREE.MeshBasicMaterial({{color: accentHex, transparent:true, opacity:.6, side:THREE.DoubleSide}})
    );
    ring.position.copy(pos);
    ring.lookAt(0, 0, 0);
    pulseRings.push({{mesh: ring, offset: idx * 0.3}});
    group.add(ring);
    // 弧线（从北京出发，跳过北京自身）
    if (idx > 0) {{
      const mid = new THREE.Vector3().addVectors(beijingVec, pos).multiplyScalar(0.5);
      const dist = beijingVec.distanceTo(pos);
      mid.normalize().multiplyScalar(100 + dist * 0.35);
      const curve = new THREE.QuadraticBezierCurve3(beijingVec, mid, pos);
      const points = curve.getPoints(40);
      const arcGeom = new THREE.BufferGeometry().setFromPoints(points);
      const arc = new THREE.Line(arcGeom, new THREE.LineBasicMaterial({{color: accentHex, transparent:true, opacity:.35}}));
      arcLines.push({{mesh: arc, offset: idx * 0.2}});
      group.add(arc);
    }}
  }});

  // 灯光
  scene.add(new THREE.AmbientLight(0x4a7080, 0.6));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight.position.set(200, 100, 200);
  scene.add(dirLight);
  const dirLight2 = new THREE.DirectionalLight(accentHex, 0.3);
  dirLight2.position.set(-200, -50, -100);
  scene.add(dirLight2);

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

  // --- 鼠标拖拽旋转 + 惯性 ---
  let dragRotY = 0, dragRotX = 0, velY = 0.0012, velX = 0;
  let isDragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener('pointerdown', function(e) {{
    isDragging = true; lastX = e.clientX; lastY = e.clientY;
    velY = 0; velX = 0;
  }});
  window.addEventListener('pointermove', function(e) {{
    if (!isDragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    velY = dx * 0.005; velX = dy * 0.005;
    dragRotY += velY; dragRotX += velX;
    dragRotX = Math.max(-0.8, Math.min(0.8, dragRotX));
    lastX = e.clientX; lastY = e.clientY;
  }});
  window.addEventListener('pointerup', function() {{ isDragging = false; }});

  // --- 动画循环（拖拽惯性 + 脉冲环 + 弧线流动）---
  const animate = () => {{
    if (!isDragging) {{
      velY *= 0.96; velX *= 0.96;
      dragRotY += velY + 0.0012;
      dragRotX += velX;
      dragRotX = Math.max(-0.5, Math.min(0.5, dragRotX));
    }}
    group.rotation.y = dragRotY;
    group.rotation.x = dragRotX;
    // 脉冲环动画
    const t = Date.now() * 0.001;
    pulseRings.forEach(function(pr) {{
      const p = (t + pr.offset) % 2.0;
      const s = 1 + p * 1.5;
      pr.mesh.scale.set(s, s, 1);
      pr.mesh.material.opacity = 0.6 * (1 - p / 2.0);
    }});
    // 弧线流动（透明度呼吸）
    arcLines.forEach(function(al) {{
      al.mesh.material.opacity = 0.2 + 0.25 * Math.sin(t * 1.5 + al.offset);
    }});
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

  // 1. 分类分布（横向柱状图 Top 8）
  const catChart = echarts.init(document.getElementById('chart-radar'));
  // 用行业口径而不是 category_l1。category_l1 混编了体裁（公示公告/政策法规/
  // 政策发布…）和主题（行业政策/商业机会…），排在一起既不是行业分布
  // 也不是体裁分布。行业才是政策研究关心的维度（各省在哪些行业加码）。
  const catTop = STATS.ind_data.slice(0, 8);
  const catNames = catTop.map(d => d.name.length > 4 ? d.name.slice(0,3)+'…' : d.name);
  const catValues = catTop.map(d => d.value);
  catChart.setOption({{
    backgroundColor: 'transparent',
    grid: {{left: 40, right: 18, top: 4, bottom: 4}},
    xAxis: {{type: 'value', show: false, max: Math.max(1, ...catValues)}},
    yAxis: {{
      type: 'category', data: catNames, inverse: true,
      axisLabel: {{color: muted, fontSize: 8}},
      axisLine: {{show: false}}, axisTick: {{show: false}}
    }},
    series: [{{
      type: 'bar', data: catValues,
      itemStyle: {{color: accent, borderRadius: [0, 3, 3, 0]}},
      barWidth: 6,
      label: {{show: true, position: 'right', color: ink, fontSize: 8}}
    }}]
  }});

  {PROVIND_JS}
  // 2. 省份热力（中国地图地理着色 — 替代横向柱状图）
  const provChart = echarts.init(document.getElementById('chart-province'));
  const provData = STATS.prov_data; // [{{name, value}}, ...]
  const provMax = Math.max(1, ...provData.map(d => d.value));
  function renderProvMap() {{
    provChart.setOption({{
      backgroundColor: 'transparent',
      tooltip: {{trigger: 'item', formatter: '{{b}}: {{c}}'}},
      visualMap: {{
        type: 'continuous', min: 0, max: provMax,
        left: 4, bottom: 4, itemWidth: 8, itemHeight: 40,
        text: ['高', '低'], textStyle: {{color: muted, fontSize: 7}},
        inRange: {{color: [accent + '30', accent, amber]}},
        calculable: false
      }},
      series: [{{
        type: 'map', map: 'china-home', roam: false,
        data: provData,
        label: {{show: false}},
        emphasis: {{label: {{show: true, color: ink, fontSize: 8}}}},
        itemStyle: {{borderColor: line, borderWidth: 0.3}},
        emphasisItemStyle: {{areaColor: amber}}
      }}]
    }});
  }}
  if (GEO_BASE) {{
    fetch(GEO_BASE + '/china-province.geojson')
      .then(r => r.json())
      .then(geo => {{ echarts.registerMap('china-home', geo); renderProvMap(); }})
      .catch(() => {{ // GeoJSON 加载失败 → 回退柱状图
        const provNames = provData.map(d => d.name);
        const provValues = provData.map(d => d.value);
        provChart.setOption({{
          backgroundColor: 'transparent',
          grid: {{left: 45, right: 18, top: 4, bottom: 4}},
          xAxis: {{type: 'value', show: false, max: provMax}},
          yAxis: {{type: 'category', data: provNames, inverse: true,
            axisLabel: {{color: muted, fontSize: 8}},
            axisLine: {{show: false}}, axisTick: {{show: false}}}},
          series: [{{type: 'bar', data: provValues,
            itemStyle: {{color: accent, borderRadius: [0, 3, 3, 0]}},
            barWidth: 6, label: {{show: true, position: 'right', color: ink, fontSize: 8}}}}]
        }});
      }});
  }} else {{
    renderProvMap(); // 无 GEO_BASE 时直接尝试（可能已注册）
  }}

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

  // 5. 图谱摘要（真实数据环形图 — 不再用假节点）
  const graphChart = echarts.init(document.getElementById('chart-graph'));
  graphChart.setOption({{
    backgroundColor: 'transparent',
    tooltip: {{trigger: 'item', formatter: '{{b}}: {{c}}'}},
    series: [{{
      type: 'pie', radius: ['45%', '70%'], center: ['50%', '50%'],
      data: [
        {{name: '节点', value: STATS.graph_nodes, itemStyle: {{color: accent}}}},
        {{name: '关系', value: STATS.graph_edges, itemStyle: {{color: amber}}}},
        {{name: '证据', value: STATS.graph_evidence, itemStyle: {{color: accent + '80'}}}}
      ],
      label: {{color: muted, fontSize: 8, formatter: '{{b}}\\n{{c}}'}},
      labelLine: {{lineStyle: {{color: line}}}}
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


@st.cache_data(ttl=300, show_spinner=False)
def _health_summary() -> dict[str, Any]:
    import collector_ops as ops

    findings = (ops.read_health() or {}).get("findings") or []
    return {"ok": sum(1 for item in findings if item.get("ok")), "total": len(findings)}


@st.cache_data(ttl=300, show_spinner=False)
def _flag_overview() -> dict[str, Any]:
    import quality_marks

    data = quality_marks.quality_flags(limit=1)
    return {"counts": data.get("counts") or {}, "labels": dict(quality_marks.FLAG_LABELS),
            "order": list(quality_marks.FLAG_ORDER)}


@st.cache_data(ttl=120, show_spinner=False)
def _freshness() -> dict[str, Any]:
    import quality_marks

    return quality_marks.data_freshness()


@st.cache_data(ttl=60, show_spinner=False)
def _progress() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """当前采集器的实时进度 + 轮次快照。

    原来只返回 ops.read_progress()，那是旧 scripts/batch_collect.py 的
    collect_progress.json，最后写入停在 2026-09-27，页面却一直显示它的
    status: running。真正在跑的 lighthouse 写另一套文件。改成读它，
    顺带把轮次快照一并带回来。
    """
    import collector_ops as ops

    return ops.read_live_progress(), ops.read_lighthouse_status()


def _metric_row(cells: list[tuple[str, str]]) -> str:
    body = "".join(
        f"<div class='metric'><div class='metric-value'>{value}</div>"
        f"<div class='metric-note'>{label}</div></div>"
        for value, label in cells
    )
    return f"<div class='metric-row'>{body}</div>"


def _section(label: str, first: bool = False) -> None:
    margin = "" if first else " style='margin-top:1rem'"
    st.markdown(f"<div class='section-label'{margin}>{label}</div>", unsafe_allow_html=True)


def _render_four_questions(records: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    """总览的四个问题：有什么 / 新不新 / 干不干净 / 在做什么。

    整块放进固定高度的滚动容器：页面不再被四段内容越撑越长，卡片尺寸统一、
    左右栏等高，版面保持整齐（样式见 app.py 的 .st-key-home-questions）。
    """
    with st.container(key="home-questions", border=True):
        _four_questions(records, stats)


def _four_questions(records: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    total = int(stats.get("total") or 0)

    # 原来的「① 有什么·库存」整段删掉了：它把公告总量/覆盖省份/政策类/民生类
    # 又报了一遍，还把地区分布和分类分布重新画成条形图和表格 —— 而这四个数字
    # 和这两张图在上面的 iframe 里已经各出现过一次，同一页三份。
    # 库存类信息留在 iframe 顶部的 KPI 条，分布图留在模块卡片里，各一处。
    # 这里只回答顶部回答不了的问题：数据新不新、干不干净、采集在不在跑。

    # ── ① 新不新 ──
    _section("① 新不新 · 时效")
    fresh = _freshness()
    hours = fresh.get("hours_since_ingest")
    if hours is None:
        headline = "未知"
    elif hours < 24:
        headline = f"{hours:.1f} 小时前"
    else:
        headline = f"{hours / 24:.1f} 天前"
    days_success = fresh.get("days_since_success")
    st.markdown(
        _metric_row([
            (html.escape(headline), "最新入库时间（距今）"),
            ("—" if days_success is None else f"{days_success:.2f}", "距最近成功采集（天）"),
            (f"{int(fresh.get('recent_count') or 0):,}", f"最近 {fresh.get('recent_days')} 天新增"),
            (html.escape(str(fresh.get("latest_publish_date") or "—")), "最新发布日期"),
        ]),
        unsafe_allow_html=True,
    )
    if fresh.get("stale"):
        st.warning(f"数据已陈旧：超过 {fresh.get('stale_hours')} 小时没有新的成功采集。")
    now = datetime.now(timezone.utc)
    buckets = {(now - timedelta(days=offset)).date().isoformat(): 0 for offset in range(6, -1, -1)}
    for record in records:
        raw = record.get("last_seen_at") or record.get("fetched_at")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        key = stamp.date().isoformat()
        if key in buckets:
            buckets[key] += 1
    st.caption(f"最近 7 天入库趋势（窗口内 {total:,} 条）")
    st.bar_chart(buckets, height=240)

    # ── ③ 干不干净 ──
    _section("② 干不干净 · 质量")
    health = _health_summary()
    flags = _flag_overview()
    counts = flags["counts"]
    checked = counts.get("total") or 0
    year_rows = stats.get("year_data") or []
    year_top, year_top_n = ("—", 0)
    if year_rows:
        year_top, year_top_n = max(((item["year"], item["count"]) for item in year_rows), key=lambda pair: pair[1])
    st.markdown(
        _metric_row([
            (f"{health['ok']}/{health['total']}", "体检达标项"),
            (str(health["total"] - health["ok"]), "未达标项"),
            (f"{counts.get('clean', 0):,}", "无任何质量标记"),
            (f"{counts.get('uncategorized', 0):,}", "未分类记录"),
        ]),
        unsafe_allow_html=True,
    )
    issues = [
        {"问题": flags["labels"][flag], "条数": counts.get(flag, 0),
         "占比": f"{counts.get(flag, 0) / checked:.1%}" if checked else "—"}
        for flag in flags["order"]
    ]
    issues.append({"问题": f"数据集中在 {year_top} 一年", "条数": year_top_n,
                   "占比": f"{year_top_n / total:.1%}" if total else "—"})
    st.dataframe(issues, width="stretch", hide_index=True, height=280)

    # ── ④ 在做什么 ──
    _section("③ 在做什么 · 采集轮次")
    live, state = _progress()
    if live is None:
        st.info("采集器没在跑。到「采集运维」页可以启动，也可以跑 "
                "python lighthouse/status.py。")
    elif live.get("stale"):
        st.warning(f"进度日志已停更 {live['age_seconds'] / 60:.0f} 分钟，"
                   f"最后一条进度不是现在的状态。")
    else:
        done, total_srcs = int(live["done"]), int(live["total"])
        pct = (done / total_srcs * 100.0) if total_srcs else 0.0
        rounds = ""
        if state:
            rounds = (f"第 {state.get('rounds_done', 0)}/{state.get('rounds_planned', 0)} 轮"
                      f" · 翻页深度 {state.get('depth')} 页")
        st.markdown(
            _metric_row([
                (f"{done}/{total_srcs}", f"本轮源进度 {pct:.0f}%"),
                (f"{int(live['records']):,}", "本轮采到"),
                (f"{int(live['dropped']):,}", "本轮丢弃"),
                (f"{live['elapsed_minutes']:.0f}m / {live['left_minutes']:.0f}m", "已用 / 剩余"),
            ]),
            unsafe_allow_html=True,
        )
        st.progress(min(1.0, pct / 100.0), text=f"本轮 {done}/{total_srcs} 个源")
        running = live.get("running") or []
        st.caption(f"正在采集：{'、'.join(running)}　（{rounds}）" if running and rounds
                   else ("正在采集：" + "、".join(running) if running else rounds))



def render_home(records: list[dict[str, Any]], sources: list[dict[str, Any]], quality: dict[str, Any], graph: dict[str, Any], theme_id: str = "cyber_neon") -> None:
    """渲染科技感首页：三维概览 + 四问式信息架构。"""
    stats = _compute_home_stats(records, sources, quality, graph)

    # ── 体检未达标告警条 ──
    # 4 个 FAIL 不该藏在采集运维页的 Tab 里，首页顶部直接红条亮出来。
    fails = health_fails()
    if fails:
        fail_names = "、".join(
            f"{METRIC_LABEL_FAIL.get(f.get('metric', ''), f.get('metric', ''))}（{f.get('value', '?')}）"
            for f in fails
        )
        st.error(
            f"⚠️ 数据体检 {len(fails)} 项未达标：{fail_names}。"
            f"详见「采集运维 → 数据体检」Tab。"
        )

    # 确保中国地图 GeoJSON 可被 iframe fetch
    geo_base = ""
    if _publish_geojson is not None:
        try:
            geo_base = _publish_geojson()
        except Exception:
            geo_base = ""
    html_content = _home_html(stats, theme_id, geo_base)
    # hud 内容约 760px，iframe 高度 820 留出余量，避免图表被截断。
    # 注意：st.iframe 在 1.64.0 对 srcdoc 内 JS 渲染存在差异，
    # components.html 虽有 deprecation warning 但行为正确，暂保留。
    import streamlit.components.v1 as components
    components.html(html_content, height=820, scrolling=True)
    _render_four_questions(records, stats)
