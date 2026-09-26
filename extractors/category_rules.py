from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "category_rules.yaml"
DEFAULT_DEPARTMENT_PATH = Path(__file__).resolve().parents[1] / "config" / "department_map.yaml"
DEFAULT_FIELDS = ("title", "content", "key_points", "issuing_body", "functions", "tags")
DEFAULT_TITLE_WEIGHT = 3
DEFAULT_BODY_WEIGHT = 1
DEFAULT_SPECIFIC_BONUS = 2
NOISE_LIMIT = 600
# Process words ("申报"/"通知"/"公告") are shared by many categories, so a title
# carrying a concrete entity (补贴, 招聘, 招生, 津贴) must outweigh them. These
# categories define themselves by that entity, so they get a score bonus.
SPECIFIC_CATEGORIES = frozenset({
    "行业补贴", "养老与社区", "人才政策", "招生信息", "公务员考试", "公招信息", "军队招录",
})
TITLE_WEIGHT = DEFAULT_TITLE_WEIGHT
BODY_WEIGHT = DEFAULT_BODY_WEIGHT
SPECIFIC_BONUS = DEFAULT_SPECIFIC_BONUS


def weights_from(config: Mapping[str, Any] | None) -> tuple[int, int, int]:
    scoring = (config or {}).get("scoring") or {}
    return (
        int(scoring.get("title_weight", DEFAULT_TITLE_WEIGHT)),
        int(scoring.get("body_weight", DEFAULT_BODY_WEIGHT)),
        int(scoring.get("specific_bonus", DEFAULT_SPECIFIC_BONUS)),
    )


def load_department_map(path: str | Path = DEFAULT_DEPARTMENT_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config/department_map.yaml must contain a mapping")
    return data


def classify_by_department(
    record: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> dict[str, str] | None:
    """Classify from the issuing body, which is far more reliable than the title.

    A ministry or bureau decides the business area, so "财政部关于记账式贴现国债
    发行" is finance regardless of the wording. Returns None when nothing
    matches so the caller can fall back to keyword rules.
    """
    rules = config if isinstance(config, dict) and config.get("department_map") else load_department_map()
    body = _normalize(record.get("issuing_body") or record.get("department") or "")
    if not body:
        return None
    for category, markers in (rules.get("department_map") or {}).items():
        for marker in markers or []:
            normalized = _normalize(marker)
            if normalized and normalized in body:
                return {"category_l1": str(category), "category_l2": "主管部门归类", "matched_department": str(marker)}
    return None


def _normalize(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    return re.sub(r"\s+", "", str(value or "")).casefold()


def load_category_rules(path: str | Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
        raise ValueError("config/category_rules.yaml must contain a categories mapping")
    return data


def body_text(content: Any, limit: int = NOISE_LIMIT) -> str:
    return _normalize(content)[:limit]


def record_text(record: Mapping[str, Any], fields: tuple[str, ...] = DEFAULT_FIELDS) -> str:
    return _normalize(" ".join(_normalize(record.get(field)) for field in fields))


def score_category(text: str, spec: Mapping[str, Any]) -> tuple[int, str]:
    """Return (weighted hits, best subcategory).

    Within a category, a subcategory holding more distinct hits is the more
    specific match, so it wins over one that merely matched first.
    """
    best_subcategory = ""
    best_hits = 0
    best_span = 0
    score = 0
    for subcategory, keywords in (spec.get("subcategories") or {}).items():
        hits = 0
        span = 0
        for keyword in keywords or []:
            normalized = _normalize(keyword)
            if normalized and normalized in text:
                hits += 1
                span = max(span, len(normalized))
        if not hits:
            continue
        score += hits
        # Specificity beats hit count: a title naming "中央机关及其直属机构"
        # is a national exam even if it also contains the bare word "公务员",
        # which would otherwise drag it into the provincial-exam bucket.
        if span > best_span or (span == best_span and hits > best_hits):
            best_hits, best_span, best_subcategory = hits, span, str(subcategory)
    return score, best_subcategory


def classify(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, str]:
    rules = config if isinstance(config, dict) and config.get("categories") else load_category_rules()
    defaults = rules.get("defaults") or {}
    title_weight, body_weight, specific_bonus = weights_from(rules)
    title = _normalize(record.get("title"))
    body = body_text(record.get("content"))
    best_category = str(defaults.get("category_l1") or "其他")
    best_subcategory = str(defaults.get("category_l2") or "未分类")
    best_score = 0
    for category, spec in rules["categories"].items():
        if not isinstance(spec, dict):
            continue
        title_score, title_sub = score_category(title, spec)
        body_score, body_sub = score_category(body, spec)
        score = title_score * title_weight + body_score * body_weight
        if title_score and category in SPECIFIC_CATEGORIES:
            score += specific_bonus
        if score > best_score:
            best_category, best_subcategory, best_score = str(category), title_sub or body_sub, score
    by_keyword = {"category_l1": best_category, "category_l2": best_subcategory}
    # The issuing body is the stronger signal, but only when the keyword rules
    # found nothing: a title like "公务员招录公告" from 人社厅 is an exam notice,
    # not a department bucket.
    if best_score == 0:
        by_department = classify_by_department(record)
        if by_department:
            return {"category_l1": by_department["category_l1"], "category_l2": by_department["category_l2"]}
    return by_keyword
