from __future__ import annotations

from typing import Any, Mapping

from .base import ApiCollector


class EducationApiCollector(ApiCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("api_kind", "education_api")
        super().__init__(normalized, **kwargs)
