"""省份 × 行业交叉视图：横向对比各省的政策结构。

为什么需要它
------------
``category_l1`` 混编了两种语义——**体裁**（公示公告/政策法规/政策发布…占64%）
和**主题**（行业政策/商业机会/人才政策…占34%）。后果很具体：「公示公告」里
科技政策的公示和农业补贴的公示混在一起，于是无法回答
「各省的科技创新政策趋势」——因为分类那一步就把行业抹掉了。

所以横向对比必须**绕开 category_l1**，改用两个正交且干净的字段：
``region_province``（省份，覆盖 87.3%）和 ``category_l2``（二级分类）。
两个字段合起来就是「省份 × 行业」。

一个必须先做的分离
------------------
``category_l2`` 本身也混着两种东西：**行业门类**（农业农村/科技创新/工业制造…）
和**体裁词**（公示/名单/办法/通知…）。本脚本按其父分类把它们分开——
依据是 ``config/category_rules.yaml`` 的结构，而不是靠词表猜：
``行业政策``/``行业补贴`` 下的是行业，其余是体裁。

2026-10-05 实测：不做这个分离的话，「公示」会以行业身份混进每个省，
横表就成了「各省发了多少公示」——那不是政策研究方向，是废数据。

用法
----
    python scripts/province_industry_matrix.py             # 交叉表 + 结论
    python scripts/province_industry_matrix.py --json      # 机器可读
    python scripts/province_industry_matrix.py --industry 科技创新  # 单行业跨省对比
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "beacon.db"
RULES = ROOT / "config" / "category_rules.yaml"
OUT = ROOT / "data" / "runtime" / "lighthouse" / "province_industry.json"

# category_l1 里属于「主题」而非「体裁」的：它们下的二级才是行业门类
INDUSTRY_PARENTS = ("行业政策", "行业补贴")


def _classify_l2() -> tuple[set[str], set[str]]:
    """按 category_rules.yaml 的父子结构，把二级分类分成行业类/体裁类。

    刻意不用词表判断——2026-10-05 实测「商务贸易」不在主分类而只在
    ``fallback_categories`` 里，靠手写白名单会漏掉一半（漏掉 380+281 条）。
    从配置里读父子关系才不会漏。
    """
    import yaml
    data = yaml.safe_load(RULES.read_text(encoding="utf-8")) or {}
    industry: set[str] = set()
    genre: set[str] = set()
    for table in (data.get("categories") or {},
                  data.get("fallback_categories") or {}):
        for parent, spec in table.items():
            if not isinstance(spec, dict):
                continue
            target = industry if parent in INDUSTRY_PARENTS else genre
            for sub in (spec.get("subcategories") or {}):
                target.add(str(sub))
    return industry, genre


def load_matrix() -> dict:
    industry, genre = _classify_l2()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT coalesce(region_province,'') p, category_l2, "
            "count(*) c, "
            "sum(CASE WHEN publish_date >= date('now','-1 year') THEN 1 ELSE 0 END) y1 "
            "FROM announcements "
            "WHERE region_province IS NOT NULL AND trim(region_province) <> '' "
            "AND category_l2 IN (%s) "
            "GROUP BY p, category_l2" % ",".join("?" * len(industry)),
            sorted(industry)).fetchall()
    finally:
        con.close()

    matrix: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    industry_total: collections.Counter = collections.Counter()
    recent: collections.Counter = collections.Counter()
    province_total: collections.Counter = collections.Counter()
    for p, l2, c, y1 in rows:
        p = str(p)
        if not p:
            continue
        matrix[p][str(l2)] = {"count": int(c), "recent": int(y1 or 0)}
        industry_total[str(l2)] += int(c)
        recent[str(l2)] += int(y1 or 0)
        province_total[p] += int(c)

    return {
        "matrix": {p: dict(v) for p, v in matrix.items()},
        "industry_total": dict(industry_total),
        "industry_recent": dict(recent),
        "province_total": dict(province_total),
        "industry_l2_count": len(industry),
        "genre_l2_count": len(genre),
    }


def analyze(data: dict) -> dict:
    matrix = data["matrix"]
    # load_matrix 里这三个是 dict（为了能 json 序列化），这里用回Counter。
    industries = collections.Counter(data["industry_total"])
    recent = collections.Counter(data["industry_recent"])
    findings: list[dict] = []

    # 1. 结构性差异：各省的「产业结构」对比
    # 用每个省在若干大行业上的**占比**（而不是绝对条数）——否则四川和
    # 北京比永远是四川多，看不出「这省重点发展什么」。
    focus = [i for i, _ in industries.most_common(8)]
    profiles: dict[str, dict[str, float]] = {}
    for p, cells in matrix.items():
        total = data["province_total"].get(p) or 1
        profiles[p] = {i: cells.get(i, {}).get("count", 0) / total for i in focus}

    # 特征行业：某省占比明显高于全国平均
    grand = max(sum(industries.values()), 1)
    national = {i: industries.get(i, 0) / grand for i in focus}
    distinctive: dict[str, list[tuple[str, float]]] = {}
    for p, prof in profiles.items():
        marks = [(i, prof[i] - national[i]) for i in focus
                 if prof[i] - national[i] > 0.01]   # 高于全国均值 1 个百分点
        if marks:
            distinctive[p] = sorted(marks, key=lambda x: -x[1])[:4]

    # 2. 新旧动能：近 1 年占比高的 = 正在加码的
    momentum = []
    for i, c in industries.items():
        y1 = recent.get(i, 0)
        if c >= 30:
            momentum.append({"industry": i, "total": c, "recent": y1,
                             "recent_share": y1 / c})

    # 3. 覆盖不足的行业：有一些记录但省份数很少 = 采集有盲区
    province_count = collections.Counter()
    for p, cells in matrix.items():
        for i in cells:
            province_count[i] += 1
    thin = [(i, n, industries[i]) for i, n in province_count.items()
            if n <= 3 and industries[i] >= 10]

    return {
        "focus_industries": focus,
        "provinces": len(matrix),
        "records": sum(industries.values()),
        "distinctive_by_province": distinctive,
        "momentum": sorted(momentum, key=lambda x: -x["recent_share"]),
        "thin_coverage": sorted(thin, key=lambda x: -x[2]),
    }


def render(data: dict, a: dict) -> str:
    L: list[str] = []
    L.append("省份 × 行业 政策结构分析")
    L.append("=" * 74)
    L.append("基数: %d 条政策、%d 个省 × %d 个行业门类"
             % (a["records"], a["provinces"], len(data["industry_total"])))
    L.append("（已剔除体裁词：公示/名单/办法/通知等 %d 类，"
             "它们描述文件形态不是行业）" % data["genre_l2_count"])
    L.append("")

    focus = a["focus_industries"]
    top_prov = sorted(a["distinctive_by_province"],
                      key=lambda p: -data["province_total"][p])[:10]
    L.append("【一】各省的产业侧重（该省占比明显高于全国均值的行业）")
    L.append("-" * 74)
    L.append("%-10s %5s  %s" % ("省份", "条数", "特色行业（占比高于全国均值）"))
    for p in top_prov:
        marks = a["distinctive_by_province"][p]
        L.append("%-10s %5d  %s" % (
            p, data["province_total"][p],
            "  ".join("%s +%.0f%%" % (i, v * 100) for i, v in marks)))
    L.append("")

    L.append("【二】哪些行业正在加码（近1年占该行业总量比）")
    L.append("-" * 74)
    L.append("%-12s %6s %6s %8s" % ("行业", "总量", "近1年", "近1年占比"))
    for m in a["momentum"][:10]:
        L.append("%-12s %6d %6d %7.0f%%"
                 % (m["industry"], m["total"], m["recent"],
                    m["recent_share"] * 100))
    L.append("")

    if a["thin_coverage"]:
        L.append("【三】采集盲区：有数据但只覆盖 <=3 个省的行业")
        L.append("-" * 74)
        for i, n, c in a["thin_coverage"][:10]:
            L.append("  %-12s %4d 条但只覆盖 %d 个省 —— 该行业大部分省份没采到"
                     % (i, c, n))
        L.append("")
    L.append("完整数据: %s" % OUT)
    return "\n".join(L)


def industry_detail(data: dict, industry: str) -> str:
    matrix = data["matrix"]
    rows = []
    for p, cells in matrix.items():
        c = cells.get(industry)
        if c:
            rows.append((c["count"], c["recent"], p))
    rows.sort(reverse=True)
    lines = ["「%s」跨省对比（共 %d 个省有数据）" % (industry, len(rows)),
             "=" * 62,
             "%-12s %6s %6s %8s" % ("省份", "总量", "近1年", "活跃度")]
    for c, y1, p in rows:
        lines.append("%-12s %6d %6d %7.0f%%" % (p, c, y1, 100 * y1 / max(c, 1)))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="省份 × 行业交叉视图")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--industry", help="单行业跨省对比")
    args = ap.parse_args()

    data = load_matrix()
    if args.industry:
        print(industry_detail(data, args.industry))
        return 0
    a = analyze(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"analysis": a, **{k: data[k] for k in
                        ("industry_total", "industry_recent", "province_total",
                         "industry_l2_count", "genre_l2_count")}},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print(render(data, a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())