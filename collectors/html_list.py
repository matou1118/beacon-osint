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


def _html_has_content(html: str) -> bool:
    """页面里到底有没有内容。

    判据是"去标签后还有没有文字"，不是"够不够长"：WAF 拦截时浏览器返回的是
    `<html><head></head><body></body></html>`，去掉标签后什么都没有。
    早先用"文本 >50 字"的阈值，会把短小的合法页面也误判成空（测试就抓到了
    '<a href="/art/1.html">公告</a>' 这种 2 字链接页）。
    """
    stripped = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    return bool(re.sub(r"\s+", " ", stripped).strip())


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
        # 这几个是政务站首页最常见的入口，词序与上面的"互动交流"不同，
        # 不能只靠 marker 覆盖。注意只加"独立成标题"时才会命中整串，
        # 长公告标题里含"政民互动"不会被误伤（判断走的是 in 整串，需配合后缀剥壳）。
        "政民互动", "依申请公开", "信息公开制度", "政策直达", "统一身份认证",
        "统一社会信用代码", "跳转到", "正在跳转", "答复列表", "相关搜索",
        # 港澳台站点的繁体栏目名。原表全是简体，这些一直没被覆盖。
        # mo_gov 的 41 条兜底里就有大量"公共服務""公布告示"。
        "公布告示", "公共服務", "政府資訊", "新聞公報", "政策公報", "招標公告",
        "職位空缺", "表格下載", "網站導覽", "聯絡我們", "私隱政策", "使用條款",
        "市政府公署", "政府公報", "行政長官", "立法會", "新聞公報", "廉政公署",
    )
    # 平台首页/栏目入口。实测兜底 343 条里，抽样反复出现
    # 「陕西省外商投资公共信息服务平台」「湖北省科学技术奖励服务平台」
    # 「青海数智信访一网通服务平台」「业务综合服务平台」这类——它们是政务站的
    # 业务入口，不是公告。
    #
    # 判别不能靠"含平台/系统二字"：「广东省商务厅关于做好2026年粤贸全球
    # 广东商品境外展览平台相关工作的通知」也含"平台"，却是真公告。
    # 区别在于**整条标题是不是就一个机构/平台名**：平台页主体是名词性名称，
    # 公告的主体是"某机关"+"关于"+公文动词。所以用公文动词做否定判据。
    # 公文动词/文种词。标题里出现任何一个，说明这是一份真文件，不能按入口页处理。
    #
    # 这份清单是被两类误伤逼出来的：
    #   1. 只匹配"的公告"时，「…管理平台国产化适配改造（2026-2027年）中标公告」
    #      会因为含"管理平台"被判成入口页，实际它是中标结果公告；
    #   2. 「宁波?12345政务服务便民热线管理办法」含"政务服务"且长度恰好卡在
    #      阈值边界上，会被入口页规则误杀——管理办法/规定/条例是法规专名。
    # 港澳台站点的繁体栏目名（公共服務/公布告示）也在这里，不另开 marker。
    _NO_DOC_ACTION_WORDS = (
        "关于", "的通知", "的公告", "的通告", "的公示", "的批复", "的决定",
        "的函", "的意见", "的通报", "印发", "发布", "公布", "公开", "征集",
        "申报", "遴选", "评选", "认定", "核准", "备案",
        "中标", "成交", "废止", "失效", "撤回", "延长", "调整", "变更",
        "续期", "征订", "遴选结果", "结果", "目录", "名单", "通报", "预警",
        "提示", "警示",
        "管理办法", "暂行办法", "实施办法", "管理规定", "工作条例",
        "实施细则", "操作规程", "技术规范",
        "公布告示", "公共服務", "政府資訊", "新聞公報", "政策公報",
        "招標公告", "職位空缺", "表格下載", "網站導覽", "聯絡我們",
        "私隱政策", "使用條款",
    )
    SECTION_NO_DOC_ACTION = re.compile("|".join(map(re.escape, _NO_DOC_ACTION_WORDS)))
    # 入口页后缀。覆盖实测出现过的形态：服务平台、门户网��、微信公众账号、
    # 网上信访投诉平台、业务综合服务平台等。
    SECTION_ENTRY_TAIL = re.compile(
        r"(?:服务平台|管理平台|申报平台|监测平台|服务平台|一网通|服务门户"
        r"|门户网站|政府网��|公众账号|微信公众号|微博|短信平台"
        r"|申报系统|业务系统|办公系统|查询系统|管理系统|信息系统"
        r"|网上信访|信访投诉|投诉平台|热线|专栏|专题|资料库|数据库)"
    )
    # 平台入口标题的长度上限。实测这批入口页标题集中在 6-17 字
    # （陕西省外商投资公共信息服务平台 15、青海数智信访一网通服务平台 13、
    # 湖北省科学技术奖励服务平台 12），而 20 字以上基本都是真文件。
    SECTION_ENTRY_MAX_CHARS = 20
    SECTION_TITLE_EXACT = frozenset({"首页", "网站首页", "下载", "地图"})
    # 政务站的 <title> 常把站点名挂在后面："<正文标题> - 湖北省人民政府门户网站"。
    # 注意分隔符不只有下划线：实测湖北省政府用 " - "。
    # 关键：带这个后缀的**不全是栏目页**，真公告也带。实测同一站：
    #   栏目页  政民互动 - 湖北省人民政府门户网站
    #   真文件  湖北省人民政府关于印发《…十五五规划》的通知 - 湖北省人民政府门户网站
    # 所以后缀只用来切分出标题主体，判栏目与否只看主体。
    #
    # 写成"捕获组 + 锚定"而不是一整条正则去 sub，有两个踩过的坑：
    #   1. sub 会把整条吃掉。"政民互动 - 湖北省人民政府门户网站" 整串都符合后缀形状，
    #      sub 之后剩空串，主体为空就什么规则都触发不了，栏目页反而漏放。
    #   2. 后缀的站点名长度不能设下限。曾按 "站点名>=4 字" 写，结果
    #      "政民互动_天津政务网"（站点名只有"天津"2 字）剥不掉。
    # 主体用贪婪 .+ 让切分点落在**最后一个**分隔符上：标题内部也可能含 " - "。
    # 站点名后缀。机构类后缀必须带上：发改委那批站点名是
    # 「河南省发展和改革委员会」「陕西省发改委」，只认"政府网/门户网站"的话
    # 这批源的导航页（新闻中心/通知公告/发改要闻/省领导…）一个都剥不开。
    SITE_NAME_SUFFIX = re.compile(
        r"^(?P<core>.+)\s*[-–—_]\s*"
        r"(?P<site>.{2,32}?(?:门户网站|政务网站|人民政府|政府办公厅|政府网|政务网|信息网"
        r"|委员会|人民政府办公厅|办公室|局|委|厅|院|中心))$"
    )
    # 标题主体这么短，且标题带站点名后缀 —— 那它是门户的栏目名而不是公告。
    #
    # 只在"带后缀"时启用是关键，不能无条件用：项目已有契约
    # tests/test_collectors.py::test_real_announcements_are_not_rejected 明确要求
    # 放行"锦江区公告"(5 字)、"工作动态"(4 字)、"信息公开"(4 字)——真实公告
    # 标题确实可以很短。而门户页会把自己的名字挂在标题后面，"政民互动 - 湖北省
    # 人民政府门户网站"剥完只剩 4 字，这种才敢按长度判。
    # 阈值 8 来自拿库里 1762 条真实标题回归的分界：带后缀的栏目名最长 8 字。
    # 改动阈值或去掉后缀前提，必须重跑 scripts/guard_regression.py。
    SECTION_CORE_MAX_CHARS = 8

    def _strip_site_suffix(self, title: str) -> str:
        """剥掉尾部站点名，返回标题主体。切不出就原样返回。"""
        text = (title or "").strip()
        match = self.SITE_NAME_SUFFIX.match(text)
        if match and len(match.group("core").strip()) >= 2:
            return match.group("core").strip()
        return text

    def _is_section_page(self, title: str) -> bool:
        value = re.sub(r"[\s　_|-]+", "", title or "")
        if not value:
            return False
        if value in self.SECTION_TITLE_EXACT:
            return True
        if any(re.sub(r"[\s　_|-]+", "", marker) in value for marker in self.SECTION_TITLE_MARKERS):
            return True
        # 判标题主体（已剥掉站点名后缀）。不能反过来——" - 门户网站"后缀真公告也有，
        # 直接按后缀拦会把湖北省政府全站真文件一起杀掉。
        stripped = self._strip_site_suffix(title)
        if stripped != (title or "").strip():
            core = re.sub(r"[\s　_|-]+", "", stripped)
            if core and len(core) <= self.SECTION_CORE_MAX_CHARS:
                return True
        core_plain = re.sub(r"[\s　_|-]+", "", stripped)
        # 平台名通常带这些后缀，本身就是"一个系统/一个入口"的标志。
        # 必须整条标题就是个平台名，不能"含平台词就算"——否则会杀掉
        # 「宁波"12345"政务服务便民热线管理办法」这种 5419 字的真文件。
        #
        # 长度分档来自实测：真文件标题即使短也带公文动词（已在下面排除），
        # 而平台页标题是纯名词性名称，长度集中在 6-17 字。20 字以上基本
        # 都是真文件，不用再靠长度赌。
        if self.SECTION_ENTRY_TAIL.search(stripped) and not self.SECTION_NO_DOC_ACTION.search(stripped):
            # 唯一实测到的漏网：比选/招标/采购类——标题里是"项目比选公告"，
            # 既不含公文动词（不是"…的公告"），又含"专题""服务"这类入口页词。
            if not re.search(r"比选|招标|采购|磋商|询价|竞谈", title or ""):
                if len(core_plain) <= self.SECTION_ENTRY_MAX_CHARS:
                    return True
        # "某某厅 某某平台" 这种两段式（中间空格或下划线分隔）也是入口页
        if re.match(r"^[^，,。]{4,14}厅\s{1,2}[^，,。]{2,20}(?:平台|系统|门户)$", title or ""):
            return True
        # "某某厅"/"某某局"/"某某政府"/"某某委员会" on its own names an organisation
        # page. 实测 sn_fgw 因此漏进过「陕西省发展和改革委员会」「陕西省发改委」两条，
        # 前者 12 字超过了带后缀时的长度阈值，后者没带后缀，只能靠这条兜。
        # "部" is excluded: a ministry name is often the subject of a real notice
        # (e.g. "生态环境部关于召回...")。
        return len(value) <= 24 and value.endswith(
            ("厅", "局", "政府", "学院", "中心", "委员会", "委", "办公厅", "办公室"))

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
                rendered_any = False
                for rendered in self._render_archive(self.list_url):
                    if not _html_has_content(rendered):
                        continue
                    rendered_any = True
                    links.extend(self._extract_links(rendered, base_url))
                # An empty <body> means the browser loaded nothing usable: typically
                # HTTP 412 anti-bot (chengdu.gov.cn does exactly this) rather than a
                # column with no announcements. Staying silent here reported
                # "0 records, no reason" and made blocked sites look empty, so say so.
                if not rendered_any:
                    self._note(
                        "render_empty_body",
                        "browser returned an empty body (likely anti-bot/WAF, not an "
                        f"empty column): {self.list_url}",
                    )
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
