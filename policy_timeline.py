"""Time-line and freshness report over the collected corpus.

Answers the product questions directly: what is new, what is history, how did a
policy program evolve across levels, and where is the record still thin.

Usage:
    python -m policy_timeline                      # overview
    python -m policy_timeline --program 农村人居环境整治行动方案
    python -m policy_timeline --gaps
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.timeline import (  # noqa: E402
    TIER_CURRENT,
    build_timeline,
    coverage_gaps,
    freshness_tier,
    parse_date,
    tier_summary,
)
from extractors.program_extract import group_by_program  # noqa: E402

DEFAULT_DB = ROOT / "data" / "beacon.db"
CATEGORIES = ROOT / "config" / "categories.yaml"


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for row in con.execute(
        "SELECT id, source_id, source_url, title, publish_date, content, category_l1, category_l2, "
        "gov_level, region_province, region_city, region_county, extra FROM announcements"
    ):
        record = dict(row)
        try:
            extra = json.loads(record.pop("extra") or "{}") or {}
        except (TypeError, ValueError):
            extra = {}
        record["summary"] = extra.get("summary")
        record["issuing_body"] = extra.get("issuing_body")
        rows.append(record)
    con.close()
    return rows


def program_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A line per policy program, ordered by how comparable it is."""
    groups = group_by_program(rows)
    out: list[dict[str, Any]] = []
    for name, items in groups.items():
        years = {str(parse_date(i.get("publish_date")))[:4] for i in items if parse_date(i.get("publish_date"))}
        levels = {str(i.get("gov_level") or "") for i in items if i.get("gov_level")}
        provinces = {str(i.get("region_province")) for i in items if i.get("region_province")}
        out.append({
            "program": name,
            "documents": len(items),
            "years": sorted(y for y in years if y and y != "None"),
            "levels": sorted(levels),
            "provinces": sorted(p for p in provinces if p and p != "None"),
            "cross_year": len([y for y in years if y and y != "None"]) >= 2,
            "cross_level": len(levels) >= 2,
            "is_program": len([y for y in years if y and y != "None"]) >= 2 and len(levels) >= 2,
            "timeline": build_timeline(items) if len(levels) >= 2 or len(items) > 1 else [],
        })
    out.sort(key=lambda r: (-r["is_program"], -r["documents"]))
    return out


def overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    freshness = tier_summary(rows)
    gaps = coverage_gaps(rows)
    lines = program_lines(rows)
    return {
        "records": len(rows),
        "freshness": freshness,
        "year_coverage": gaps,
        "programs": {
            "candidates": len(lines),
            "is_program": sum(1 for line in lines if line["is_program"]),
            "cross_year_only": sum(1 for line in lines if line["cross_year"] and not line["cross_level"]),
            "cross_level_only": sum(1 for line in lines if line["cross_level"] and not line["cross_year"]),
        },
        "levels": dict(Counter(str(r.get("gov_level") or "?") for r in rows)),
        "categories": dict(Counter(str(r.get("category_l1") or "?") for r in rows)),
    }


def render_overview(data: dict[str, Any]) -> str:
    fresh = data["freshness"]
    lines = ["", f"政策信息时间线总览（{data['records']} 条）", "=" * 64]
    lines.append("【数据新鲜度】")
    for tier, label in fresh["labels"].items():
        count = fresh["counts"].get(tier, 0)
        years = fresh["years"].get(tier) or []
        span = f"{years[0]}-{years[-1]}" if years else "-"
        lines.append(f"  {label:<8s} {count:>4d} 条 ({fresh['rates'].get(tier, 0)*100:>4.1f}%)  年份 {span}")
    lines.append("")
    lines.append("【时间线完整度】近 10 年")
    years = data["year_coverage"]["years_present"]
    for year, count in years.items():
        bar = "█" * min(30, count * 30 // max(1, max(years.values())))
        lines.append(f"  {year}  {count:>4d} {bar}")
    lines.append(f"  覆盖��� {data['year_coverage']['coverage_rate']*100:.0f}%"
                 f"  缺失年份: {', '.join(data['year_coverage']['missing_years']) or '无'}")
    lines.append("")
    prog = data["programs"]
    lines.append("【专项识别】")
    lines.append(f"  候选专名 {prog['candidates']} | 满足专项定义 {prog['is_program']}"
                 f" | 跨年未跨层 {prog['cross_year_only']} | 跨层未跨年 {prog['cross_level_only']}")
    lines.append("")
    lines.append("【层级分布】" + "  ".join(f"{k}:{v}" for k, v in data["levels"].items()))
    lines.append("")
    return "\n".join(lines)


def render_program(line: dict[str, Any]) -> str:
    out = ["", f"专项：{line['program']}", "=" * 64]
    out.append(f"文件 {line['documents']} 篇 | 年份 {', '.join(line['years']) or '-'}"
               f" | 层级 {', '.join(line['levels'])}")
    if line["provinces"]:
        out.append(f"涉及地区: {', '.join(line['provinces'])}")
    out.append(f"满足专项定义(跨年+跨层): {'是' if line['is_program'] else '否'}")
    out.append("")
    out.append("时间线（最早 → 最新）:")
    for item in line["timeline"]:
        where = "/".join(str(v) for v in (item["region_province"], item["region_city"], item["region_county"]) if v) or "全国"
        out.append(f"  {item['publish_date'] or '未标注日期'} [{item['gov_level'] or '-'}] {where}")
        out.append(f"      {str(item['title'])[:60]}")
        if item["summary"]:
            out.append(f"      摘要: {str(item['summary'])[:130]}")
        out.append(f"      链接: {item['url']}")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--program", help="show one program timeline")
    parser.add_argument("--gaps", action="store_true", help="only show coverage gaps")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1
    rows = load_rows(args.db)

    if args.program:
        for line in program_lines(rows):
            if args.program in line["program"]:
                print(json.dumps(line, ensure_ascii=False, indent=2) if args.json else render_program(line))
                return 0
        print(f"no program matching: {args.program}")
        print("candidates: " + "、".join(line["program"] for line in program_lines(rows)[:12]))
        return 1

    if args.gaps:
        data = coverage_gaps(rows)
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json
              else "\n".join([
                  "", f"近 {data['span_years']} 年覆盖", "=" * 52,
                  *[f"  {y}  {c:>4d}" for y, c in data["years_present"].items()],
                  f"  覆盖率 {data['coverage_rate']*100:.0f}%  缺失: {', '.join(data['missing_years']) or '无'}",
              ]))
        return 0

    data = overview(rows)
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else render_overview(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
