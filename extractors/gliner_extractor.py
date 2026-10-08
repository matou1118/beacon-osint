from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

try:
    from gliner2 import GLiNER2
except ImportError:
    GLiNER2 = None


ENTITY_DESCRIPTIONS = {
    "issuing_body": "发布机构全称",
    "region": "行政区划名称",
    "target_group": "适用人群或主体",
    "target_industry": "适用行业",
}


def _load_category_values() -> list[str]:
    """从 config/categories.yaml 动态加载一级分类枚举。

    GLiNER2 的 classification 约束和 validate_payload 的越界校验都以此为准，
    必须与 config/categories.yaml 的 l1 枚举一致，否则政务文种等新类会被
    强制改回「其他」，撤销 classify() 的正确分类。
    """
    path = Path(__file__).resolve().parent.parent / "config" / "categories.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list((data.get("l1") or {}).keys())


CATEGORY_VALUES = _load_category_values()

ADMISSION_SUBCATEGORY_VALUES = [
    "高职单招",
    "军队院校招生",
    "公安司法院校招生",
    "公费定向培养",
    "行业院校招生",
    "职业院校招生",
    "事业单位定向培养",
    "招生政策解读",
]

GOV_LEVEL_VALUES = ["国家部委", "省级", "市级", "县级"]


def build_schema(extractor: Any) -> Any:
    schema = (
        extractor.create_schema()
        .entities(ENTITY_DESCRIPTIONS)
        .classification("gov_level", GOV_LEVEL_VALUES)
        .classification("category_l1", CATEGORY_VALUES)
        .classification("admission_sub_category", ADMISSION_SUBCATEGORY_VALUES)
        .structure("announcement")
        .field("title", dtype="str", description="公告标题，必须摘录原文标题")
        .field("publish_date", dtype="str", description="公告发布日期")
        .field("amount", dtype="str", description="金额、补助或资金规模")
        .field("deadline", dtype="str", description="申报或办理截止日期")
        .field("conditions", dtype="str", description="申报条件或适用条件")
        .field("contact", dtype="str", description="官方联系电话或联系方式")
        .field("key_points", dtype="list", description="原文关键短句，不得改写")
        .field("institution_name", dtype="str", description="招生院校名称")
        .field("institution_type", dtype="str", description="招生院校类型")
        .field("admission_type", dtype="str", description="招生类型")
        .field("enrollment_count", dtype="str", description="招生计划人数")
        .field("tuition_info", dtype="str", description="学费标准")
        .field("employment_guarantee", dtype="str", description="就业保障原文")
    )
    return getattr(schema, "schema", schema)


def _to_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if hasattr(value, "json"):
        raw = value.json()
        return _to_mapping(json.loads(raw) if isinstance(raw, str) else raw)
    if isinstance(value, str):
        return _to_mapping(json.loads(value))
    raise ValueError("GLiNER2 result must be a JSON object")


def _normalize_result(data: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(data)
    announcement = result.get("announcement")
    if isinstance(announcement, list):
        announcement = announcement[0] if announcement else {}
    if isinstance(announcement, Mapping):
        result.update(dict(announcement))
    for key in ("gov_level", "category_l1", "admission_sub_category"):
        if key in data:
            result[key] = data[key]
    entities = data.get("entities")
    if isinstance(entities, Mapping):
        for key, value in entities.items():
            if isinstance(value, list):
                value = value[0] if value else None
            if value is not None and result.get(key) is None:
                result[key] = value
    result.pop("announcement", None)
    result.pop("entities", None)
    return result


def validate_payload(payload: Mapping[str, Any], source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = _normalize_result(payload)
    needs_review = bool(result.get("needs_review", 0))

    def mark_review() -> None:
        nonlocal needs_review
        needs_review = True

    for required in ("title", "issuing_body", "gov_level"):
        if not result.get(required):
            mark_review()
    if source:
        if source.get("title") is not None and not result.get("title"):
            result["title"] = source["title"]
        if source.get("issuing_body") is not None:
            result["issuing_body"] = source["issuing_body"]
        if source.get("name") is not None:
            result["issuing_body"] = source["name"]
        if source.get("gov_level") is not None:
            result["gov_level"] = source["gov_level"]
        if source.get("jurisdiction_code") is not None:
            result["jurisdiction_code"] = source["jurisdiction_code"]
        if source.get("region_province") is not None and result.get("region") is None:
            result["region"] = source["region_province"]
    if not result.get("title"):
        mark_review()
    if not result.get("issuing_body"):
        mark_review()
    if not result.get("gov_level"):
        mark_review()
    gov_level = result.get("gov_level")
    if gov_level is not None and gov_level not in GOV_LEVEL_VALUES:
        mark_review()
    category = result.get("category_l1")
    if category not in CATEGORY_VALUES:
        result["category_l1"] = "其他"
        mark_review()
    admission_category = result.get("admission_sub_category")
    if result.get("category_l1") == "招生信息":
        if admission_category not in ADMISSION_SUBCATEGORY_VALUES:
            result["admission_sub_category"] = None
            mark_review()
    elif admission_category is not None:
        result["admission_sub_category"] = None
        mark_review()
    key_points = result.get("key_points")
    if key_points is not None and not isinstance(key_points, list):
        result["key_points"] = []
        mark_review()
    result["needs_review"] = int(needs_review)
    return result


class Gliner2Extractor:
    def __init__(self, model_name: str = "fastino/gliner2-base-v1", backend: Any | None = None) -> None:
        self.model_name = model_name
        self.backend = backend

    def load(self) -> None:
        if self.backend is not None:
            return
        if GLiNER2 is None:
            raise RuntimeError("gliner2 is not installed; install requirements.txt and verify the model license")
        self.backend = GLiNER2.from_pretrained(self.model_name)

    def create_schema(self) -> Any:
        self.load()
        return self.backend.create_schema()

    def extract(
        self,
        text: str,
        schema: Any | None = None,
        source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        self.load()
        active_schema = schema if schema is not None else build_schema(self)
        result = self.backend.extract(text, active_schema)
        return validate_payload(_to_mapping(result), source=source)
