"""采集覆盖缺口分析：回答「该采的没采到的是什么」。

借鉴 Easel 的 ``skill-content-gap-analysis``（分析赛道内容空白、找高需求
低竞争的蓝海机会）。那个 skill 面向社媒选题：我们只采**已经存在**的东西，
从没人问**该采而没采**的是什么——这个盲区在 2026-10-05 造成实打实的浪费：
扩源时试了 13 个候选门户的首页/栏目页，花了大量时间，才在最后发现
「112 个省级源，市级只有 4 个」这个早就该先看的缺口。

所以这里做同一件事，只是对象从「内容赛道」换成「信源覆盖」。

五个维度（全部可从现有数据推导，无���询）
----------------------------------------
1. 层级缺口  national / provincial / province / municipal / county 的分布
2. 省份失衡  各省源数对比（实测四川 42个 vs 大部分省 5-6 个）
3. 部委缺口  实际有多少个国务院组成部门，我们接了几个
4. 主题缺口  行业领域二级分类里哪些长期零产出
5. 源健康    配置了但从没产出 / 产出极稀薄的源

输出按「可补性 x 缺口大小」排序，每条都带**证据**（具体数字），
不用「建议加强某方面」这种没法执行的结论。
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "beacon.db"
SOURCES = ROOT / "config" / "sources.yaml"
REPORT = ROOT / "data" / "runtime" / "lighthouse" / "coverage_gaps.json"

# 国务院组成机构里最常见的政策发布主体（用于算部委覆盖率）。
# 只列**确实会发政策文件**的部门，不列纯协调机构。
# 依据：国务院机构改革后的常设组成部门，以及常见的政策发布口径。
MINISTRIES = [
    "发改委", "财政部", "工信部", "科技部", "教育部", "人社部", "住建部",
    "交通运输部", "水利部", "农业农村部", "商务部", "卫健委", "民政部",
    "司法部", "公安部", "国防部", "自然资源部", "生态环境部", "应急管理部",
    "国资委", "市场监管总局", "药监局", "医保局", "统计局", "知识产权局",
    "能源局", "林草局", "气象局", "海关总署", "税务总局", "金融监管总局",
    "证监会", "广电总局", "文旅部", "体育总局", "信访局", "侨办", "台办",
    "侨联", "气象局", "铁路局", "民航局", "邮政局", "烟草专卖",
]

# 各省的简称/前缀，用于把源的 id 前缀映射到省。
PROVINCE_HINTS = {
    "bj": "北京", "tj": "天津", "sh": "上海", "cq": "重庆", "hb": "湖北",
    "hn": "湖南", "sd": "山东", "sc": "四川", "gz": "贵州", "yn": "云南",
    "js": "江苏", "zj": "浙江", "ah": "安徽", "fj": "福建", "jx": "江西",
    "ha": "河南", "sn": "陕西", "gx": "广西", "nm": "内蒙古", "xj": "新疆",
    "xz": "西藏", "hl": "黑龙江", "jl": "吉林", "ln": "辽宁", "gs": "甘肃",
    "qh": "青海", "ni": "宁夏", "mo": "澳门", "hk": "香港",
}


def _sources() -> list[dict[str, Any]]:
    import yaml
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8")) or {}
    return [s for s in (data.get("sources") or []) if s.get("enabled")]


def _productivity() -> dict[str, dict[str, Any]]:
    if not DB.exists():
        return {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        out: dict[str, dict[str, Any]] = {}
        for sid, n in con.execute(
                "SELECT source_id, COUNT(*) FROM announcements GROUP BY source_id"):
            out[str(sid)] = {"records": int(n)}
        return out
    finally:
        con.close()


def analyze() -> dict[str, Any]:
    sources = _sources()
    prod = _productivity()
    gaps: list[dict[str, Any]] = []

    # ── 1. 层级缺口 ───────────────────────────────────────────
    # 参照比例：一个成熟的政策库，市级源应该至少和省��同量级——
    # 因为**执行层面**���政策（补贴、招生、招聘）都发在市级。
    by_level = collections.Counter(str(s.get("level") or "?") for s in sources)
    municipal = by_level.get("municipal", 0)
    provincial = by_level.get("province", 0)
    if municipal < provincial // 8:
        gaps.append({
            "dimension": "层级覆盖",
            "severity": "high",
            "finding": "市级源 %d 个 vs 省级 %d 个" % (municipal, provincial),
            "evidence": "按执行层级看，补贴/招生/招聘类政策大量发在市级，"
                        "市级源过少意味着执行层信息大量缺失",
            "action": "优先补省会城市 + 经济强市的政府门户（每个省先补 1-2 个）",
            "gap_size": provincial - municipal * 8,
        })

    # ── 2. 省份失衡 ──────────────────────────────────────────
    per_province: dict[str, int] = collections.Counter()
    for s in sources:
        sid = str(s.get("id") or "")
        m = re.match(r"^([a-z]{2,3})_", sid)
        if not m:
            continue
        prefix = m.group(1)
        name = PROVINCE_HINTS.get(prefix)
        if name:
            per_province[name] += 1
    if per_province:
        top, bottom = (per_province.most_common(1)[0],
                       [p for p in per_province if per_province[p] <= 2])
        if bottom:
            gaps.append({
                "dimension": "省份均衡",
                "severity": "high",
                "finding": "%s 有 %d 个源，而 %d 个省只有 <=2 个"
                           % (top[0], top[1], len(bottom)),
                "evidence": "已建省份: %s；覆盖薄弱: %s"
                            % (", ".join("%s(%d)" % kv for kv in
                                         per_province.most_common(6)),
                               ", ".join(bottom[:12])),
                "action": "按「每省至少 3 个源（政府门户/发改/行业主管）」补齐",
                "gap_size": len(bottom) * 3,
            })

    # ── 3. 部委缺口 ──────────────────────────────────────────
    covered = {str(s.get("name") or "") for s in sources}
    covered_ministries = set()
    for name in covered:
        for m in MINISTRIES:
            if m in name:
                covered_ministries.add(m)
    missing = [m for m in MINISTRIES if m not in covered_ministries]
    if missing:
        gaps.append({
            "dimension": "部委覆盖",
            "severity": "medium",
            "finding": "常设发布主体覆盖 %d/%d，缺 %d 个"
                       % (len(covered_ministries), len(MINISTRIES), len(missing)),
            "evidence": "已覆盖: %s" % ", ".join(sorted(covered_ministries)),
            "action": "优先补「政策发布量最大」的缺失部委",
            "missing": missing,
            "gap_size": len(missing),
        })

    # ── 4. 主题缺口（长期零产出的行业领域）────────────────────
    if DB.exists():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT category_l2, COUNT(*) FROM announcements "
                "WHERE category_l1='行业政策' AND category_l2 IS NOT NULL "
                "AND trim(category_l2) <> '' GROUP BY category_l2").fetchall()
            produced = {r[0]: r[1] for r in rows}
        finally:
            con.close()
        import yaml
        rules = yaml.safe_load(
            (ROOT / "config" / "category_rules.yaml").read_text(encoding="utf-8"))
        subs = ((rules.get("categories") or {}).get("行业政策") or {}).get(
            "subcategories") or {}
        zero = [s for s in subs if not produced.get(s)]
        thin = [(s, produced.get(s, 0)) for s in subs
                if 0 < produced.get(s, 0) < 20]
        if zero:
            gaps.append({
                "dimension": "主题覆盖",
                "severity": "medium",
                "finding": "%d 个行业领域零产出" % len(zero),
                "evidence": "零产出领域: %s；稀薄(<20 条): %s"
                            % (", ".join(zero),
                               ", ".join("%s(%d)" % t for t in thin[:6])),
                "action": "零产出的领域要么补源（该领域没接站点），"
                          "要么该领域在这个信源体系里确实没数据——需人工判一次",
                "gap_size": len(zero),
            })

    # ── 5. 无产出源 ──────────────────────────────────────────
    barren = [str(s.get("id")) for s in sources
              if prod.get(str(s.get("id")), {}).get("records", 0) == 0]
    if barren:
        gaps.append({
            "dimension": "源健康",
            "severity": "low",
            "finding": "%d 个已启用源从未产出任何记录" % len(barren),
            "evidence": ", ".join(barren[:15]),
            "action": "跑 source_tiers 判定：T5 熔断 / T4 降频，"
                      "而不是继续每天回访",
            "gap_size": len(barren),
        })

    gaps.sort(key=lambda g: {"high": 0, "medium": 1, "low": 2}[g["severity"]],
              reverse=False)
    return {
        "analyzed_at": time_str(),
        "sources": len(sources),
        "by_level": dict(by_level),
        "per_province": dict(per_province),
        "ministry_coverage": {"covered": sorted(covered_ministries),
                              "missing": missing},
        "gaps": gaps,
    }


def time_str() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def render(report: dict[str, Any]) -> str:
    lines = ["采集覆盖缺口分析（%d 个已启用源）" % report["sources"], "=" * 66]
    lines.append("层级分布: %s" % ", ".join(
        "%s=%d" % kv for kv in sorted(report["by_level"].items())))
    lines.append("")
    if not report["gaps"]:
        lines.append("未发现显著缺口")
        return "\n".join(lines)
    icon = {"high": "[高]", "medium": "[中]", "low": "[低]"}
    for i, g in enumerate(report["gaps"], 1):
        lines.append("%d. %s %s — %s" % (i, icon[g["severity"]],
                                         g["dimension"], g["finding"]))
        lines.append("   证据: %s" % g["evidence"])
        lines.append("   建议: %s" % g["action"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    report = analyze()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(render(report))
    print()
    print("已写入 %s" % REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())