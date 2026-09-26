from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from typing import Any

from extractors.gliner_extractor import Gliner2Extractor, _normalize_result, _to_mapping, build_schema, validate_payload
from extractors.regex_fallback import RegexFallbackExtractor


def load_records(paths: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        records.extend(value if isinstance(value, list) else value.get("records", []))
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        unique[str(record.get("source_url") or record.get("url"))] = record
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+")
    parser.add_argument("--output", default="gliner2_benchmark_report.json")
    parser.add_argument("--max-chars", type=int, default=1000)
    args = parser.parse_args()

    records = load_records(args.inputs)[:10]
    extractor = Gliner2Extractor()
    extractor.load()
    schema = build_schema(extractor)
    fallback = RegexFallbackExtractor()
    items: list[dict[str, Any]] = []
    for record in records:
        started = time.perf_counter()
        item: dict[str, Any] = {
            "source_id": record.get("source_id"),
            "source_url": record.get("source_url"),
            "expected_title": record.get("title"),
            "expected_issuing_body": record.get("issuing_body"),
            "expected_gov_level": record.get("gov_level"),
        }
        try:
            raw = _to_mapping(extractor.backend.extract(str(record.get("content") or "")[: args.max_chars], schema))
            raw_normalized = _normalize_result(raw)
            final = validate_payload(raw, source=record)
            item.update(
                {
                    "json_success": True,
                    "fallback_used": False,
                    "needs_review": final.get("needs_review", 0),
                    "raw_title": raw_normalized.get("title"),
                    "raw_issuing_body": raw_normalized.get("issuing_body"),
                    "raw_gov_level": raw_normalized.get("gov_level"),
                    "final_title": final.get("title"),
                    "final_issuing_body": final.get("issuing_body"),
                    "final_gov_level": final.get("gov_level"),
                }
            )
        except Exception as exc:
            fallback_result = fallback.extract(str(record.get("content") or "")[: args.max_chars], source=record)
            item.update(
                {
                    "json_success": False,
                    "fallback_used": True,
                    "needs_review": 1,
                    "error": str(exc),
                    "fallback_result": fallback_result,
                }
            )
        item["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        items.append(item)
        print(json.dumps({"source_id": item["source_id"], "elapsed_seconds": item["elapsed_seconds"], "json_success": item["json_success"]}, ensure_ascii=True))

    successful = [item for item in items if item.get("json_success")]

    def accuracy(field: str, expected: str) -> float:
        if not successful:
            return 0.0
        return sum(1 for item in successful if item.get(field) == item.get(expected)) / len(successful)

    elapsed = [float(item["elapsed_seconds"]) for item in items]
    report = {
        "model": "fastino/gliner2-base-v1",
        "sample_count": len(items),
        "json_parse_success_rate": len(successful) / len(items) if items else 0.0,
        "raw_accuracy": {
            "title": accuracy("raw_title", "expected_title"),
            "issuing_body": accuracy("raw_issuing_body", "expected_issuing_body"),
            "gov_level": accuracy("raw_gov_level", "expected_gov_level"),
        },
        "source_enriched_accuracy": {
            "title": accuracy("final_title", "expected_title"),
            "issuing_body": accuracy("final_issuing_body", "expected_issuing_body"),
            "gov_level": accuracy("final_gov_level", "expected_gov_level"),
        },
        "average_elapsed_seconds": statistics.mean(elapsed) if elapsed else 0.0,
        "max_elapsed_seconds": max(elapsed) if elapsed else 0.0,
        "under_10_seconds_rate": sum(1 for value in elapsed if value < 10) / len(elapsed) if elapsed else 0.0,
        "items": items,
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
