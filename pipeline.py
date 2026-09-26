from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping


Payload = Mapping[str, Any]
Formatter = Callable[[Mapping[str, Any]], Any]


def run_native_pipeline(
    payload: Payload,
    crawl: Callable[[Payload], Any],
    summary: Callable[[Any], Any] | None,
    extraction: Callable[[Any], Any],
    metadata: Callable[[Any], Any],
    formatter: Formatter,
) -> Any:
    crawled = crawl(payload)
    with ThreadPoolExecutor(max_workers=3) as executor:
        summary_future = executor.submit(summary, crawled) if summary else None
        extraction_future = executor.submit(extraction, crawled)
        metadata_future = executor.submit(metadata, crawled)
        result = {
            "summary": summary_future.result() if summary_future else None,
            "structured": extraction_future.result(),
            "metadata": metadata_future.result(),
        }
    return formatter(result)
