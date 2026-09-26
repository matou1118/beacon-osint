from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from .base import BaseCollector
from .crawl4ai_adapter import crawl_and_extract_sync
from .playwright_worker import render_page, render_pages, shutdown_renderer  # shutdown_renderer: re-export

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Fallback when a source does not set render_mode. Kept as a constant so the
# self-improvement loop can flip the project-wide default in one place
# (config/defect_registry.yaml -> collection_yield_rate fixes).
DEFAULT_RENDER_MODE = "static"


class SectionLandingError(ValueError):
    """A site-chrome page, not an announcement.

    Distinct from a real failure: a list page legitimately links to "return to
    home" and section landings, and one of those must not mark the source broken.
    """


class HtmlListCollector(BaseCollector):
    # Section landing pages that live on the same domain and would otherwise be
    # mistaken for announcements when links are harvested from whole-page markdown.
    # These are site-chrome names only. Real column names such as tzgg (通知公告)
    # or gsgg (公示公告) are deliberately absent: they are legitimate parents of
    # announcement URLs, and filtering them would drop valid detail links.
    NAVIGATION_SEGMENTS = frozenset({
        "about", "aboutus", "contact", "copyright", "disclaimer", "feedback", "government",
        "help", "home", "index", "intro", "links", "login", "map", "navigation", "position",
        "privacy", "register", "search", "sitemap", "sitemapxml", "sjgk", "gk", "zcjd",
        "zwgk", "zcwj", "xxgk", "xxgkml", "zc", "hd", "lxwm", "wz",
    })

    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(source, **kwargs)
        self.errors: list[dict[str, str]] = []

    def _soup(self, html: str) -> Any:
        if BeautifulSoup is None:
            return None
        return BeautifulSoup(html, "html.parser")

    # Titles of site sections and chrome pages. URL path segments cannot catch
    # these because government sites abbreviate them (jgyg, ldxx, gzxx), so the
    # title is the only reliable signal.
    SECTION_TITLE_MARKERS = (
        "网站地图", "机构沿革", "机构职能", "机关简介", "机关处室", "政府领导",
        "政务公开", "互动交流", "智能问答", "新媒体矩阵", "长者专区", "无障碍",
        "网站首页", "版权所有", "联系我们", "网站声明", "常见问题", "访问帮助",
        "领导简介", "意见征集", "我要查", "旧版网站", "年度报表", "工作报表",
        # Portal furniture that carries no announcement. Measured on 282 stored
        # records: every undated one turned out to be a page of this kind
        # ("走进四川", "网站导航", "闽政通APP宣传页", "省政府令_…", "互动_…").
        "网站导航", "走进", "提建议", "宣传页", "APP", "邮件系统", "外网",
        "机构设置", "政府部门", "省级服务平台", "政策主题服务", "政务服务网",
        "信息公开指南", "政府信息公开指南", "网站声明", "访问指南",
    )
    SECTION_TITLE_EXACT = frozenset({"首页", "网站首页", "下载", "地图"})
    # A portal page often appends its own site name: "省政府令_河南省人民政府门户".
    SECTION_TITLE_TAIL = re.compile(r"[\u4e00-\u9fa5]{2,10}_[\u4e00-\u9fa5]{4,20}(?:门户网站|人民政府|政府网)$")

    def _is_section_page(self, title: str) -> bool:
        value = re.sub(r"[\s　_|-]+", "", title or "")
        if not value:
            return False
        if value in self.SECTION_TITLE_EXACT:
            return True
        if any(re.sub(r"[\s　_|-]+", "", marker) in value for marker in self.SECTION_TITLE_MARKERS):
            return True
        if self.SECTION_TITLE_TAIL.search(re.sub(r"[\s　|]+", "", title or "")):
            return True
        # "某某厅"/"某某局"/"某某政府" on its own names an organisation page.
        # "部" is excluded: a ministry name is often the subject of a real notice
        # (e.g. "生态环境部关于召回...").
        return len(value) <= 24 and value.endswith(("厅", "局", "政府", "学院", "中心"))

    def _navigation_like(self, url: str) -> bool:
        segments = [part for part in urlparse(url).path.lower().split("/") if part]
        if not segments:
            return True
        tail = segments[-1].split(".")[0]
        if tail in self.NAVIGATION_SEGMENTS:
            return True
        extra = self.source.get("navigation_segments") or []
        return bool(set(segments) & {str(segment).lower() for segment in extra})

    def _link_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or not self._is_allowed_url(url):
            return False
        if self._navigation_like(url):
            return False
        if path in {"", "/"} or path.endswith(("/index.htm", "/index.html")):
            return False
        if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".zip", ".rar")):
            return False
        pattern = self.source.get("detail_pattern")
        if pattern and not re.search(str(pattern), url):
            return False
        return True

    def _container(self, soup: Any) -> Any:
        selector = self.source.get("list_selector")
        if not selector:
            return soup
        container = soup.select_one(str(selector))
        if container is None:
            raise ValueError(f"list_selector did not match: {selector}")
        return container

    def _extract_links(self, html: str, base_url: str | None = None) -> list[str]:
        if BeautifulSoup is None:
            raise RuntimeError("beautifulsoup4 is required for HTML list parsing; install requirements.txt")
        soup = self._soup(html)
        container = self._container(soup)
        link_selector = str(self.source.get("link_selector", "a[href]"))
        try:
            anchors = container.select(link_selector)
        except Exception as exc:
            raise ValueError(f"invalid link_selector: {link_selector}") from exc
        base = base_url or self.list_url
        links = []
        for anchor in anchors:
            href = anchor.get("href") if hasattr(anchor, "get") else None
            if not href or not isinstance(href, str):
                continue
            href = href.strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(base, href)
            if self._link_allowed(url) and url.rstrip("/") != base.rstrip("/"):
                links.append(url)
        return list(dict.fromkeys(links))

    def _crawl4ai_enabled(self) -> bool:
        settings = self.source.get("crawl4ai", {})
        return self.source.get("render_mode") in {"crawl4ai", "static_then_crawl4ai"} or bool(settings.get("enabled", False))

    def _crawl4ai_result(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        if self.source.get("check_robots", True):
            self._assert_robots_allows(url)
        settings = dict(self.source.get("crawl4ai", {}))
        return crawl_and_extract_sync(url, settings)

    def _crawl4ai_links(self, result: Mapping[str, Any], base_url: str) -> list[str]:
        links = []
        structured = result.get("structured")
        if isinstance(structured, Mapping):
            structured = structured.get("items") or structured.get("data") or []
        if isinstance(structured, list):
            for item in structured:
                if not isinstance(item, Mapping):
                    continue
                href = item.get("链接") or item.get("link") or item.get("url")
                if href:
                    url = urljoin(base_url, str(href))
                    if self._link_allowed(url):
                        links.append(url)
        for item in result.get("links", []):
            href = item.get("href") or item.get("url") if isinstance(item, Mapping) else item
            if href:
                url = urljoin(base_url, str(href))
                if self._link_allowed(url):
                    links.append(url)
        markdown = str(result.get("markdown") or "")
        for href in re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", markdown):
            if self._link_allowed(href):
                links.append(href)
        return list(dict.fromkeys(links))

    def _crawl4ai_quality(self, result: Mapping[str, Any]) -> dict[str, Any]:
        fit = str(result.get("markdown") or "")
        raw = str(result.get("raw_markdown") or "")
        noise_markers = sum(fit.count(marker) for marker in ("登录", "注册", "首页", "导航", "广告", "版权"))
        return {
            "fit_markdown_length": len(fit),
            "raw_markdown_length": len(raw),
            "fit_ratio": round(len(fit) / len(raw), 4) if raw else 0.0,
            "noise_marker_count": noise_markers,
            "fit_markdown_fallback": bool((result.get("metadata") or {}).get("fit_markdown_fallback")),
            "usable": bool(fit.strip()) and len(fit) >= int(self.source.get("minimum_content_chars", 80)),
        }

    def _parse_crawl4ai_result(self, result: Mapping[str, Any], url: str) -> dict[str, Any]:
        markdown = str(result.get("markdown") or "")
        metadata = result.get("metadata") or {}
        title = metadata.get("title") or metadata.get("og_title")
        if not title:
            match = re.search(r"^#\s+(.+)$", markdown, flags=re.M)
            title = match.group(1).strip() if match else ""
        return {
            "title": str(title or "").strip(),
            "publish_date": metadata.get("publish_date") or metadata.get("date"),
            "content": markdown,
            "url": metadata.get("source_url") or metadata.get("url") or url,
        }

    def _playwright(self) -> Any:
        # Rendering moved to a worker process (see _render_with_playwright): the
        # sync API cannot start inside crawl4ai's asyncio loop.
        raise RuntimeError("Playwright now runs in a worker process; see render_page")

    def close(self) -> None:
        # The warm browser is shared by every source and released at process exit
        # (atexit in playwright_worker). Shutting it down here relaunched Chromium
        # on every collector.close() -- 52 launches per pass -- and, under
        # concurrency, killed the browser another source was driving. base.close()
        # releases the httpx client this collector owns, which this override used
        # to skip and so leaked one connection per source.
        super().close()

    def _render_with_playwright(self, url: str) -> str:
        """Fetch a JS-rendered page in a worker process.

        Rendering in-process mixed two event loops: crawl4ai_adapter calls
        asyncio.run(), and the sync Playwright API refuses to start once that
        loop has run ("Playwright Sync API inside the asyncio loop"). That
        failure hit 46 of 145 registered sources. A worker process keeps the
        loops separate and also isolates crashes from the collector.
        """
        if not self.source.get("playwright_fallback", False):
            raise ValueError("Playwright fallback is disabled for this source")
        return render_page(
            url,
            executable_path=self.source.get("playwright_executable_path"),
            timeout_ms=int(self.source.get("playwright_timeout_ms", 60000)),
            wait_ms=int(self.source.get("playwright_wait_ms", 5000)),
            wait_until=str(self.source.get("playwright_wait_until", "networkidle")),
            locale=str(self.source.get("playwright_locale", "zh-CN")),
        )

    def _archive_pages(self) -> int:
        """How many list pages to walk. 0 or 1 means the first page only."""
        try:
            return max(1, int(self.source.get("archive_pages", 1)))
        except (TypeError, ValueError):
            return 1

    def _static_archive_pages(self) -> list[str]:
        """Walk static archive pages when the site uses index_N.html style paging.

        archive_pages only reached the rendered path before, so static sources
        kept returning page 1 -- which is why the 1-10 year history came back
        empty. Static archives are plain links, so no browser is needed.
        """
        pages = self._archive_pages()
        if pages <= 1:
            return []
        collected: list[str] = []
        for index in range(1, pages + 1):
            url = self._page_url(index)
            if not url:
                continue
            try:
                response = self._request(url)
            except Exception as exc:  # noqa: BLE001 - a missing page is not fatal
                self._note("static_page_failed", f"page {index} failed: {exc}")
                break
            collected.append(self._response_text(response))
        return collected

    def _page_url(self, index: int) -> str | None:
        """Derive page N from the list URL. Supports the common static patterns."""
        if index <= 1:
            return None
        text = self.list_url
        # list.shtml / list_1.shtml / index.html / ?page=1
        for pattern, template in (
            (r"list_(\d+)\.s?html?$", "list_{n}.shtml"),
            (r"list\.s?html?$", "list_{n}.shtml"),
            (r"index\.s?html?$", "index_{n}.shtml"),
            (r"index_(\d+)\.s?html?$", "index_{n}.shtml"),
        ):
            if re.search(pattern, text):
                if re.search(r"list_\d+\.s?html?$", text) or re.search(r"index_\d+\.s?html?$", text):
                    return None  # zero-based family: guessed, leave it to the renderer
                return re.sub(pattern, template, text)
        if re.search(r"[?&]page=\d+", text):
            return re.sub(r"([?&]page=)\d+", rf"\g<1>{index}", text)
        return None

    def _render_archive(self, url: str) -> list[str]:
        pages = self._archive_pages()
        if pages <= 1:
            return [self._render_with_playwright(url)]
        return render_pages(
            url,
            executable_path=self.source.get("playwright_executable_path"),
            timeout_ms=int(self.source.get("playwright_timeout_ms", 60000)),
            wait_ms=int(self.source.get("playwright_wait_ms", 5000)),
            wait_until=str(self.source.get("playwright_wait_until", "networkidle")),
            locale=str(self.source.get("playwright_locale", "zh-CN")),
            pages=pages,
        )

    def _list_html(self) -> tuple[str, str]:
        response = self._request(self.list_url)
        return self._response_text(response), str(getattr(response, "url", self.list_url))

    def _static_quality_ok(self, parsed: Mapping[str, Any]) -> bool:
        content = str(parsed.get("content") or "")
        return bool(parsed.get("title")) and len(content) >= int(self.source.get("minimum_content_chars", 80))

    def fetch_list(self) -> list[str]:
        """List links for this source, trying several strategies in order.

        Sources are heterogeneous, so a single strategy leaves most of them
        empty: static HTML misses JS-built columns, rendering misses nothing but
        costs a browser launch, and crawl4ai is the last resort. Order is cheapest
        first and each step records why it did not work, so a failure is
        diagnosable instead of just "0 records".
        """
        feed_urls = self.feed_urls()
        if feed_urls:
            return feed_urls[: int(self.source.get("max_items", 20))]

        render_mode = self.source.get("render_mode", DEFAULT_RENDER_MODE)
        cap = int(self.source.get("max_items", 20))

        if render_mode == "crawl4ai":
            result = self._crawl4ai_result(self.list_url)
            links = self._crawl4ai_links(result, self.list_url)
            if links:
                return links[:cap]

        html, base_url = "", self.list_url
        try:
            html, base_url = self._list_html()
        except Exception as exc:
            if not self.source.get("playwright_fallback", False):
                # No fallback configured: a broken list page is a broken source,
                # and swallowing it would report "0 records" instead of the cause.
                raise
            self._note("static_list_failed", f"static list fetch failed: {exc}")

        links = self._extract_links(html, base_url) if html else []

        # Static archives first: cheaper than a browser, and it is the only path
        # that reaches 1-10 year history for sites that do not need rendering.
        if len(links) < cap:
            for page_html in self._static_archive_pages():
                links.extend(self._extract_links(page_html, base_url))

        # Still short: the column is JS-rendered or paginated behind a widget.
        if len(links) < cap and self.source.get("playwright_fallback", False):
            try:
                for rendered in self._render_archive(self.list_url):
                    links.extend(self._extract_links(rendered, base_url))
            except Exception as exc:
                self._note("archive_render_failed", f"archive render failed: {exc}")

        if not links and render_mode == "static_then_crawl4ai" and self._crawl4ai_enabled():
            result = self._crawl4ai_result(self.list_url)
            links = self._crawl4ai_links(result, base_url)
        return self._dedupe_links(links)[:cap]

    def _note(self, kind: str, message: str) -> None:
        if not any(item.get("error", "").startswith(message[:40]) for item in self.errors):
            self.errors.append({"url": self.list_url, "error": f"{kind}: {message}"})

    def _dedupe_links(self, links: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for link in links:
            if link not in seen:
                seen.add(link)
                out.append(link)
        return out

    def fetch_detail(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        render_mode = self.source.get("render_mode", DEFAULT_RENDER_MODE)
        if render_mode == "crawl4ai":
            result = self._crawl4ai_result(url)
            parsed = self._parse_crawl4ai_result(result, url)
        else:
            parsed = {}
            try:
                response = self._request(url)
                final_url = str(getattr(response, "url", url))
                parsed = self.parse(response, final_url)
            except Exception as exc:
                final_url = url
                if not self.source.get("playwright_fallback", False):
                    raise
                self.errors.append({"url": url, "error": f"static fetch failed, trying rendered page: {exc}"})
            if (not parsed.get("title") or not parsed.get("content")) and self.source.get("playwright_fallback", False):
                rendered = self._render_with_playwright(final_url)
                parsed = self.parse(rendered, final_url)
            if not self._static_quality_ok(parsed) and render_mode == "static_then_crawl4ai" and self._crawl4ai_enabled():
                result = self._crawl4ai_result(final_url)
                parsed = self._parse_crawl4ai_result(result, url)
        if not parsed.get("title") or not parsed.get("content"):
            raise ValueError(f"detail page has no usable title or content: {url}")
        if self._is_section_page(str(parsed.get("title") or "")):
            raise SectionLandingError(str(parsed.get("title")))
        record = self.normalize_record(parsed)
        if "result" in locals() and self._crawl4ai_enabled():
            quality = self._crawl4ai_quality(result)
            record["content_source"] = "crawl4ai_fit_markdown"
            record["content_quality"] = quality
            if not quality["usable"] or quality["fit_markdown_fallback"]:
                record["needs_review"] = 1
        return record

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Fetch details for the list links, trying past dead candidates.

        The first link on a gov list page is often "return to home" or a section
        landing, so probing with limit=1 made the whole source look broken. That
        misjudged 40 of 145 sources. Try several candidates before giving up and
        report the real reason: a landing page is skipped, not fatal.
        """
        records: list[dict[str, Any]] = []
        landing_titles: list[str] = []
        for url in self.fetch_list():
            if limit is not None and len(records) >= limit:
                break
            if self.out_of_time():
                # Budget spent: hand back what was collected instead of nothing.
                # The batch runner used to discard timed-out sources whole, which
                # is why a full pass returned 0 records for most sources.
                self.errors.append({
                    "url": url,
                    "error": f"deadline reached after {len(records)} records, returning partial result",
                })
                break
            try:
                records.append(self.fetch_detail(url))
            except SectionLandingError as exc:
                # Expected on list pages: the site chrome is not an announcement.
                landing_titles.append(str(exc))
                continue
            except Exception as exc:
                self.errors.append({"url": url, "error": str(exc)})
        if not records and landing_titles and not self.errors:
            self.errors.append({
                "url": self.list_url,
                "error": f"only landing pages found among {len(landing_titles)} candidates: "
                          f"{landing_titles[0][:80]}",
            })
        return records
