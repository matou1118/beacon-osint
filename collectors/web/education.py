from __future__ import annotations

from typing import Any, Mapping

from ..html_list import HtmlListCollector


class EducationCollector(HtmlListCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("source_type", "education_resource")
        super().__init__(normalized, **kwargs)
