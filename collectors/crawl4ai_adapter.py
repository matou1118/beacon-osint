from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, JsonCssExtractionStrategy
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
except ImportError:
    AsyncWebCrawler = None
    BrowserConfig = None
    CrawlerRunConfig = None
    JsonCssExtractionStrategy = None
    PruningContentFilter = None
    DefaultMarkdownGenerator = None


def _decode_extracted(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return value


def build_crawler_config(options: Mapping[str, Any] | None = None) -> Any:
    if CrawlerRunConfig is None or PruningContentFilter is None or DefaultMarkdownGenerator is None:
        raise RuntimeError("crawl4ai is required for rendered pages; install requirements.txt")
    settings = dict(options or {})
    generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=float(settings.get("pruning_threshold", 0.48)),
            threshold_type=str(settings.get("threshold_type", "fixed")),
            min_word_threshold=int(settings.get("min_word_threshold", 1)),
        ),
        options={"ignore_links": False, "escape_html": True},
    )
    css_schema = settings.get("css_schema")
    extraction_strategy = JsonCssExtractionStrategy(css_schema) if css_schema and JsonCssExtractionStrategy is not None else None
    return CrawlerRunConfig(
        remove_overlay_elements=bool(settings.get("remove_overlay_elements", True)),
        markdown_generator=generator,
        extraction_strategy=extraction_strategy,
        wait_for=str(settings.get("wait_for", "css:body")),
        page_timeout=int(settings.get("page_timeout", 30000)),
        check_robots_txt=bool(settings.get("check_robots_txt", True)),
        cache_mode=str(settings.get("cache_mode", "bypass")),
        exclude_social_media_domains=list(settings.get("exclude_social_media_domains", [])),
        verbose=False,
    )


def build_browser_config(options: Mapping[str, Any] | None = None) -> Any:
    if BrowserConfig is None:
        raise RuntimeError("crawl4ai is required for rendered pages; install requirements.txt")
    settings = dict(options or {})
    return BrowserConfig(
        headless=bool(settings.get("headless", True)),
        chrome_channel=str(settings.get("browser_channel", "chromium")),
        user_agent=str(settings.get("user_agent", "BeaconOSINT/0.1")),
        enable_stealth=False,
        proxy=None,
        verbose=False,
    )


async def crawl_and_extract(url: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if AsyncWebCrawler is None:
        raise RuntimeError("crawl4ai is not installed; install requirements.txt")
    config = build_crawler_config(options)
    browser_config = build_browser_config(options)
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=config)
    if not getattr(result, "success", False):
        raise RuntimeError(str(getattr(result, "error_message", "Crawl4AI request failed")))
    markdown_v2 = getattr(result, "markdown_v2", None)
    fit_markdown = str(getattr(markdown_v2, "fit_markdown", "") or "")
    raw_markdown = str(getattr(markdown_v2, "raw_markdown", "") or getattr(result, "markdown", "") or "")
    fit_fallback = not bool(fit_markdown.strip()) and bool(raw_markdown.strip())
    if fit_fallback:
        fit_markdown = raw_markdown
    metadata = dict(getattr(result, "metadata", {}) or {})
    metadata["fit_markdown_fallback"] = fit_fallback
    links = list(getattr(result, "links", []) or [])
    return {
        "markdown": fit_markdown,
        "raw_markdown": raw_markdown,
        "structured": _decode_extracted(getattr(result, "extracted_content", None)),
        "metadata": metadata,
        "links": links,
    }


def crawl_and_extract_sync(url: str, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return asyncio.run(crawl_and_extract(url, options))
