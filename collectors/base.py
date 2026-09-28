from __future__ import annotations

import hashlib
import json
import re
import time
import warnings
from abc import ABC, abstractmethod
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import httpx
except ImportError:
    httpx = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import trafilatura

    _TRAFILATURA_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - 取决于环境
    # 正文抽取会静默退化到 soup.get_text()，也就是把导航/页眉/页脚一起存进
    # content。实测 trafilatura 因 lxml.html.clean 拆包而 import 失败过一次，
    # 结果整库 10% 的记录被站点框架文字污染（门户页 12%、摘要超长 22%、
    # 归类落到"其他" 95%），而没有任何一行日志提示。
    # 所以这里必须出声，并且有测试盯着它不许再退化。
    trafilatura = None
    _TRAFILATURA_IMPORT_ERROR = exc
    warnings.warn(
        "trafilatura 不可用，正文抽取将退化为整页 get_text()（会带入站点框架文字）。"
        f"原因：{exc}。修复：pip install lxml_html_clean",
        RuntimeWarning,
        stacklevel=2,
    )

try:
    from trafilatura.deduplication import content_fingerprint as trafilatura_content_fingerprint
except ImportError:  # pragma: no cover - 取决于环境
    trafilatura_content_fingerprint = None


def optional_deps_health() -> dict[str, str]:
    """可选依赖的降级情况，给体检/工作台用。空字典表示都正常。"""
    health: dict[str, str] = {}
    if trafilatura is None:
        health["trafilatura"] = str(_TRAFILATURA_IMPORT_ERROR)
    if BeautifulSoup is None:
        health["beautifulsoup4"] = "not installed"
    return health


