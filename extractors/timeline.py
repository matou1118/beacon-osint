"""Freshness tier and time-line assembly.

Two questions the corpus has to answer, and they need different handling:
"what is new" (the last 12 months) and "how did this evolve" (the full 1-10 year
history). Splitting them at the record level is what lets the product show a
current feed and a timeline from the same store, without re-crawling.

Tiers follow AGENTS.md 1.9:
    current   publish_date within 365 days
    recent    365-1080 days
    archive   older than 3 years, or undated
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Mapping

CURRENT_DAYS = 365
RECENT_DAYS = 1080  # three years

TIER_CURRENT = "current"
TIER_RECENT = "recent"
TIER_ARCHIVE = "archive"

TIER_LABELS = {
    TIER_CURRENT: "新信息",
    TIER_RECENT: "近期历史",
    TIER_ARCHIVE: "历史档案",
}


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text[: len(fmt) + 6].rstrip("Z"), fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def freshness_tier(value: Any, today: date | None = None) -> str:
    published = parse_date(value)
    if published is None:
        return TIER_ARCHIVE
    reference = today or date.today()
    age = (reference - published).days
    if age <= CURRENT_DAYS:
        return TIER_CURRENT
    if age <= RECENT_DAYS:
        return TIER_RECENT
    return TIER_ARCHIVE


def tier_summary(records: Iterable[Mapping[str, Any]], today: date | None = None) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    years: dict[str, set[str]] = defaultdict(set)
    for record in records:
        tier = freshness_tier(record.get("publish_date"), today)
        counts[tier] += 1
        published = parse_date(record.get("publish_date"))
        if published:
            years[tier].add(str(published.year))
    total = max(1, sum(counts.values()))
    return {
        "total": sum(counts.values()),
        "counts": dict(counts),
        "rates": {tier: round(count / total, 4) for tier, count in counts.items()},
        "years": {tier: sorted(v) for tier, v in years.items()},
        "labels": TIER_LABELS,
    }


def build_timeline(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One chronological line per record, oldest first.

    Sorted by publish_date so a reader sees how a program evolved; undated records
    go last rather than being dropped, because the link is still useful.
    """
    items = []
    for record in records:
        published = parse_date(record.get("publish_date"))
        items.append({
            "publish_date": published.isoformat() if published else None,
            # Undated records sort last, not first: a reader scanning a timeline
            # needs the known order first and the unknown tail after it.
            "sort_key": published.toordinal() if published else 10**9,
            "tier": freshness_tier(record.get("publish_date")),
            "title": record.get("title"),
            "gov_level": record.get("gov_level"),
            "region_province": record.get("region_province"),
            "issuing_body": record.get("issuing_body"),
            "category_l1": record.get("category_l1"),
            "category_l2": record.get("category_l2"),
            "url": record.get("source_url") or record.get("url"),
            "summary": record.get("summary"),
        })
    items.sort(key=lambda r: (r["sort_key"], str(r["title"] or "")))
    return items


def timeline_by_program(
    records: Iterable[Mapping[str, Any]], extract=None
) -> dict[str, list[dict[str, Any]]]:
    """Group a timeline per policy program, using the supplied extractor."""
    from .program_extract import extract_program_candidates

    pick = extract or extract_program_candidates
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        candidates = pick(record)
        name = max(candidates, key=len) if candidates else None
        if name:
            groups[name].append(record)
    return {name: build_timeline(items) for name, items in groups.items() if len(items) > 1}


def coverage_gaps(records: Iterable[Mapping[str, Any]], span_years: int = 10) -> dict[str, Any]:
    """Where a topic is thin, so collection effort can be aimed instead of spread."""
    by_year: dict[str, int] = defaultdict(int)
    for record in records:
        published = parse_date(record.get("publish_date"))
        if published:
            by_year[str(published.year)] += 1
    current_year = date.today().year
    years = [str(y) for y in range(current_year - span_years + 1, current_year + 1)]
    missing = [y for y in years if int(by_year.get(y, 0)) == 0]
    return {
        "span_years": span_years,
        "years_present": {y: by_year.get(y, 0) for y in years},
        "missing_years": missing,
        "coverage_rate": round(1 - len(missing) / len(years), 4),
    }
