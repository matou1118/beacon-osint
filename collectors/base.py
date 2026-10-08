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


# 句末标点：判断「这是正文还是导航」的核心信号。
_SENT_MARKS = re.compile(r"[。！？；]")


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
                # ★ 不读系统代理。
                #
                # 2026-10-03 实测：重启后 Windows 注册表里的系统代理
                # （127.0.0.1:31180）连不上，而 collectors/net.py 的「代理自检」
                # 正确地判定采集不能跑、整轮拒绝开跑 —— 于是采集彻底停摆。
                # 但那些都是政府公开站点，**直连完全正常**：
                # 实测 miit.gov.cn 0.3 秒、mee.gov.cn 0.1 秒拿到 200。
                #
                # 也就是说系统代理对采集既无必要又致命：它在浏览器里开着
                # （给开发者翻墙用），却会让采集跟着一起断。
                # ``trust_env=False`` 让 httpx 忽略 HTTP_PROXY/HTTPS_PROXY
                # 和注册表代理设置，采集只走直连。
                #
                # 保留 net.py 的代理自检仍有意义：它现在的作用变成「告知」
                # 而不是「拦截」——真需要走代理的部署改这里加回去即可。
                trust_env=False,
            )
        self.allowed_domains = self._build_allowed_domains()


    # 正文容器 class 名白名单。只作为快速命中项，不作唯一依据
    # （真正兜底的是 _auto_locate_body）。
    _FALLBACK_CONTAINERS = (
        "show", "show_center", "article", "content", "detail", "zoom",
        "TRS_Editor", "TRS_UEDITOR", "xl_content", "newsContent",
        "conTxt", "article-content", "pages_content", "b-detail",
    )

    @staticmethod
    def _body_looks_wrong(text: str, title: str) -> bool:
        """抽出来的正文是不是「其实抓到的是导航/侧栏」。

        判据两条，都是从实测来的：

        * 正文里找不到标题的任何一段——标题就在页面上，正文却完全不含标题
          片段，说明抽的根本不是正文区。
        * 几乎没有句读——列表页导航是词条拼的，正文有句子。
        """
        body = (text or "").strip()
        if not body:
            return True
        def _norm(text: str) -> str:
            return re.sub(r"\s+", "", text or "")

        # 标题和正文里的空格/全半角经常不一致（"关于 2025年" vs
        # "关于2025年"），所以两边都去空白再比；仍找不到就取标题里
        # 最长的一段连续汉字来比，避免被标点切碎。
        # ── 判据一：句读 ──────────────────────────────────────────
        # 导航/侧栏是词条拼的，正文有句子。这条**不依赖标题**。
        if len(body) >= 120 and not _SENT_MARKS.search(body):
            return True
        # 很短的正文（<120字）没资格判错：短公告本来就可能只有一两句话。
        if len(body) < 120:
            return False

        # ── 判据二：标题片段 ─────────────────────────────────────
        # 只有正文够长时才用这条，因为它依赖标题出现在正文里。
        #
        # 实测踩过：trafilatura 的 txt 模式**不把 <h1> 算进正文**，
        # 所以干净的结果里根本没有标题，而「正文必须含标题」会把
        # 它误判成抽到导航（test_content_excludes_site_chrome 现场抓到）。
        # 于是这条降级成「有标题才查，查不到不算错」，只作为
        # 长正文下的加强判据，不作为独立否决项。
        head = _norm(title)
        if head and len(head) >= 6:
            body_n = _norm(body)
            if head[:10] in body_n:
                return False                      # 明确是正文
            runs = re.findall(r"[\u4e00-\u9fa5]{6,}", title or "")
            runs.sort(key=len, reverse=True)
            hit = any(r in body_n for r in runs[:2])
            if not hit and len(body) >= 400:
                # 长到 400 字以上、标题片段一处都对不上，才判错。
                # 300 字左右的短公告不适用——它们可能只复述标题。
                return True
        return False

    def _extract_body(self, html: str, title: str = "") -> str:
        """抽正文，**并校验结果**，不对就换策略重试。

        2026-10-03 实测（sd_rst 一轮 598 条里 350 条正文是导航）：
        trafilatura 在这些页面上选错了 DOM 区域——抽到了 483 字符的
        「索引 服务 日一二三四五六最新发布」日历组件，而真正的文章在
        `<div class="show">` 里（页面本身完好：55KB、正文齐全、HTTP 200）。

        为什么它会选错：`show` 这种 class 名不含 content/article/detail
        之类常见词，正文定位的启发式打分输了，日历组件反而排在前面。

        关键是这个失败**完全静默**：页面能抓、标题能抽、日期能抽，
        逐源结果记为 `ok`，正文却是导航。所以必须拿标题去校验抽取结果，
        不合格就换策略，而不是直接采信。

        重试策略（按代价从低到高）：
          1. 带表格的 markdown 模式
          2. 关掉 favor_precision / favor_recall 之外的不同参数组合
          3. 自己找白名单里的正文容器（_FALLBACK_CONTAINERS）
        """
        if trafilatura is None:
            return ""
        attempts: list[dict[str, object]] = [
            {"include_comments": False, "include_tables": True,
             "output_format": "txt"},
            {"include_comments": False, "include_tables": False,
             "output_format": "txt"},
            {"include_comments": False, "include_tables": True,
             "output_format": "markdown", "favor_recall": True},
        ]
        best = ""
        for kwargs in attempts:
            try:
                out = str(trafilatura.extract(html, **kwargs) or "")
            except TypeError:
                try:
                    out = str(trafilatura.extract(html) or "")
                except Exception:                       # noqa: BLE001
                    continue
            except Exception:                           # noqa: BLE001
                continue
            if len(out) > len(best):
                best = out
            if not self._body_looks_wrong(out, title):
                return out

        # 三次都不合格 -> 自己找容器（此时 best 多半是导航，宁可不要）
        container = self._find_body_container(html)
        if container and not self._body_looks_wrong(container, title):
            return container
        # 白名单没命中（或命中了但校验不过）-> 自动定位。
        #
        # 硬编码 class 名覆盖不全：2026-10-03 实测各站正文容器叫 show / art /
        # TRS_Editor / warp_list 各不相同，而 sc_jx_gov、sc_xz_gov、sc_gs_gov
        # 抓到的是全站导航条，它们的容器 class 压根不在白名单里。
        # 「句读密度高 + 链接占比低」是结构特征，与 class 无关。
        auto = self._auto_locate_body(html)
        if auto and not self._body_looks_wrong(auto, title):
            return auto
        # 容器也没通过校验。
        #
        # **不要退回 best。** best 是所有尝试里最长的那份，而长度大不等于
        # 是正文——2026-10-03 实测：同一份 html 上，trafilatura 的
        # favor_recall 档（301 字符）比干净档（293 字符）更长，却多带了页脚
        # 「版权所有 备案号 浏览次数」。返回 best 就等于把页脚放回去，直接打破
        # test_content_excludes_site_chrome_when_trafilatura_available。
        #
        # 三档全不过时说明这页正文本来就难定位，正确做法是让上层升级到渲染级，
        # 而不是硬塞一份已知混着 chrome 的内容。
        if not self._body_looks_wrong(best, title):
            return best
        return ""

    # 块级候选容器：正文几乎总在这些标签里
    _BODY_TAGS = ("div", "article", "section", "td")

    def _auto_locate_body(self, html: str) -> str:
        """**自动**定位正文块，不靠 class 名硬编码。

        为什么不用 class 白名单：2026-10-03 实测各站的正文容器 class 五花八门
        （``show`` / ``art`` / ``TRS_Editor`` / ``warp_list``），硬编码既覆盖不全
        又会漏掉新站。而「正文」有两个稳定的**结构**特征，跟 class 无关：

        * **句读密度高**——正文是句子（。！？；），导航是词条拼接
        * **链接占比低**——正文里的文字不是链接；导航条整条都是 ``<a>``

        实测分离度很干净：

            sc_jx_gov  正文 2001 字 句读 15.99/千字 链接占比 0%
            sc_xz_gov  正文 3658 字 句读 11.48/千字 链接占比 0%
            （而导航条几乎 100% 是链接，句读接近 0）

        打分 = 句读密度为主、链接占比为惩罚项，再按长度微调。取最高分那个。
        白名单只当**加速**用（命中就直接返回），不作为唯一依据。
        """
        if BeautifulSoup is None:
            return ""
        try:
            document = BeautifulSoup(html or "", "html.parser")
        except Exception:                                 # noqa: BLE001
            return ""
        for tag in document(["script", "style", "nav", "footer", "header",
                             "noscript"]):
            tag.decompose()

        best_score = 0.0
        best_text = ""
        for node in document.find_all(self._BODY_TAGS):
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            size = len(text)
            if size < 150:
                continue
            marks = len(_SENT_MARKS.findall(text))
            if marks == 0:
                continue                      # 没有句读的块不可能是正文
            density = marks * 1000.0 / size
            anchors = node.find_all("a")
            link_chars = sum(len(a.get_text(strip=True)) for a in anchors)
            link_ratio = link_chars / float(size)
            # 句读密度是主项；链接占比 >30% 说明这整块是导航，扣得最狠。
            score = density * (1.0 - min(link_ratio, 0.9))
            # 极短的块（<400字）即便句读很高也可能只是摘要，略微压一下，
            # 让同分时更长的那块胜出。
            if size < 400:
                score *= 0.9
            if score > best_score:
                best_score = score
                best_text = text
        return best_text

    def _find_body_container(self, html: str) -> str:
        """按class 白名单找最长的正文容器，取其文本。soup 不可用时返回空串。"""
        if BeautifulSoup is None:
            return ""
        try:
            document = BeautifulSoup(html or "", "html.parser")
        except Exception:                               # noqa: BLE001
            return ""
        for tag in document(["script", "style", "nav", "footer", "header",
                             "noscript"]):
            tag.decompose()
        best = ""
        for name in self._FALLBACK_CONTAINERS:
            for node in document.find_all(class_=lambda c: c and name in
                                          " ".join(c if isinstance(c, list) else [c])):
                text = self._clean(node.get_text(" ", strip=True))
                if len(text) > len(best):
                    best = text
        return best

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
            content = self._extract_body(html, title)
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
