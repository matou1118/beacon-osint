from __future__ import annotations

from typing import Any, Mapping

from .api.education_api import EducationApiCollector
from .api.legal_db import LegalDbCollector
from .api.open_data import OpenDataCollector
from .download import DownloadCollector
from .media.media_collector import MediaCollector
from .rss.rss_collector import RssCollector
from .education import EducationCollector
from .web.county import CountyCollector
from .web.municipal import MunicipalCollector
from .web.national import NationalCollector
from .web.provincial import ProvincialCollector


class SourceRouter:
    def collector_for(self, source: Mapping[str, Any]) -> Any:
        method = str(source.get("access_method") or "").lower()
        api_kind = str(source.get("api_kind") or source.get("source_type") or "").lower()
        if source.get("type") == "media" or source.get("media_type"):
            return MediaCollector(source)
        if method in {"download", "file", "attachment"} or source.get("download_url"):
            return DownloadCollector(source)
        if method in {"api", "government_api", "json_api"} or source.get("list_endpoint"):
            if api_kind in {"legal_db", "legal", "flk"}:
                return LegalDbCollector(source)
            if api_kind in {"education", "education_api", "education_resource"}:
                return EducationApiCollector(source)
            return OpenDataCollector(source)
        if source.get("collector") == "education" or api_kind in {"education", "education_resource", "school"}:
            return EducationCollector(source)
        if method == "rss" or source.get("rss_url"):
            return RssCollector(source)
        if api_kind in {"education", "education_resource", "school"}:
            return EducationCollector(source)
        level = source.get("level")
        if level == "provincial":
            return ProvincialCollector(source)
        if level == "municipal":
            return MunicipalCollector(source)
        if level == "county":
            return CountyCollector(source)
        return NationalCollector(source)

    def collect(self, source: Mapping[str, Any], limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        collector = self.collector_for(source)
        try:
            return collector.collect(limit=limit), list(getattr(collector, "errors", []))
        finally:
            collector.close()
