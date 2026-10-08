"""Auto-discovered comparison topics, built from the real store.

The hand-seeded `policy_topics` table holds one row and zero field extractions, so
the topic-scoped comparison matrix had nothing to show. The engines that actually
work on real data are policy_compare (theme tagging, amount normalisation, the
national -> province -> city chain) and extractors.program_extract (专项 names), and
neither was reachable from the workbench.

This module is the pure data layer for that: given the announcements table, it
produces the same shape the doc asks for -- a topic, the levels that issued
something, the levels that did not ("未出台"), comparable amounts, and a Markdown
/ CSV / JSON export payload. No judgement, no recommendation: only what the
sources say and what is missing.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

LEVEL_ORDER = ["国家部委", "国家级", "省级", "市级", "县级", "区县级"]
LEVEL_SHORT = {
    "国家部委": "国家", "国家级": "国家", "省级": "省", "市级": "市",
    "县级": "县", "区县级": "区县",
}

# Comparability ranking. A topic is only useful for cross-region comparison when
# it spans at least two levels and carries at least one normalised amount, so the
# workbench leads with those instead of whatever is newest.
MIN_DOCS_FOR_TOPIC = 2
AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|亿元|万|亿|元)")
AMOUNT_UNIT = {"元": 1, "万元": 10_000, "亿": 100_000_000, "亿元": 100_000_000, "万": 10_000}

# A number followed by 万/亿 in Chinese policy text is usually NOT money:
# "1000万吨/年" is coal-mine capacity, "成品油价格" is 元/升, "500万亩" is area.
# Measured on the real store, a context-anywhere rule turned every 万吨/年 mining
# approval into a multi-hundred-million-yuan subsidy. So the money word has to sit
# right next to the number, and quantity units are rejected outright.
MONEY_WORDS = (
    "补贴", "资助", "奖励", "资金", "补助", "扶持", "奖补", "贴补", "专项", "预算",
    "投入", "额度", "上限", "不低于", "不超过", "标准", "经费", "拨款", "投资", "金额",
    "安排", "计提", "基数", "保费", "减免", "补偿",
)
QUANTITY_RE = re.compile(
    r"(?:万吨|吨|万亩|亩|亿千瓦|万千瓦|千瓦|平方公里|公顷|万吨/年|吨/年|万立方米|"
    r"元/升|元/吨|元/千瓦时|人次|万户|万家|万个|人|台|辆|套|项|件|次)"
)
# Phone numbers, postcodes and document numbers look like amounts.
AMOUNT_EXCLUDE = re.compile(r"(?:电话|传真|邮编|证号|编号|文号|代码|号)")
# Sane bounds: 1,000 yuan to 1 trillion yuan. Outside that it is a count, a year,
# or a concatenation of two numbers.
AMOUNT_MIN = 1_000
AMOUNT_MAX = 1_000_000_000_000
CONTEXT_WINDOW_BEFORE = 14
CONTEXT_WINDOW_AFTER = 6

# Portal and section pages reached the store through list-page scraping. They carry
# no policy fact, but they do carry a plausible-looking figure ("海南省人民政府网"
# normalised to 1.7 trillion yuan), so they have to be excluded before comparing.
# The collector raises SectionLandingError for most of these; the leftovers are
# site names with no document verb in them.
PORTAL_MARKERS = (
    "人民政府网", "政府网", "门户网站", "政务服务网", "新闻中心", "政务公开", "信息公开",
    "办事大厅", "互动交流", "专题专栏", "工作动态", "政务联播", "公告公示", "通知公告",
    "政策文件", "信息公开目录", "政府信息公开", "关于本站", "网站首页", "返回首页",
    "设为首页", "网站地图", "政务服务", "专栏", "频道", "板块", "窗口", "信息港",
)


def is_portal_page(record: dict[str, Any]) -> bool:
    """True when the row is a site/section name rather than a document.

    Matching is containment, not equality: the store also holds
    "四川省商务厅-外贸全周期服务专栏", "宁波12345政务服务" and
    "政务联播 - 四川省人民政府网站", which are all landing pages with a prefix or
    a suffix. A document verb anywhere in the title wins, so
    "关于开展xxx的通知" is never mistaken for a page.
    """
    title = str(record.get("title") or "").strip()
    if not title:
        return True
    if len(title) > 40:
        return False
    if DOCUMENT_VERB_RE.search(title):
        return False
    return any(marker in title for marker in PORTAL_MARKERS)
# News and interview items still yield plausible-looking figures ("《顶流》访谈
# 院士：新型电网" -> 5万元). Their amounts are shown as 待确认, never counted as a
# comparable fact, which is the doc's own annotation for an incomplete extraction.
NON_DOCUMENT_CATEGORIES = {"新闻动态", "活动通知", "其他", "商业机会"}
# A document verb anywhere in the title beats any portal marker, so
# "关于开展xxx的通知" is never mistaken for a landing page.
#
# 2026-09-30 补：新闻类动词也进来。实测 76 条真内容被判门户页，源头是标题
# 带了网站名后缀就命中 PORTAL_MARKERS，而标题里又没有公文动词：
#   "内蒙古：二十余万新就业形态人员有了职业伤害保障 - 工作动态 - 中国就业网"
#   "中华人民共和国政府信息公开条例"        <- 有"条例"但不在原列表里
#   "关于本站"                              <- 这个是真门户，靠其它标记
# 补上任/召开/举行/开幕/签约/揭牌/发布(已有)/获得/实现/成功/入选/荣获/
# 条例/办法(已有)/规定(已有)/解读(已有) 等新闻与法规常用动词。
DOCUMENT_VERB_RE = re.compile(
    r"(?:通知|公告|公示|通告|办法|方案|意见|规定|决定|批复|函|令|"
    r"解读|答记者问|政策|通报|发布|征求意见|"
    r"条例|规章|细则|准则|"
    r"任免|任职|履新|上任|被任命|"
    r"召开|举行|举办|开幕|启幕|签约|揭牌|成立|"
    r"获得|荣获|入选|成功|实现|推进|印发)"
)


def is_document(record: dict[str, Any]) -> bool:
    """Whether the row is a document rather than news or an interview."""
    if str(record.get("category_l1") or "") in NON_DOCUMENT_CATEGORIES:
        return False
    return bool(DOCUMENT_VERB_RE.search(str(record.get("title") or "")))


def parse_amount(*values: Any) -> int | None:
    """Normalise the first plausible money figure to a yuan integer.

    Conservative on purpose: a missing amount shows as 未出台, a wrong one makes
    the whole comparison a lie.
    """
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for match in AMOUNT_RE.finditer(text):
            tail = text[match.end():match.end() + CONTEXT_WINDOW_AFTER]
            if QUANTITY_RE.match(tail.lstrip()):
                # "1000万吨/年" -- capacity, not money.
                continue
            head = text[max(0, match.start() - CONTEXT_WINDOW_BEFORE):match.start()]
            if QUANTITY_RE.search(head[-8:]):
                continue
            if AMOUNT_EXCLUDE.search(text[max(0, match.start() - 6):match.end() + 6]):
                continue
            if not any(word in head for word in MONEY_WORDS):
                # No money word next to the number: treat as a count, not an amount.
                continue
            try:
                number = float(match.group(1))
            except ValueError:
                continue
            total = int(number * AMOUNT_UNIT.get(match.group(2), 1))
            if not AMOUNT_MIN <= total <= AMOUNT_MAX:
                continue
            return total
    return None


def fetch_announcements(db_path: str | Path) -> list[dict[str, Any]]:
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(row) for row in con.execute(
        "SELECT id, source_id, title, publish_date, content, category_l1, category_l2, "
        "gov_level, region_province, region_city, region_county, amount, deadline, "
        "target_industry, source_url, extra FROM announcements"
    )]
    con.close()
    for record in rows:
        try:
            parsed = json.loads(record.pop("extra") or "{}")
        except (TypeError, ValueError):
            parsed = {}
        record["issuing_body"] = parsed.get("issuing_body") or record.get("source_id")
        record["summary"] = parsed.get("summary") or ""
        # The pipeline already extracts an amount column; trust it before falling
        # back to re-parsing the body, which is where false positives come from.
        record["_amount_value"] = parse_amount(record.get("amount")) or parse_amount(
            record.get("title"), record.get("summary"), record.get("content")
        )
        record["_portal"] = is_portal_page(record)
        record["_document"] = is_document(record)
    return rows


def _region_of(record: dict[str, Any]) -> str:
    for key in ("region_province", "region_city", "region_county"):
        value = record.get(key)
        if value:
            return str(value)
    return "未标注地区"


def _level_of(record: dict[str, Any]) -> str:
    return str(record.get("gov_level") or "未标注")


# Junk buckets: 其他/未分类 are catch-alls, not topics. Grouping them produced a
# 384-row "topic" that led every ranking and compared portal pages to each other.
JUNK_CATEGORIES = {"其他", "未分类", "", "None"}


def build_topics(records: list[dict[str, Any]], include_junk: bool = False) -> list[dict[str, Any]]:
    """Group records into comparison topics by category and title keywords.

    Topics are the unit the doc compares: one subject, many regions. Grouping by
    (category_l2, category_l1) keeps the topic list to something a person can scan
    and every group is backed by real rows -- nothing is invented.
    """
    usable = [r for r in records if not r.get("_portal")]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in usable:
        l1 = str(record.get("category_l1") or "未分类")
        l2 = str(record.get("category_l2") or "未分类")
        if not include_junk and (l1 in JUNK_CATEGORIES or l2 in JUNK_CATEGORIES):
            continue
        groups.setdefault((l1, l2), []).append(record)

    topics: list[dict[str, Any]] = []
    for (l1, l2), items in groups.items():
        if len(items) < MIN_DOCS_FOR_TOPIC and not include_junk:
            continue
        levels = Counter(_level_of(item) for item in items)
        regions = {_region_of(item) for item in items if _region_of(item) != "未标注地区"}
        amounts = [item["_amount_value"] for item in items if item.get("_amount_value")]
        topics.append({
            "name": f"{l1} · {l2}" if l2 and l2 != "未分类" else l1,
            "category_l1": l1,
            "category_l2": l2,
            "documents": items,
            "doc_count": len(items),
            "levels": dict(levels),
            "level_count": len(levels),
            "regions": sorted(regions),
            "region_count": len(regions),
            "amount_count": len(amounts),
            "amount_min": min(amounts) if amounts else None,
            "amount_max": max(amounts) if amounts else None,
            "comparable": len(levels) >= 2 and bool(amounts),
        })
    topics.sort(key=lambda t: (t["comparable"], t["level_count"], t["region_count"], t["doc_count"]), reverse=True)
    return topics


def _cell(record: dict[str, Any] | None) -> str:
    if record is None:
        return ""
    if record.get("amount"):
        amount = record["amount"]
        return f"{amount / 10000:.0f}万元" if amount >= 10000 else f"{amount:.0f}元"
    if record.get("amount_unverified"):
        return f"待确认（原文提及 {record['amount_unverified'] / 10000:.0f}万元）"
    return "未出台"


def build_comparison(topic: dict[str, Any]) -> dict[str, Any]:
    """One topic as a comparison matrix: rows = regions, columns = comparable fields.

    Missing values are labelled 未出台 rather than left blank, per the doc's
    difference annotation table.
    """
    regions = sorted({_region_of(item) for item in topic["documents"]})
    by_region: dict[str, list[dict[str, Any]]] = {}
    for item in topic["documents"]:
        by_region.setdefault(_region_of(item), []).append(item)

    rows: list[dict[str, Any]] = []
    for region in regions:
        items = sorted(by_region[region], key=lambda r: str(r.get("publish_date") or ""))
        primary = items[0]
        is_doc = bool(primary.get("_document"))
        amount = primary.get("_amount_value") if is_doc else None
        rows.append({
            "region": region,
            "level": LEVEL_SHORT.get(_level_of(primary), _level_of(primary)),
            "title": primary.get("title"),
            "issuing_body": primary.get("issuing_body"),
            "publish_date": primary.get("publish_date"),
            "amount": amount,
            # Kept but not comparable: a news item's figure is shown with its
            # source so a reader can judge, never used as a difference fact.
            "amount_unverified": None if is_doc else primary.get("_amount_value"),
            "amount_state": "normal" if is_doc else "pending",
            "deadline": primary.get("deadline"),
            "url": primary.get("source_url"),
            "doc_count": len(items),
            "is_document": is_doc,
        })

    amounts = [r["amount"] for r in rows if r.get("amount")]
    deadlines = {r["deadline"] for r in rows if r.get("deadline")}
    amount_values = {r["amount"] for r in rows if r.get("amount")}
    pending_cells = [r["region"] for r in rows if r["amount_state"] == "pending"]
    # Difference annotation, straight from the doc's table.
    if pending_cells and amount_values:
        amount_state = "pending"
    elif len(amount_values) > 1:
        amount_state = "diff"
    elif len(amount_values) == 1:
        amount_state = "same"
    else:
        amount_state = "missing"
    if len(deadlines) > 1:
        deadline_state = "diff"
    elif deadlines:
        deadline_state = "same"
    else:
        deadline_state = "missing"

    return {
        "topic": topic["name"],
        "category_l1": topic["category_l1"],
        "category_l2": topic["category_l2"],
        "regions": regions,
        "rows": rows,
        "field_states": {"amount": amount_state, "deadline": deadline_state},
        "levels_present": sorted({r["level"] for r in rows}),
        "amount_min": min(amounts) if amounts else None,
        "amount_max": max(amounts) if amounts else None,
        "amount_summary": (
            f"{min(amounts) / 10000:.0f}~{max(amounts) / 10000:.0f}万元" if amounts and max(amounts) >= 10000
            else (f"{amounts[0] / 10000:.0f}万元" if amounts else "未提取到金额")
        ),
        "doc_count": topic["doc_count"],
    }


def to_markdown(result: dict[str, Any]) -> str:
    """Markdown comparison table, ready to paste into a report."""
    header = "| 地区 | 层级 | 金额 | 时限 | 标题 | 发文单位 | 日期 | 原文 |"
    divider = "|---|---|---|---|---|---|---|---|"
    lines = [
        f"## {result['topic']}",
        "",
        f"- 文件数：{result['doc_count']}　覆盖地区：{len(result['regions'])}",
        f"- 金额区间：{result['amount_summary']}",
        f"- 金额标注：{result['field_states']['amount']}　时限标注：{result['field_states']['deadline']}",
        "",
        header,
        divider,
    ]
    for row in result["rows"]:
        cells = [
            row["region"], row["level"],
            _cell(row), row.get("deadline") or "未提取",
            (row.get("title") or "").replace("|", "/"),
            row.get("issuing_body") or "", row.get("publish_date") or "",
            f"[原文]({row['url']})" if row.get("url") else "",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("说明：标注「待确认」的金额来自新闻或访谈类原文，仅列出供参考，未作为差异判定依据。")
    lines.append("")
    return "\n".join(lines)


def to_csv(result: dict[str, Any]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["地区", "层级", "金额(元)", "时限", "标题", "发文单位", "发布日期", "原文链接"])
    for row in result["rows"]:
        writer.writerow([
            row["region"], row["level"], row.get("amount") or "", row.get("deadline") or "",
            row.get("title") or "", row.get("issuing_body") or "", row.get("publish_date") or "",
            row.get("url") or "",
        ])
    return buffer.getvalue()


def to_excel(result: dict[str, Any]) -> bytes | None:
    """Excel export via pandas. Returns None when pandas or openpyxl is missing.

    The openpyxl import happens inside ExcelWriter, so the guard has to wrap the
    write, not just the import.
    """
    try:
        import io

        import pandas as pd
    except ImportError:
        return None
    frame = pd.DataFrame([{
        "地区": r["region"], "层级": r["level"], "金额(元)": r.get("amount"),
        "时限": r.get("deadline") or "", "标题": r.get("title") or "",
        "发文单位": r.get("issuing_body") or "", "发布日期": r.get("publish_date") or "",
        "原文链接": r.get("url") or "",
    } for r in result["rows"]])
    buffer = io.BytesIO()
    try:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name=result["topic"][:28])
    except Exception:  # noqa: BLE001 - openpyxl missing or engine failure
        return None
    return buffer.getvalue()
