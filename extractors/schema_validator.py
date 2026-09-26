from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .gliner_extractor import validate_payload


def load_schema(path: str = "config/schema.yaml") -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("schema configuration must contain a mapping")
    return data


def validate_extraction(
    payload: Mapping[str, Any],
    schema_path: str = "config/schema.yaml",
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    load_schema(schema_path)
    return validate_payload(payload, source=source)
