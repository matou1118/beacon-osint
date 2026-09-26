"""Policy program extraction.

A 专项 is a policy that keeps running, not a one-off notice: the same name shows
up across years and across administrative levels, each level issuing its own
implementation rules. AGENTS.md 4.9 defines the test as two conditions at once:
the same program name across >=2 years AND across >=2 levels.

Names come from the title first, then the opening of the body, because gov
notices put the instrument name in "关于印发《X》的通知" and often repeat it as the
first line of the letter.

Normalisation strips the administrative wrapper so the same program matches across
levels: "关于印发X方案的通知" and "X方案" must collapse to one key.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

# Primary: a book-title-quoted instrument name.
QUOTED_RE = re.compile(r"《([^》]{4,60})》")
# Secondary: "关于印发X的通知" / "转发X实施方案" without brackets. The tail
# alternation is ordered longest-first so "…行动方案" is not cut at "行动" and
# reduced to "…行动".
ISSUING_RE = re.compile(
    r"(?:关于印发|印发|关于发布|发布|转发|印发并|公布)\s*"
    r"([^，。；、]{4,50}?(?:实施方案|行动计划|工作方案|管理办法|实施细则|行动方案|"
    r"试点方案|规划|计划|通知|公告|规定|条例|细则|行动|指南|目录|决定|批复|函))"
    r"\s*(?:的通知|的函|的公告|的批复)?"
)
# Tertiary: a bare instrument name near the start of the body.
LEADING_RE = re.compile(r"^[^，。；]{0,30}?([一-鿿]{4,40}(?:实施方案|行动计划|专项|工程|意见|办法))")
# Page furniture that the leading scan otherwise swallows ("机构…司发布日期…分类").
FURNITURE_RE = re.compile(r"(?:司|部|局|中心|处|室|发布日期|发布机构|分类|来源|字号|浏览|点击)")

PREFIX_RE = re.compile(
    r"^(?:"
    r"中共中央|国务院办公厅|国务院|国家发展改革委|国家发改委|"
    r"[\u4e00-\u9fa5]{2,4}?(?:省|市|自治区|县|区)(?:人民政府)?|"
    r"[\u4e00-\u9fa5]{2,10}?(?:厅|局|委|部|署|办|司|中心|政府)"
    r")?"
    r"(?:关于|印发|转发|发布|公布|中共中央|国务院|各|的)+"
)
SUFFIX_RE = re.compile(
    r"(?:的通知|的函|的公告|的批复|的通报告知|的意见的回复|的意见|的批复|的决定|的通报|的说明"
    r"|的通知的函|的公告的说明)"
    r"(?:[\(（][^)）]*[\)）])?$"
)
LEVEL_NOISE_RE = re.compile(r"[【】\[\]（）()《》\s\"'“”‘’、，。；：:！!？?｜|]")
# Titles scraped from gov CMS often carry inline markup ("文化产业发展<div>十五五
# 规划"), which otherwise blocks the name match.
HTML_TAG_RE = re.compile(r"<[^>]{1,40}>")
# Words that make a name generic enough to be a coincidence rather than a program.
GENERIC_RE = re.compile(r"^(?:通知|公告|公示|说明|意见|方案)$")
# Statutes and regulations are cited as the authority for a notice, not as the
# program itself. "关于…的公示（依据《中华人民共和国大气污染防治法》）" is a
# quota notice, not a 大气法 program.
STATUTE_RE = re.compile(r"(?:法|条例|规定|解释|决定|批复)$")
STATUTE_HINT_RE = re.compile(r"^中华人民共和国|^国务院令|^\d{4}年中华人民共和国")


# Issuance serials and mastheads get glued into the captured span when a page
# has no proper instrument name. Neither is a program.
SERIAL_RE = re.compile(r"(?:令|办|函|文|电|字|号|密)[\u4e00-\u9fa5]{0,6}(?:[\u4e00-\u9fa5]{0,8}(?:令|办|函|文|电|字|号)){2,}")
MASTHEAD_RE = re.compile(r"(?:日报|晚报|时报|新闻|通讯|广播|电视台|报$|刊$|网$|门户$)")


def normalize_name(raw: str) -> str:
    """Collapse the administrative wrapper so levels match each other."""
    name = HTML_TAG_RE.sub("", str(raw or ""))
    name = LEVEL_NOISE_RE.sub("", name)
    name = PREFIX_RE.sub("", name)
    name = SUFFIX_RE.sub("", name)
    name = re.sub(r"^的+|的+$", "", name)
    return name.strip()


def is_program_name(name: str) -> bool:
    if len(name) < 4 or len(name) > 60:
        return False
    if GENERIC_RE.match(name):
        return False
    if STATUTE_HINT_RE.match(name):
        return False
    if SERIAL_RE.search(name) or MASTHEAD_RE.search(name):
        return False
    if FURNITURE_RE.search(name):
        return False
    # A bare "…法/…条例" is a statute. Programs are 方案/计划/工程/行动/专项, and
    # an administrative 办法/细则 that carries a subject.
    if STATUTE_RE.search(name) and not re.search(r"(?:实施方案|管理办法|实施细则|工作方案|试点)", name):
        return False
    # Require a program-shaped tail so a stray noun phrase does not qualify.
    if not re.search(r"(?:计划|方案|行动|工程|专项|振兴|试点|示范|建设|规划|升级|改造|提升|服务)$", name):
        return False
    return bool(re.search(r"[一-鿿]{2,}", name))


def extract_program_candidates(record: Mapping[str, Any]) -> list[str]:
    """Return normalised candidate program names, best source first."""
    title = str(record.get("title") or "")
    content = str(record.get("content") or "")
    out: list[str] = []
    for pattern, text in ((QUOTED_RE, title), (ISSUING_RE, title), (QUOTED_RE, content),
                           (LEADING_RE, content), (ISSUING_RE, content)):
        for match in pattern.finditer(text):
            name = normalize_name(match.group(1))
            if is_program_name(name) and name not in out:
                out.append(name)
            if len(out) >= 4:
                return out
    return out


def assign_programs(records: Iterable[Mapping[str, Any]]) -> dict[int, str | None]:
    """Pick the program name per record: the longest candidate that is a real name.

    "最长" matters: "关于印发《X专项资金管理办法》的通知" yields both the full
    instrument and the bare "X" fragment, and the full one is the program.
    """
    out: dict[int, str | None] = {}
    for record in records:
        candidates = extract_program_candidates(record)
        best = max(candidates, key=len) if candidates else None
        out[int(record["id"])] = best
    return out


def group_by_program(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Group records under their program name."""
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        candidates = extract_program_candidates(record)
        if not candidates:
            continue
        name = max(candidates, key=len)
        groups[name].append(record)
    return dict(groups)


def analyse_programs(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Report which names meet the AGENTS.md 4.9 test, and which come close."""
    rows = list(records)
    groups = group_by_program(rows)
    report: list[dict[str, Any]] = []
    for name, items in groups.items():
        years = {str(i.get("publish_date") or "")[:4] for i in items if i.get("publish_date")}
        levels = {str(i.get("gov_level") or "") for i in items if i.get("gov_level")}
        provinces = {str(i.get("region_province")) for i in items if i.get("region_province")}
        report.append({
            "name": name,
            "documents": len(items),
            "years": sorted(years),
            "levels": sorted(levels),
            "provinces": sorted(p for p in provinces if p and p != "None"),
            "multi_year": len(years) >= 2,
            "multi_level": len(levels) >= 2,
            "is_program": len(years) >= 2 and len(levels) >= 2,
        })
    report.sort(key=lambda r: (-r["is_program"], -r["documents"]))
    return {
        "records": len(rows),
        "candidates": len(report),
        "programs": sum(1 for r in report if r["is_program"]),
        "near_miss_multi_year": sum(1 for r in report if r["multi_year"] and not r["multi_level"]),
        "near_miss_multi_level": sum(1 for r in report if r["multi_level"] and not r["multi_year"]),
        "detail": report,
    }
