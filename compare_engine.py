"""Policy comparison engine — field-level diff across regions.

Builds the 对比工作台 matrix: for one topic, compare the same schema field
across every linked region document. Pure functions over plain dicts so the
page layer and tests can drive it without touching the DB.

Cell diff types (rendered on the workbench):
    diff     琥珀差异高亮   normalized values disagree across regions
    same     一致项折叠     all comparable regions agree
    missing  未出台灰斜体   region has no linked document for this topic
    pending  待确认蓝虚线   extraction absent or not yet review-confirmed
    revoked  已废止划线     source document marked withdrawn
Extra conditional clauses surface as a boolean overlay (紫角标) on any cell.
"""
from __future__ import annotations

import re
from typing import Any

# 视为"附加条件"的引导词：命中则给单元格加 extra 角标。
EXTRA_PATTERN = re.compile(r"同时|另外|此外|还需|另行|不得同时|叠加享受")

_PUNCT = str.maketrans("，。；：（）！？,.;:()!?\u3000", "，。；：（）！？,.;:()!? ")


def normalize_value(text: Any) -> str:
    """Canonical form for equality: collapse whitespace, strip punctuation, casefold."""
    cleaned = re.sub(r"\s+", "", str(text or "")).translate(_PUNCT)
    return cleaned.casefold().strip("，。；,;")


def has_extra_clause(text: Any) -> bool:
    return bool(EXTRA_PATTERN.search(str(text or "")))


def _cell_missing(region: str) -> dict[str, Any]:
    return {"region": region, "value": None, "diff_type": "missing", "extra": False,
            "confidence": 0, "evidence_text": "该地区未出台相关文件", "announcement_id": None}


def _cell_pending(region: str, announcement_id: int | None, confidence: int = 0) -> dict[str, Any]:
    return {"region": region, "value": None, "diff_type": "pending", "extra": False,
            "confidence": confidence, "evidence_text": "字段待人工确认", "announcement_id": announcement_id}


def build_matrix(
    docs: list[dict[str, Any]],
    extractions: dict[int, dict[str, dict[str, Any]]],
    field_schema: list[dict[str, str]],
    expected_regions: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the comparison matrix.

    docs          one record per region: {id, region, title, retention_class, url}
    extractions   {announcement_id: {field_key: {value, status, confidence, evidence_text}}}
    field_schema  [{field_key, label}] ordered rows
    expected_regions  regions the user explicitly selected; ones without a doc
                      get "missing" cells (未出台) instead of being silently dropped

    Returns {"rows": [...], "counts": {...}, "regions": [...], "docs": [...]}
    """
    doc_by_region: dict[str, dict[str, Any] | None] = {}
    region_order: list[str] = []
    for doc in docs:
        region = str(doc.get("region") or "未知")
        if region not in doc_by_region:
            doc_by_region[region] = doc
            region_order.append(region)
    for region in expected_regions or []:
        if region not in doc_by_region:
            doc_by_region[region] = None
            region_order.append(region)

    rows: list[dict[str, Any]] = []
    counts = {"diff": 0, "same": 0, "missing": 0, "pending": 0, "revoked": 0}

    for spec in field_schema:
        key = spec["field_key"]
        cells: dict[str, dict[str, Any]] = {}
        comparable: list[str] = []

        for region in region_order:
            doc = doc_by_region[region]
            if doc is None:
                cells[region] = _cell_missing(region)
                continue
            ann_id = doc.get("id")
            if doc.get("retention_class") == "revoked":
                cells[region] = {"region": region, "value": None, "diff_type": "revoked", "extra": False,
                                 "confidence": 0, "evidence_text": "文件已废止", "announcement_id": ann_id}
                continue
            extraction = (extractions.get(ann_id) or {}).get(key)
            if not extraction or extraction.get("value") in (None, ""):
                cells[region] = _cell_pending(region, ann_id,
                                              int(extraction.get("confidence") or 0) if extraction else 0)
                continue
            if extraction.get("status") == "pending":
                cells[region] = {"region": region, "value": extraction.get("value"), "diff_type": "pending",
                                 "extra": has_extra_clause(extraction.get("value")),
                                 "confidence": int(extraction.get("confidence") or 0),
                                 "evidence_text": extraction.get("evidence_text"), "announcement_id": ann_id}
                continue
            value = str(extraction.get("value"))
            cells[region] = {"region": region, "value": value, "diff_type": "diff", "extra": has_extra_clause(value),
                             "confidence": int(extraction.get("confidence") or 0),
                             "evidence_text": extraction.get("evidence_text"), "announcement_id": ann_id}
            comparable.append(region)

        # 归一化后一致 → same；否则保持 diff。
        normalized = {r: normalize_value(cells[r].get("value")) for r in comparable}
        distinct = {v for v in normalized.values() if v}
        if comparable and len(distinct) <= 1:
            for r in comparable:
                cells[r]["diff_type"] = "same"

        row_counts = {"diff": 0, "same": 0, "missing": 0, "pending": 0, "revoked": 0}
        for cell in cells.values():
            row_counts[cell["diff_type"]] += 1
        for state, n in row_counts.items():
            counts[state] += n

        variants = sorted({normalized[r] for r in comparable if normalized[r]})
        if row_counts["missing"]:
            summary = "、".join(r for r in region_order if cells[r]["diff_type"] == "missing") + " 未出台"
        elif row_counts["diff"]:
            summary = f"{row_counts['diff']} 地表述不一（共 {len(variants)} 种口径）"
        elif row_counts["pending"]:
            summary = f"{row_counts['pending']} 地字段待确认"
        else:
            summary = "各地口径一致"

        rows.append({
            "field_key": key,
            "label": spec.get("label", key),
            "cells": cells,
            "counts": row_counts,
            "summary": summary,
        })

    return {"rows": rows, "counts": counts,
            "regions": region_order,
            "docs": [{"id": d.get("id"), "region": d.get("region"), "title": d.get("title"),
                      "url": d.get("url"), "published_at": d.get("published_at")} for d in docs]}


def to_csv_rows(matrix: dict[str, Any]) -> list[list[str]]:
    """Flatten the matrix for CSV export: header + one row per field."""
    regions = matrix["regions"]
    header = ["字段"] + [f"{r}·状态" for r in regions] + regions
    rows: list[list[str]] = [header]
    for row in matrix["rows"]:
        line: list[str] = [row["label"]]
        for r in regions:
            line.append(row["cells"][r]["diff_type"])
        for r in regions:
            line.append(str(row["cells"][r].get("value") or ""))
        rows.append(line)
    return rows
