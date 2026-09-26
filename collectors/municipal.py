from __future__ import annotations

from typing import Any, Mapping

from .html_list import HtmlListCollector


class MunicipalCollector(HtmlListCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("level", "municipal")
        normalized.setdefault("gov_level", "市级")
        super().__init__(normalized, **kwargs)
