from __future__ import annotations

from typing import Any, Mapping

from .base import ApiCollector


class LegalDbCollector(ApiCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        normalized = dict(source)
        normalized.setdefault("api_kind", "legal_db")
        super().__init__(normalized, **kwargs)
