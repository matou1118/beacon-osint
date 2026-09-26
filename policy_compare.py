"""Cross-level policy comparison.

Answers the question the flat table cannot: for one policy theme, what did the
national level set, how did each province implement it, and where do cities and
counties add their own terms?

Everything here is deterministic. Themes come from config/policy_themes.yaml by
keyword; no model inference, per the project constitution.

Usage:
    python -m policy_compare --theme 招商引资
    python -m policy_compare --all --limit 20
    python -m policy_compare --coverage
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
THEMES_PATH = ROOT / "config" / "policy_themes.yaml"
DEFAULT_DB = ROOT / "data" / "beacon.db"

LEVEL_ORDER = ["国家部委", "国家级", "省级", "市级", "县级", "区县级"]
LEVEL_LABELS = {
    "国家部委": "国家部委",
    "国家级": "国家级",
    "省级": "省级",
    "市级": "市级",
    "县级": "县级",
    "区县级": "县级",
}

AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|亿元|万|亿|元)")
AMOUNT_UNIT = {"万元": 10_000, "亿元": 100_000_000, "万": 10_000, "亿": 100_000_000, "元": 1}
# Money only counts when the number is a plausible subsidy, not a phone number,
# a document serial, or a year. A 2.7-million-yuan cultural-tourism subsidy does
# not exist; a phone number in the same shape does.
AMOUNT_MIN = 1_000
AMOUNT_MAX = 1_000_000_000_000
AMOUNT_CONTEXT = re.compile(r"(?:补贴|资助|奖励|资金|补助|扶持|奖补|贴补|专项|预算|投入|额度|上限|不低于|不超过|最高)")
AMOUNT_EXCLUDE = re.compile(r"(?:电话|传真|邮编|证号|编号|文号|号$|代码|邮编)")


def load_themes(path: Path = THEMES_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def tag_theme(record: dict[str, Any], config: dict[str, Any]) -> tuple[str, int]:
    """Tag by theme keyword hits.

    Title is weighted far above the body: a body mentions "文化" or "产业" in
    boilerplate often enough to swamp the real signal, and mis-tagging breaks the
    cross-level grouping this module exists to produce.
    """
    title = normalize(record.get("title"))
    body = normalize(record.get("content"))[:400]
    best_theme = str((config.get("defaults") or {}).get("policy_theme") or "其他主题")
    best_score = 0
    for theme, spec in (config.get("themes") or {}).items():
        score = 0
        for keyword in spec.get("keywords") or []:
            token = normalize(keyword)
            if not token:
                continue
            if token in title:
                score += 3
            elif token in body:
                score += 1
        if score > best_score:
            best_theme, best_score = str(theme), score
    return best_theme, best_score


def parse_amount(*values: Any) -> int | None:
    """Return a funding amount in yuan, or None.

    Only accepts a number that sits next to funding vocabulary and looks like a
    real subsidy. Phone numbers, document serials and plain years are rejected so
    a bogus amount never becomes the basis of a cross-jurisdiction comparison.
    """
    for value in values:
        if not value:
            continue
        text = str(value)
        for match in AMOUNT_RE.finditer(text):
            try:
                number = int(float(match.group(1)) * AMOUNT_UNIT.get(match.group(2), 1))
            except ValueError:
                continue
            if not AMOUNT_MIN <= number <= AMOUNT_MAX:
                continue
            window = text[max(0, match.start() - 18):match.end() + 12]
            if AMOUNT_EXCLUDE.search(window):
                continue
            if AMOUNT_CONTEXT.search(text):
                return number
    return None


def fetch(db_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(row) for row in con.execute(
        "SELECT id, source_id, title, publish_date, content, category_l1, category_l2, "
        "gov_level, region_province, region_city, region_county, amount, "
        "deadline, target_industry, source_url, extra FROM announcements"
    )]
    con.close()
    for record in rows:
        extra = record.pop("extra") or None
        try:
            parsed = json.loads(extra) if extra else {}
        except (TypeError, ValueError):
            parsed = {}
        record["issuing_body"] = parsed.get("issuing_body") or record.get("source_id")
    return rows


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_level = Counter(str(r.get("gov_level") or "未标注") for r in rows)
    provinces = sorted({str(r["region_province"]) for r in rows if r.get("region_province")})
    cities = sorted({str(r["region_city"]) for r in rows if r.get("region_city")})
    counties = sorted({str(r["region_county"]) for r in rows if r.get("region_county")})
    return {
        "total": len(rows),
        "by_level": dict(by_level),
        "provinces": provinces,
        "province_count": len(provinces),
        "cities": cities,
        "city_count": len(cities),
        "counties": counties,
        "county_count": len(counties),
    }


def build_chain(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    chains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in rows:
        theme, hits = tag_theme(record, config)
        record["_theme"] = theme
        record["_hits"] = hits
        record["_amount_value"] = parse_amount(record.get("amount"), record.get("title"), record.get("content"))
        chains[theme].append(record)
    for records in chains.values():
        records.sort(key=lambda r: (
            LEVEL_ORDER.index(str(r.get("gov_level"))) if str(r.get("gov_level")) in LEVEL_ORDER else 99,
            str(r.get("publish_date") or ""),
        ), reverse=False)
    return chains


def compare(theme: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_level[str(record.get("gov_level") or "未标注")].append(record)
    chain = []
    for level in LEVEL_ORDER:
        for record in by_level.get(level, []):
            chain.append({
                "level": LEVEL_LABELS.get(level, level),
                "province": record.get("region_province"),
                "city": record.get("region_city"),
                "county": record.get("region_county"),
                "title": record.get("title"),
                "publish_date": record.get("publish_date"),
                "category_l1": record.get("category_l1"),
                "issuing_body": record.get("issuing_body"),
                "amount": record.get("_amount_value"),
                "deadline": record.get("deadline"),
                "url": record.get("source_url"),
            })
    amounts = [c["amount"] for c in chain if c.get("amount")]
    regions = {c["province"] or c["city"] or c["county"] for c in chain if c.get("province") or c.get("city") or c.get("county")}
    return {
        "theme": theme,
        "document_count": len(records),
        "levels_present": [lvl for lvl in LEVEL_ORDER if by_level.get(lvl)],
        "levels_missing": [lvl for lvl in LEVEL_ORDER if not by_level.get(lvl) and lvl in ("省级", "市级", "县级")],
        "regions": sorted(regions),
        "amounts_found": len(amounts),
        "amount_max": max(amounts) if amounts else None,
        "amount_min": min(amounts) if amounts else None,
        "comparable": len({lvl for lvl in by_level}) >= 2 and len(amounts) >= 1,
        "chain": chain,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--theme")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"database not found: {args.db}")
        return 1
    config = load_themes()
    rows = fetch(args.db)
    chains = build_chain(rows, config)

    if args.coverage or not (args.theme or args.all):
        report = coverage(rows)
        report["themes"] = {k: len(v) for k, v in sorted(chains.items(), key=lambda kv: -len(kv[1]))}
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _format_coverage(report))
        return 0

    if args.theme:
        matches = {k: v for k, v in chains.items() if args.theme in k}
        if not matches:
            print(f"unknown theme: {args.theme}")
            print("available: " + "、".join(sorted(chains)))
            return 1
        result = compare(args.theme, matches[args.theme])
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else _format_compare(result))
        return 0

    results = [compare(theme, records) for theme, records in chains.items() if records]
    results.sort(key=lambda r: -r["document_count"])
    payload = {"total": len(rows), "themes": results[: args.limit]}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else _format_all(payload))
    return 0


def _format_coverage(report: dict[str, Any]) -> str:
    lines = ["", f"数据覆盖（共 {report['total']} 条）", "=" * 52]
    for level, count in sorted(report["by_level"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {level:<10s} {count:>4d}")
    lines += [
        "",
        f"  省级覆盖   {report['province_count']} 个省：{'、'.join(report['provinces']) or '无'}",
        f"  市级覆盖   {report['city_count']} 个市：{'、'.join(report['cities']) or '无'}",
        f"  县级覆盖   {report['county_count']} 个县：{'、'.join(report['counties']) or '无'}",
        "",
        "主题分布（记录数）：",
    ]
    for theme, count in list(report["themes"].items())[:20]:
        lines.append(f"  {theme:<12s} {count:>4d}")
    lines.append("")
    return "\n".join(lines)


def _format_compare(result: dict[str, Any]) -> str:
    lines = ["", f"主题：{result['theme']}（{result['document_count']} 条）", "=" * 52]
    if result["levels_missing"]:
        lines.append(f"⚠ 缺层级：{'、'.join(result['levels_missing'])} —— 跨层级对比不完整")
    if result["regions"]:
        lines.append(f"  涉及地区：{'、'.join(result['regions'])}")
    if result["amounts_found"]:
        lines.append(f"  金额可比：{result['amounts_found']} 条，区间 {result['amount_min']} ~ {result['amount_max']} 元")
    else:
        lines.append("  金额可比：0 条 —— 无法做力度差异对比（amount 抽取不足）")
    lines += ["", "政策链（国家、省、市、县）："]
    for item in result["chain"]:
        where = "／".join(str(v) for v in (item["province"], item["city"], item["county"]) if v) or "全国"
        amount = f" 金额={item['amount']}" if item.get("amount") else ""
        lines.append(f"  [{item['level']}] {where}｜{str(item['title'])[:40]}{amount}")
    lines.append("")
    return "\n".join(lines)


def _format_all(payload: dict[str, Any]) -> str:
    lines = ["", f"主题对比总览（{payload['total']} 条）", "=" * 52]
    lines.append(f"{'主题':<12s} {'条数':>4s} {'层级':<18s} {'可比':>4s}")
    for item in payload["themes"]:
        levels = "/".join(x[:2] for x in item["levels_present"]) or "-"
        mark = "是" if item["comparable"] else "否"
        lines.append(f"{item['theme']:<12s} {item['document_count']:>4d} {levels:<18s} {mark:>4s}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