class BaseCollector(ABC):
    min_interval_seconds = 3.0

    def __init__(
        self,
        source: Mapping[str, Any],
        client: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = dict(source)
        self.source_id = str(self.source.get("id", ""))
        self.source_name = str(self.source.get("name", ""))
        self.domain = str(self.source.get("domain", ""))
        self.list_url = str(self.source.get("url", ""))
        # 库里已有的 source_url。列表页会把这些链接再报一遍，但详情页我们早就
        # 存过了——再抓一遍只是让数据库去重，然后把绝大部分抓取预算花在已知内容上。
        # 实测 42,491 次抓取里只有 1,854 条是新的（重复处理率 96%）。
        # 成熟的抓取器都做这件事：Conditional GET / RSS 的 lastBuildDate 判断。
        # 用 set 传入而不是让采集器自己查库，是为了让采集层保持无数据库依赖。
        self.known_urls = source.get("known_urls") or frozenset()
        # 本次因为"已知"而跳过的链接数，供日志/统计用
        self.skipped_known = 0
        self.user_agent = str(self.source.get("user_agent", "BeaconOSINT/0.1"))
        configured_interval = float(self.source.get("request_interval_seconds", self.min_interval_seconds))
        self.interval_seconds = max(self.min_interval_seconds, configured_interval)
        self.clock = clock
        self.sleep = sleep
        self._last_request_at: float | None = None
        # Optional wall-clock budget for one collect() run, taken from the source
        # dict so callers can set it without a new constructor argument. A source
        # that runs out returns what it already has instead of being discarded.
        budget = self.source.get("deadline_seconds")
        try:
            self._deadline: float | None = self.clock() + float(budget) if budget else None
        except (TypeError, ValueError):
            self._deadline = None
        self._robots_cache: dict[str, tuple[bool, str | None]] = {}
        self._owns_client = client is None
        self.client = client
        if self.client is None and httpx is not None:
            self.client = httpx.Client(
                follow_redirects=True,
                timeout=float(self.source.get("timeout_seconds", 30)),
                headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            )
        self.allowed_domains = self._build_allowed_domains()

    def _build_allowed_domains(self) -> set[str]:
        domains = set()
        if self.domain:
            domains.add(self.domain.lower().lstrip("."))
        for value in self.source.get("allowed_domains", []):
            domains.add(str(value).lower().lstrip("."))
        if not domains and self.list_url:
            host = urlparse(self.list_url).hostname
            if host:
                domains.add(host.lower())
        return domains

    def _is_allowed_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == domain or host.endswith("." + domain) for domain in self.allowed_domains)

    def _wait_for_rate_limit(self) -> None:
        now = self.clock()
        if self._last_request_at is not None:
            remaining = self.interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.clock()

    def _robots_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    def _request(
        self,
        url: str,
        check_robots: bool = True,
        allowed_statuses: set[int] | None = None,
    ) -> Any:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        if check_robots and self.source.get("check_robots", True):
            self._assert_robots_allows(url)
        if self.client is None:
            raise RuntimeError("httpx is required; install requirements.txt")
        self._wait_for_rate_limit()
        response = self.client.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        status = int(getattr(response, "status_code", 200))
        # WAF/anti-bot: try stealth fallback for 412/403
        if status in (403, 412) and self.source.get("stealth_fallback", True):
            stealth_resp = self._request_stealth(url)
            if stealth_resp is not None:
                return stealth_resp
        if status >= 400 and status not in (allowed_statuses or set()):
            raise RuntimeError(f"HTTP {status} for {url}")
        final_url = str(getattr(response, "url", url))
        if not self._is_allowed_url(final_url):
            raise PermissionError(f"Redirect outside source allowlist: {final_url}")
        if check_robots and self.source.get("check_robots", True) and final_url != url:
            self._assert_robots_allows(final_url)
        return response

    def _request_stealth(self, url: str) -> Any | None:
        """Try cloudscraper and curl-cffi to bypass WAF/anti-bot (HTTP 412/403).

        Returns an httpx-like response on success, or None if both fail.
        """
        try:
            from .stealth_fetch import fetch_stealth
            timeout = int(self.source.get("timeout_seconds", 30))
            resp = fetch_stealth(url, self.user_agent, timeout)
            if resp is not None:
                final_url = str(getattr(resp, "url", url))
                if not self._is_allowed_url(final_url):
                    return None
                return resp
        except Exception:
            pass
        return None

    def _assert_robots_allows(self, url: str) -> None:
        robots_url = self._robots_url(url)
        origin = f"{urlparse(robots_url).scheme}://{urlparse(robots_url).netloc}"
        if origin in self._robots_cache:
            allowed, reason = self._robots_cache[origin]
            if not allowed:
                raise PermissionError(reason or f"robots.txt disallows {url}")
            return
        response = self._request(
            robots_url,
            check_robots=False,
            allowed_statuses={401, 403, 404, 410, 412},
        )
        status = int(getattr(response, "status_code", 200))
        if status in (404, 410, 412):
            # 404/410: no robots.txt → allowed.
            # 412: WAF blocked robots.txt → cannot determine, assume allowed.
            self._robots_cache[origin] = (True, None)
            return
        if status in (401, 403):
            # WAF may return 403 for robots.txt too; try stealth before giving up.
            if self.source.get("stealth_fallback", True):
                stealth_resp = self._request_stealth(robots_url)
                if stealth_resp is not None and stealth_resp.status_code < 400:
                    text = str(getattr(stealth_resp, "text", ""))
                    if not text.strip():
                        self._robots_cache[origin] = (True, None)
                        return
                    parser = RobotFileParser(robots_url)
                    parser.parse(text.splitlines())
                    allowed = parser.can_fetch(self.user_agent, url)
                    self._robots_cache[origin] = (allowed, None if allowed else f"robots.txt disallows {url}")
                    if not allowed:
                        raise PermissionError(self._robots_cache[origin][1])
                    return
            self._robots_cache[origin] = (False, f"robots.txt access denied: {origin}")
            raise PermissionError(self._robots_cache[origin][1])
        if status >= 400:
            self._robots_cache[origin] = (False, f"robots.txt unavailable: {origin}")
            raise PermissionError(self._robots_cache[origin][1])
        text = str(getattr(response, "text", ""))
        if not text.strip():
            self._robots_cache[origin] = (True, None)
            return
        parser = RobotFileParser(robots_url)
        parser.parse(text.splitlines())
        allowed = parser.can_fetch(self.user_agent, url)
        self._robots_cache[origin] = (allowed, None if allowed else f"robots.txt disallows {url}")
        if not allowed:
            raise PermissionError(self._robots_cache[origin][1])

    def _forge_rss_endpoint(self) -> str | None:
        config = self.source.get("forge_rss", {})
        if not config.get("enabled", False):
            return None
        if config.get("bypass_antibot") or config.get("social_media") or config.get("login_state"):
            raise PermissionError("ForgeRSS bypass, social media and login features are disabled")
        endpoint = config.get("endpoint")
        if not endpoint:
            raise ValueError("forge_rss.endpoint is required when enabled")
        return str(endpoint)

    def fetch_rss_entries(self) -> list[Any]:
        feed_url = self.source.get("rss_url") or self._forge_rss_endpoint()
        if not feed_url:
            return []
        if feedparser is None:
            raise RuntimeError("feedparser is required for RSS sources; install requirements.txt")
        response = self._request(str(feed_url), check_robots=bool(self.source.get("rss_check_robots", True)))
        content = getattr(response, "content", None)
        parsed = feedparser.parse(content if content is not None else str(getattr(response, "text", "")))
        return list(getattr(parsed, "entries", []))

    def feed_urls(self) -> list[str]:
        urls = []
        for entry in self.fetch_rss_entries():
            link = entry.get("link") if hasattr(entry, "get") else None
            if link and self._is_allowed_url(str(link)):
                urls.append(str(link))
        return list(dict.fromkeys(urls))

    @staticmethod
    def _response_text(value: Any) -> str:
        if isinstance(value, bytes):
            # 先试 UTF-8，失败则用 chardet 检测
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    import chardet
                    detected = chardet.detect(value)
                    encoding = detected.get("encoding") or "utf-8"
                    return value.decode(encoding, errors="replace")
                except ImportError:
                    return value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            return value
        return str(getattr(value, "text", ""))

    @staticmethod
    def _soup(html: str) -> Any:
        if BeautifulSoup is None:
            return None
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _normalize_date(value: str | None) -> str | None:
        if not value:
            return None
        match = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", value)
        if not match:
            return None
        year, month, day = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None

    def _date_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        text = str(url)

        # "/2026-09/25/", "/2026/9/25/" - year, month, day.
        full = re.search(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|\.|-|_|$)", text)
        if full:
            return self._safe_date(int(full.group(1)), int(full.group(2)), int(full.group(3)))

        # "/20260925" or "t20260925_1" - compact, common in ministry paths.
        compact = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text)
        if compact:
            return self._safe_date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))

        # "/2026-09/" - year and month only.
        partial = re.search(r"/(20\d{2})[/-](\d{1,2})(?:/|-|\.|$)", text)
        if partial:
            return self._safe_date(int(partial.group(1)), int(partial.group(2)), 1)

        # "/2026/" - year only. Coarse, but keeps the record on the time line
        # instead of leaving publish_date NULL, which hid 21 of 282 records.
        year_only = re.search(r"/(20\d{2})(?:/|$)", text)
        if year_only:
            return self._safe_date(int(year_only.group(1)), 1, 1)
        return None

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> str | None:
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None

    def parse(self, response_or_html: Any, url: str | None = None) -> dict[str, Any]:
        html = self._response_text(response_or_html)
        soup = self._soup(html)
        title = ""
        publish_date = self._date_from_url(url)
        content = ""
        if soup is not None:
            for meta in soup.find_all("meta"):
                key = str(meta.get("name") or meta.get("property") or "").lower()
                value = meta.get("content")
                if key in {"og:title", "articletitle", "contenttitle", "title"} and value:
                    title = self._clean(str(value))
                    if title:
                        break
            if not title:
                title_candidates = [self._clean(node.get_text(" ", strip=True)) for node in soup.find_all(["h1", "title"])]
                title_candidates = [candidate for candidate in title_candidates if candidate]
                if title_candidates:
                    title = max(title_candidates, key=len)
            for meta in soup.find_all("meta"):
                key = str(meta.get("name") or meta.get("property") or "").lower()
                value = meta.get("content")
                if key in {"article:published_time", "date", "pubdate", "publishdate", "dcterms.date", "publish_date"} and value and not publish_date:
                    publish_date = self._normalize_date(str(value))
                    if publish_date:
                        break
            for node in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                node.decompose()
        if not publish_date:
            for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.I):
                key_match = re.search(r"(?:name|property)\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.I)
                value_match = re.search(r"content\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.I)
                if not key_match or not value_match:
                    continue
                if key_match.group(1).lower() in {"article:published_time", "date", "pubdate", "publishdate", "dcterms.date", "publish_date"} and not publish_date:
                    publish_date = self._normalize_date(value_match.group(1))
                    if publish_date:
                        break
        if trafilatura is not None:
            try:
                content = str(trafilatura.extract(html, include_comments=False, include_tables=True, output_format="txt") or "")
            except TypeError:
                content = str(trafilatura.extract(html) or "")
        if not content and soup is not None:
            content = self._clean(soup.get_text(" ", strip=True))
        if not content:
            content = self._clean(re.sub(r"<[^>]+>", " ", html))
        if not publish_date:
            publish_date = self._normalize_date(content or html)
        # 在清洗前提取附件链接（PDF/Word/Excel）
        attachment_links: list[str] = []
        if soup is not None:
            for anchor in soup.find_all("a", href=True):
                href = anchor.get("href", "")
                if re.search(r"\.(?:pdf|doc|docx|xls|xlsx|wps|et)(?:[?#]|$)", href, re.I):
                    full_url = urljoin(url or self.list_url, href)
                    if self._is_allowed_url(full_url):
                        attachment_links.append(full_url)
        return {
            "title": title,
            "publish_date": publish_date,
            "content": content,
            "url": url or self.list_url,
            "attachment_links": list(dict.fromkeys(attachment_links))[:10],
        }

    @staticmethod
    def compute_content_hash(record: Mapping[str, Any]) -> str:
        payload = {
            "title": record.get("title", ""),
            "publish_date": record.get("publish_date", ""),
            "url": record.get("url") or record.get("source_url", ""),
            "content": record.get("content", ""),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def compute_content_fingerprint(record: Mapping[str, Any]) -> str:
        """纯正文的内容级指纹，用来判"这是同一份文件"。

        与 compute_content_hash 分工：那个把 url 也算进去，是身份标识（upsert 用），
        所以同一份文件挂在两个 URL 下时它必然不同，去不了重——实测全库只认出
        26 条重复。指纹只看正文（忽略空白差异），实测认出 64 条。
        trafilatura 不可用时退回归一化正文的 sha256，行为退化成旧水平而不是失效。
        """
        content = re.sub(r"\s+", "", str(record.get("content") or ""))
        if not content:
            return ""
        if trafilatura is not None:
            try:
                return str(trafilatura_content_fingerprint(content) or "") or hashlib.sha256(
                    content.encode("utf-8")).hexdigest()
            except Exception:  # noqa: BLE001 - 指纹只是优化，不该拖垮采集
                pass
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _retention_until(self, publish_date: str | None) -> str | None:
        return self._retention_until_with_days(publish_date, self.source.get("retention_days"))

    def _retention_until_with_days(self, publish_date: str | None, days: Any) -> str | None:
        if days is None or not publish_date:
            return None
        try:
            published = datetime.fromisoformat(str(publish_date)).date()
            retention_date = published + timedelta(days=int(days))
        except (TypeError, ValueError):
            return None
        return datetime.combine(retention_date, datetime_time.min, tzinfo=timezone.utc).isoformat()

    def normalize_record(self, parsed: Mapping[str, Any]) -> dict[str, Any]:
        record = {
            "source_id": self.source_id,
            "issuing_body": self.source_name,
            "source_type": self.source.get("type"),
            "source_priority": self.source.get("priority"),
            "cadence_tier": self.source.get("cadence_tier"),
            "focus_level": self.source.get("focus_level"),
            "project_role": self.source.get("project_role"),
            "project_tracking": bool(self.source.get("project_role") == "development_reform_commission"),
            "level": self.source.get("level", "national"),
            "gov_level": self.source.get("gov_level", "国家部委"),
            "jurisdiction_code": self.source.get("jurisdiction_code"),
            "source_url": parsed.get("url") or self.list_url,
            "url": parsed.get("url") or self.list_url,
            "title": parsed.get("title") or "",
            "date": parsed.get("publish_date"),
            "publish_date": parsed.get("publish_date"),
            "content": parsed.get("content", ""),
            "region_province": self.source.get("region_province"),
            "region_city": self.source.get("region_city"),
            "region_county": self.source.get("region_county"),
            "category_l1": self.source.get("category_l1"),
            "category_l2": self.source.get("category_l2"),
            "admission_sub_category": self.source.get("admission_sub_category"),
            "retention_class": self.source.get("retention_class", "active"),
            "retention_until": self._retention_until(parsed.get("publish_date")),
            "content_source": "static_html",
            "content_quality": {},
            "needs_review": 0,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "attachment_links": parsed.get("attachment_links") or [],
        }
        record["content_hash"] = self.compute_content_hash(record)
        record["content_fingerprint"] = self.compute_content_fingerprint(record)
        # Re-derive retention: the extractor may have downgraded this record to
        # transient after classification, and the expiry must match the class.
        retention_days = self.source.get("retention_days")
        if record.get("retention_class") == "transient":
            retention_days = self.source.get("transient_retention_days", 30)
        record["retention_until"] = self._retention_until_with_days(
            parsed.get("publish_date"), retention_days
        )
        return record

    def close(self) -> None:
        if self._owns_client and self.client is not None and hasattr(self.client, "close"):
            self.client.close()

    def out_of_time(self) -> bool:
        """True once the run's deadline_seconds budget is spent.

        Collectors check this between items so a slow source degrades to a partial
        result instead of being thrown away whole.
        """
        return self._deadline is not None and self.clock() >= self._deadline

    def __enter__(self) -> "BaseCollector":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    @abstractmethod
    def fetch_list(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def fetch_detail(self, url: str) -> dict[str, Any]:
        raise NotImplementedError
