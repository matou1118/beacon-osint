from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

from storage.documents import ingest_public_document

from .base import BaseCollector


class DownloadCollector(BaseCollector):
    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(source, **kwargs)
        self.errors: list[dict[str, str]] = []

    def _download_record(self, url: str) -> dict[str, Any] | None:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        download_config = dict(self.source.get("downloads") or {})
        if not download_config:
            config_path = Path("config/downloads.yaml")
            if config_path.exists():
                download_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        try:
            result = ingest_public_document(
                url,
                self.source.get("download_dir") or "data/downloads",
                self.allowed_domains,
                self.source_id,
                download_config,
                client=self.client,
            )
        except Exception as exc:
            self.errors.append({"url": url, "error": str(exc)})
            return None
        extraction = result["extraction"]
        title = self.source.get("title") or Path(urlparse(url).path).stem
        publish_date = self.source.get("publish_date")
        if not publish_date:
            match = re.search(r"(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})", Path(urlparse(url).path).stem)
            publish_date = "-".join(match.groups()) if match else None
        record = self.normalize_record({"title": title, "publish_date": publish_date, "content": extraction.get("text") or "", "url": url})
        record["content_source"] = "public_download"
        record["needs_review"] = int(bool(extraction.get("needs_review")))
        record["download_metadata"] = result["download"]
        record["extraction_metadata"] = {key: value for key, value in extraction.items() if key != "text"}
        return record

    def fetch_list(self) -> list[str]:
        return [str(self.source.get("download_url") or self.list_url)]

    def fetch_detail(self, url: str) -> dict[str, Any]:
        record = self._download_record(url)
        if record is None:
            raise RuntimeError(f"unable to download public document: {url}")
        return record

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        record = self._download_record(self.fetch_list()[0])
        return [record][:limit] if record is not None and limit is not None else ([record] if record is not None else [])
