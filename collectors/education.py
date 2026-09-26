from __future__ import annotations

from typing import Any, Mapping

from .web.education import EducationCollector as _WebEducationCollector


class EducationCollector(_WebEducationCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("source_type", "education_resource")
        normalized.setdefault("category_l1", "招生信息")
        super().__init__(normalized, **kwargs)
