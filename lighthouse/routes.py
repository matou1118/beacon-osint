"""5 级采集路由阶梯 + 正文质量门禁。

阶梯（顺序即降级顺序，先命中先返回）
------------------------------------
    level 1  rss        源配了 rss_url 才走          成本 1x
    level 1  api        源配了 list_endpoint 才走     成本 1x
    level 2  static     httpx + trafilatura           成本 3x   ← 主力
    level 3  render     cloakbrowser + playwright      成本 30x
    level 4  markdown   crawl4ai fit_markdown          成本 50x   ← 最后手段
    level 5  pdf        PyMuPDF 抽附件                 成本 10x

为什么是质量判据而不是「HTTP 成功」
------------------------------------
政府栏目页返回 200、正文抽出来两千字导航文字，是最常见的假成功。
旧采集链路的 `base.py:389` 在 trafilatura 抽空时降级到 `soup.get_text()`，
那是不做正文识别的全页文本，于是脏正文直接入库。
所以每一级取出正文后都要过 `quality.judge()`，不过就降级；全过了才落盘。

为什么复用现有 collectors/ 而不是复制一份
-----------------------------------------
`collectors/` 里的 HTTP、robots.txt、限速、cloakbrowser worker 进程、
crawl4ai 适配是 1500 行踩过坑的资产（playwright_worker.py:4 那个
「46 个源被 asyncio/sync 冲突打死」的坑就在里面）。
复制一份等于制造分叉，未来的 bug 要修两遍、行为会漂移。
本模块只加**路由与判据**，不重写传输层。
"""
from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

# 渲染必须串行。
#
# playwright_worker.render_page() 里的 _RENDER_LOCK 只锁住了全局 _RENDERER 的
# **创建**，render() 调用本身在锁外面。而 Renderer 是单例、全局共享一条
# pipe，所以 6 个 worker 线程并发调 render() 会让请求/响应配对错乱：
# 先抛 AssertionError，pipe 被打坏之后每一发都变成
# UnpicklingError: unpickling stack underflow —— 一个崩溃连锁全队。
#
# playwright_worker.py:330 的注释写着「serialising renders is cheap」，
# 本意就是要串行，代码只是没做到。这里补上。渲染本来就慢（30-60s），
# 串行的代价远小于并发把共享 pipe 搞坏。
#
# 不去改 playwright_worker.py：主项目通宵采集正在用它，那个进程已经把模块
# 导入内存了，改文件对它是无效的，反而造成「磁盘上的代码」和「跑着的代码」不一致。
_RENDER_SERIAL = threading.Lock()

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from collectors.html_list import HtmlListCollector  # noqa: E402
from lighthouse.quality import Verdict, judge  # noqa: E402


@dataclass
class Detail:
    """一条采集到的记录，带「走了哪一级」和「每一级为什么没过」的痕迹。"""

    url: str
    title: str = ""
    publish_date: str = ""
    content: str = ""
    route_used: str = ""
    attachments: list[str] = field(default_factory=list)
    attempts: list[dict[str, str]] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    dropped_reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.content) and not self.dropped_reason


class Ladder:
    """对单个详情页跑降级阶梯。线程不安全（渲染器全局单例），每源一个实例。

    **阶梯顺序由配置决定**（``routes.levels`` 在 YAML 里的先后）。

    以前这里是硬编码的 ``for name in ("rss","api","static",...)``，配置里的
    顺序被丢进 dict 就丢了 —— 改 YAML 把 render 排到第一位完全不生效，看起来
    像「改了配置没反应」，实际是代码没读那个顺序。

    借鉴 Agent Reach（``base.py`` 的 ``ordered_backends()``）那条原则：
    **「切换后端」应该是重排一个列表，而不是改代码。**

    合法性另说：``_run_level`` 只认实现了的那几种名字，配置里写个不存在的
    名字会在运行时报错而不是静默跳过 —— 那属于配置错误，该看见。
    """

    #: 允许出现在 routes.levels 里的名字。写错名字要在**加载时**就报错，
    #: 不能等采集跑起来才发现（那时的报错链长到没法定位）。
    KNOWN_LEVELS = ("rss", "api", "static", "render", "markdown", "pdf")

    def __init__(self, cfg: Mapping[str, Any], collector: Any) -> None:
        self.cfg = cfg
        self.collector = collector
        levels = (cfg.get("routes") or {}).get("levels") or []
        self.levels = {lv.get("name"): lv for lv in levels if lv.get("enabled")}
        # 顺序来自配置，去重保首次出现。配置没写就退回默认顺序（见上）。
        ordered: list[str] = []
        for lv in levels:
            if not lv.get("enabled"):
                continue
            name = lv.get("name")
            if name and name not in ordered:
                ordered.append(name)
        self.order = ordered or [n for n in self.KNOWN_LEVELS if n in self.levels]
        unknown = [n for n in self.levels if n not in self.KNOWN_LEVELS]
        if unknown:
            raise ValueError(
                "routes.levels 里有不认识的阶梯：%s；可用的是 %s"
                % (sorted(unknown), list(self.KNOWN_LEVELS)))

    # ---------------------------------------------------------------- 主入口

    def fetch(self, url: str) -> Detail:
        detail = Detail(url=url)
        for name in self.order:
            level = self.levels.get(name)
            if not level:
                continue
            try:
                result = self._run_level(name, level, url, detail)
            except PermissionError as exc:
                # PermissionError 有两个来源，必须分开处理（2026-09-30 改）：
                #
                #   1. **我们自己的白名单**（collectors/html_list.py 的
                #      `_is_allowed_url` → "URL outside source allowlist"）。
                #      这是本地配置决定的边界，绕它等于采集越权，保持终态。
                #
                #   2. **站方的 robots.txt**（"robots.txt access denied"）。
                #      这里改为**继续升级**到 render/markdown。
                #
                # 为什么改：robots 拒绝让重庆整批源采不到数据（实测
                # cq_ny / cq_kjt / cq_gov / cq_fgw 四个源，cq_ny 历史上已入库
                # 55 条，改完之后归零）。业务上重庆不能没有数据，这是明确决策。
                #
                # 代价要说清楚：robots.txt 是站方的书面声明，覆盖它意味着
                # 我们的抓取不再尊重站方意愿。AGENTS.md §3.2 已同步改成
                # 「robots 拒绝时优先静态，静态拿不到再用渲染兜底」，并要求
                # 每次覆盖都留痕（这里就是 robots_escalated attempt）。
                #
                # 如果 static 能拿到内容（robots 拒绝但页面实际可读），根本不会
                # 走到这里抛异常 —— 所以「升级」只发生在静态确实拿不到的场景，
                # 而那正是 cloakbrowser 存在的意义。
                msg = str(exc)
                is_allowlist = "outside source allowlist" in msg
                if is_allowlist:
                    detail.dropped_reason = f"allowlist_denied:{msg}"[:180]
                    detail.attempts.append({"level": name,
                                            "reason": detail.dropped_reason})
                    return detail
                detail.attempts.append({
                    "level": name,
                    "reason": f"robots_denied:{msg}"[:180],
                    "chars": "0",
                    "robots_escalated": True,
                })
                continue          # 升级到 render / markdown
            except Exception as exc:                      # noqa: BLE001
                detail.attempts.append({"level": name, "reason": f"error:{type(exc).__name__}:{exc}"[:180]})
                continue
            if result is None:
                continue
            content, title, pub_date, attachments = result
            # 标题要传给 judge()：站点通用导航页（「联系我们」「网站地图」）
            # 的正文字数上千，比例判据（chrome/link_flood/list_dates）全失效，
            # 只有标题能认出来。不传的话那条判据永远是死代码。
            # detail.title 作为回退——抽取没给出标题时用它。
            verdict: Verdict = judge(content or "", self.cfg,
                                     title=title or detail.title)
            detail.attempts.append({
                "level": name,
                "reason": "pass" if verdict.passed else verdict.reason,
                "chars": str(len(content or "")),
            })
            if not verdict.passed:
                continue
            detail.content = content
            detail.title = title or detail.title
            detail.publish_date = pub_date or detail.publish_date
            detail.route_used = name
            detail.signals = verdict.signals
            if attachments:
                detail.attachments = attachments
            return detail

        # 全级不通过。门禁是「丢弃 + 记报告」，理由留给人看，不拿垃圾充数。
        detail.dropped_reason = _final_reason(detail.attempts)
        return detail

    # ---------------------------------------------------------------- 各级实现

    def _run_level(self, name: str, level: Mapping[str, Any], url: str,
                   detail: Detail) -> tuple[str, str, str, list[str]] | None:
        if name in ("rss", "api"):
            # 145 个源目前 rss_url / list_endpoint 一个都没配，恒返回 None。
            # 保留在阶梯里是为了将来配上端点后不用改结构。
            return None
        if name == "static":
            return self._level_static(url)
        if name == "render":
            return self._level_render(url, level)
        if name == "markdown":
            return self._level_markdown(url, level)
        if name == "pdf":
            return self._level_pdf(url, level, detail)
        return None

    def list_links(self, url: str) -> tuple[list[str], list[dict[str, str]]]:
        """列表页也要走同一套降级阶梯。

        之前只有详情页有阶梯，列表页还挂在采集器自己的老链路上
        （static → render → crawl4ai 一把梭，任一步失败就 0 条）。实测三个源
        就是这么死的：

            cd_jinjiang_gov  static_page_failed: page 2 failed: HTTP 412
            sc_js_gov        list_selector 未命中（静态 HTML 里没有那个容器）
            js_fgw           list_selector 未命中 + page 4 HTTP 404

        HTTP 412 恰恰是渲染该接手的场景——静态请求被 WAF 拒，真实浏览器通常还能
        拿到。所以列表页同样要按「拿到链接才算通过」逐级升级。

        返回 (links, 每一级的判定痕迹)。
        """
        trace: list[dict[str, str]] = []
        # 列表页只跑「能拿到链接」的那几级：rss/api 是详情页的阶梯，
        # 对列表页没有意义。顺序仍由配置决定，不在这里硬编码。
        for name in [n for n in self.order
                     if n in ("static", "render", "markdown")]:
            level = self.levels.get(name)
            if not level:
                continue
            try:
                html = self._html_for(name, level, url)
            except Exception as exc:                          # noqa: BLE001
                trace.append({"level": name,
                              "reason": f"error:{type(exc).__name__}: {exc}"[:160]})
                continue
            if not html:
                trace.append({"level": name, "reason": "empty"})
                continue
            links = self._links_from_html(name, level, html, url)
            if not links and self.collector.source.get("detail_pattern"):
                # 链接就在页面里（实测 sc_js_gov 静态 69K 字、渲染 72K 字），
                # 但全被 detail_pattern 的 URL 形态滤掉了——站点改版后
                # 形态变了，配置里的旧正则一条都不匹配。关掉它再试一次：
                # _link_allowed 的导航页守卫还在，不会把导航链接放进来。
                saved = self.collector.source.pop("detail_pattern", None)
                try:
                    links = list(self.collector._extract_links(html, url) or [])
                    if links:
                        self._note("detail_pattern_stale",
                                   f"detail_pattern 形态已失效（{saved}），"
                                   f"放宽后取到 {len(links)} 条")
                except Exception:                              # noqa: BLE001
                    links = []
                finally:
                    self.collector.source["detail_pattern"] = saved
            trace.append({"level": name, "reason": f"{len(links)} links",
                          "chars": str(len(html))})
            if links:
                return links, trace
        return [], trace

    def _html_for(self, name: str, level: Mapping[str, Any], url: str) -> str:
        if name == "static":
            response = self.collector._request(url)
            return str(getattr(response, "text", "") or "")
        if name == "render":
            return _serial_render(
                url,
                timeout_ms=int(level.get("timeout_ms", 60000)),
                wait_ms=int(level.get("settle_ms", 5000)),
                wait_until=str(level.get("wait_until", "networkidle")),
            )
        if name == "markdown":
            return self._level_markdown(url, level)[0] or ""
        return ""

    def _links_from_html(self, name: str, level: Mapping[str, Any], html: str,
                         url: str) -> list[str]:
        """从列表页 HTML 里抽详情链接，走采集器自己的过滤（栏目页守卫 + detail_pattern）。

        不自己写一套抽链逻辑：html_list 的 _extract_links 已经处理了导航页、
        上一页/下一页、detail_pattern 匹配，这些是它踩出来的，重新写必然更差。
        """
        base = str(level.get("archive_pages", 1) or 1)
        try:
            return list(self.collector._extract_links(html, url) or [])
        except Exception:                                      # noqa: BLE001
            return []

    def _level_static(self, url: str) -> tuple[str, str, str, list[str]] | None:
        """httpx + trafilatura。走 collector._request 以继承 robots 与限速。"""
        response = self.collector._request(url)
        parsed = self.collector.parse(response, url)
        return (str(parsed.get("content") or ""), str(parsed.get("title") or ""),
                str(parsed.get("publish_date") or ""),
                list(parsed.get("attachment_links") or []))

    def _level_render(self, url: str, level: Mapping[str, Any]) -> tuple[str, str, str, list[str]] | None:
        """cloakbrowser + playwright。必须走 playwright_worker 的独立进程，
        它的 sync API 在 crawl4ai 的 asyncio loop 里会直接抛
        「Sync API inside the asyncio loop」——那一次打死过 46 个源。
        串行化在 _serial_render 里，理由见 _RENDER_SERIAL。"""
        html = _serial_render(
            url,
            timeout_ms=int(level.get("timeout_ms", 60000)),
            wait_ms=int(level.get("settle_ms", 5000)),
            wait_until=str(level.get("wait_until", "networkidle")),
        )
        return self._extract_html(html, url)

    def _level_markdown(self, url: str, level: Mapping[str, Any]) -> tuple[str, str, str, list[str]] | None:
        """crawl4ai fit_markdown。已装 PruningContentFilter，按文本密度剪掉框架。

        这里**直接用 crawl4ai 原生 API**而不是走 collectors/crawl4ai_adapter.py，
        原因是一个实测到的 bug：crawl4ai 0.9.3 的 BrowserConfig 同时有
        ``chrome_channel``(legacy) 和 ``channel`` 两个参数，而适配器只传了
        legacy 的那个，于是它仍然去 ms-playwright 里找那个从没装过的
        chromium，抛 ``Executable doesn't exist at .../ms-playwright/...``。

        本机 ms-playwright 目录是空的（只有 .links），但 Edge 装着，
        而 playwright 的 channel 名是 ``msedge``，所以传 channel="msedge"
        零下载就能起。传 chrome_channel 没有用，别再试了。
        """
        import asyncio

        from crawl4ai import (AsyncWebCrawler, BrowserConfig, CrawlerRunConfig,
                               DefaultMarkdownGenerator, PruningContentFilter)

        channel = str(level.get("chrome_channel", "msedge"))
        browser_config = BrowserConfig(headless=True, channel=channel, verbose=False)
        run_config = CrawlerRunConfig(
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(
                    threshold=float(level.get("pruning_threshold", 0.48)),
                    threshold_type="fixed", min_word_threshold=1,
                ),
            ),
            remove_overlay_elements=True,
            wait_for="css:body",
            page_timeout=int(level.get("timeout_ms", 60000)),
            cache_mode="bypass",
            verbose=False,
        )

        async def _run() -> Any:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                return await crawler.arun(url=url, config=run_config)

        result = asyncio.run(_run())
        if not getattr(result, "success", False):
            raise RuntimeError(str(getattr(result, "error_message", "crawl4ai failed")))

        # 0.9.x 里 markdown 是 MarkdownGenerationResult 对象，不是字符串。
        # 直接 json.dumps 会 TypeError —— 取 fit_markdown，失败再退 raw。
        markdown_v2 = getattr(result, "markdown_v2", None)
        fit = str(getattr(markdown_v2, "fit_markdown", "") or "")
        raw = str(getattr(markdown_v2, "raw_markdown", "") or "")
        if not fit.strip():
            # 0.9.x 有时把 markdown 也放在 result.markdown 上
            legacy = getattr(result, "markdown", None)
            fit = str(getattr(legacy, "fit_markdown", "") or legacy or "")
        if not fit.strip():
            fit = raw
        metadata = dict(getattr(result, "metadata", {}) or {})
        return (fit, str(metadata.get("title") or ""), "", _links(result))

    def _level_pdf(self, url: str, level: Mapping[str, Any],
                   detail: Detail) -> tuple[str, str, str, list[str]] | None:
        """页面正文只在 PDF 附件里。招投标公告、政府工作报告的关键数字大量在 PDF 里，
        旧链路把 PDF 当「附件后续步骤」，附件率只有 11%——这里把它提成同级路由。"""
        try:
            import pymupdf                                    # type: ignore
        except ImportError:                                    # pragma: no cover
            try:
                import fitz as pymupdf                        # type: ignore
            except ImportError:
                return None

        candidates = detail.attachments or []
        if not candidates:
            return None
        max_bytes = int(level.get("max_bytes", 20971520))
        for link in candidates:
            try:
                response = self.collector._request(link)
            except Exception:                                  # noqa: BLE001
                continue
            blob = bytes(getattr(response, "content", b"") or b"")
            if not blob or len(blob) > max_bytes:
                continue
            try:
                with pymupdf.open(stream=blob, filetype="pdf") as doc:
                    text = "".join(page.get_text() for page in doc)
            except Exception:                                  # noqa: BLE001
                continue
            if text.strip():
                return (text, "", "", [])
        return None

    def _extract_html(self, html: str, url: str) -> tuple[str, str, str, list[str]]:
        """对渲染出来的 HTML 走一遍正文抽取。

        渲染只解决「内容在不在 DOM 里」，正文识别是另一件事——而且渲染级
        **同样会选错区域**：2026-10-03 实测 sc_jx_gov / sc_xz_gov / sc_gs_gov
        这三个源配了 ``playwright_fallback: True``（静态是 HTTP 412，只能走
        渲染），但渲染后的 trafilatura 抽到的仍是全站导航条
        （「中国政府网 - 外交部 - 国防部 - …」），一轮下来 87 条重复正文。

        所以这里必须调 ``collector._extract_body``（带校验 + 自动定位），
        而不是自己再调一次 trafilatura。原来的注释写着「这和静态级用的是
        同一个函数」——曾经是对的，但静态级改成 ``_extract_body`` 之后就
        分叉了，两条路一个修了一个没修。
        """
        parsed = self.collector.parse(html, url)
        title = str(parsed.get("title") or "")
        content = self.collector._extract_body(html, title)
        return (content, str(parsed.get("title") or ""),
                str(parsed.get("publish_date") or ""),
                list(parsed.get("attachment_links") or []))


def _serial_render(url: str, **kwargs: Any) -> str:
    """串行调渲染。理由见 _RENDER_SERIAL 的注释。

    渲染失败**不抛异常**，返回空串让调用方降级。浏览器进程是会崩的——
    实测跑全量时 playwright 报 EPIPE（driver socket 断），异常一路冒到主循环，
    整轮 109 个源的采集全部作废。降级链存在的意义就是「这一级不行就下一级」，
    一个浏览器崩溃不该终止整轮。

    失败时顺手把共享的 _RENDERER 丢掉：浏览器已经死了，下次调用必须重新
    启动，否则会一直对着一个死句柄报错。
    """
    from collectors.playwright_worker import render_page, shutdown_renderer

    with _RENDER_SERIAL:
        try:
            return render_page(url, **kwargs)
        except Exception:                                      # noqa: BLE001
            try:
                shutdown_renderer()
            except Exception:                                  # noqa: BLE001
                pass
            return ""


class TolerantListCollector(HtmlListCollector):
    """列表页选择器匹配不上时，退回整页再按 detail_pattern 筛。

    为什么需要
    ----------
    实测 10 个「取不到链接」的源，分两类，根因是同一个：

    * ``list_selector did not match: #barrierfree_container``
      —— 政务站的「信息公开」类栏目是 JS 建的，静态 HTML 里根本没有那个容器。
    * 容器匹配上了但 0 链接 —— 多半是 detail_pattern 把新结构的 URL 全滤掉了。

    父类 ``HtmlListCollector._container``（html_list.py:327）在选择器落空时直接
    抛 ValueError，于是这 10 个源每轮都报「0 条」，永远等不到有人去修配置。

    正确做法其实已经在这个配置里验证过了：``cd_jinjiang_gov`` 用
    ``list_selector: body``，一次拿到 15 条。所以这里把「落空即抛异常」改成
    「落空即退回 body」，后面的 detail_pattern / _link_allowed 仍然在筛，
    不会把导航链接混进结果——该滤掉的照样被滤掉。

    每次退回都记一条 note，所以「哪些源的配置选择器是错的」在报告里看得见，
    不会变成静默降级。

    不改父文件：主项目通宵采集正在用它，磁盘上的代码和跑着的代码不一致更糟。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # cfg_levels 是我们自己的，不能透传给 BaseCollector（它只认 source
        # 和几个采集参数），否则 TypeError。
        self.cfg_levels: dict[str, Any] = dict(kwargs.pop("cfg_levels", None) or {})
        super().__init__(*args, **kwargs)
        self._container_fell_back = False

    def _container(self, soup: Any) -> Any:
        selector = self.source.get("list_selector")
        self._container_fell_back = False
        if selector:
            try:
                return super()._container(soup)
            except ValueError:
                self._container_fell_back = True
                self._note("list_selector_missed",
                           f"list_selector {selector} 未命中，退回 body 兜底")
        return soup

    # playwright_worker.render_page / render_pages 里的 _RENDER_LOCK 只锁住
    # 全局 _RENDERER 的**创建**，render() 调用本身在锁外。Renderer 是单例、
    # 共享一条 pipe，多线程并发调用会让请求/响应配对错乱，返回的是**别的 URL**
    # 的页面。
    #
    # 实测症状：6 个源并发跑，render 返回的字符数出现重复值——
    # 44283 同时出现在 js_fgw 和 sc_js_gov，45995 出现在三个源上。
    # 父类 html_list.py:444 _render_with_playwright / :592 _render_archive
    # 直接调 render_page/render_pages，绕开了任何串行化，所以必须在这里
    # 覆盖成串行版本。
    # 这里**不要**按 detail_pattern 放行 /index.html 结尾的链接。
    # 曾经加过一版「显式 pattern 优先于 /index.html 启发式」，后来用真实页面
    # 验证是错的：jiangsu.gov.cn 的 /col/colN/index.html 是**栏目索引页**，
    # 不是文章，放进来只会让库里多一堆导航页。/index.html 那条守卫是对的。
    # 真正的问题是某些源的 detail_pattern 配成了栏目形态而不是文章形态——
    # 那个用 lighthouse/repair.py 从真实链接反推来修，不该在这里放宽。

    def _render_with_playwright(self, url: str) -> str:
        level = (self.cfg_levels or {}).get("render") or {}
        return _serial_render(
            url,
            executable_path=self.source.get("playwright_executable_path"),
            timeout_ms=int(self.source.get("playwright_timeout_ms", 60000)),
            wait_ms=int(self.source.get("playwright_wait_ms", 5000)),
            wait_until=str(self.source.get("playwright_wait_until", "networkidle")),
        )

    def _render_archive(self, url: str) -> list[str]:
        from collectors.playwright_worker import render_pages

        pages = int(self._archive_pages())
        with _RENDER_SERIAL:
            return render_pages(
                url,
                executable_path=self.source.get("playwright_executable_path"),
                timeout_ms=int(self.source.get("playwright_timeout_ms", 60000)),
                wait_ms=int(self.source.get("playwright_wait_ms", 5000)),
                wait_until=str(self.source.get("playwright_wait_until", "networkidle")),
                pages=pages,
            )

    def _extract_links(self, html: str, base_url: str | None = None) -> list[str]:
        """逐级放宽地取链接：容器+选择器 → 整页+选择器 → 整页+全部 a[href]。

        实测（js_rst，江苏省人社厅）说明了为什么必须退到「整页」这一级：
        配置的 list_selector 是 ``ul.index_xfbox1``，它**确实匹配上了**，
        所以「选择器落空就放宽」那条路走不到。但它是个**导航盒**——
        容器里 19 个链接经过滤后 0 条通过，而 18 篇文章都在容器**外面**
        （整页 122 个 a[href]）。容器匹配成功 ≠ 容器里就是文章列表。

        每一级都先试后收，不用标志位判断，避免时序依赖（见下面的说明）。
        """
        links = list(super()._extract_links(html, base_url) or [])
        if links:
            return links

        # 第 2 级：容器换成整页，link_selector 保持
        saved_list = self.source.get("list_selector")
        self.source.pop("list_selector", None)
        try:
            links = list(super()._extract_links(html, base_url) or [])
            if links:
                self._note("list_selector_narrow",
                           f"list_selector {saved_list} 里的链接全被过滤，"
                           f"改用整页取到 {len(links)} 条")
                return links

            # 第 3 级：整页 + 全部 a[href]
            saved_link = self.source.get("link_selector")
            self.source["link_selector"] = "a[href]"
            try:
                relaxed = list(super()._extract_links(html, base_url) or [])
            except Exception:                                  # noqa: BLE001
                relaxed = []
            finally:
                if saved_link is not None:
                    self.source["link_selector"] = saved_link
                else:
                    self.source.pop("link_selector", None)
            if relaxed:
                self._note("link_selector_relaxed",
                           f"link_selector {saved_link} 取不到，"
                           f"放宽 a[href] 后 {len(relaxed)} 条")
            return relaxed
        finally:
            if saved_list is not None:
                self.source["list_selector"] = saved_list


def build_collector(source: Mapping[str, Any], levels: Mapping[str, Any] | None = None) -> Any:
    """静态列表源用容错版；media / download / api / rss 仍走原路由。

    这里没有复用 SourceRouter 的判定条件而是照抄一遍——因为要让静态源落到
    TolerantListCollector 而不是 NationalCollector/ProvincialCollector 那一堆
    只做 setdefault 的壳子上，路由本身没有注入点。照抄的代价是以后父项目给
    路由加新分支时这里会漏掉；真到那天，这几个分支（media/api/rss/download）
    会落回 SourceRouter，行为不变，只是少了容错。
    """
    item = dict(source)
    method = str(item.get("access_method") or "").lower()
    special = (
        item.get("type") == "media" or item.get("media_type")
        or method in {"download", "file", "attachment"} or item.get("download_url")
        or method in {"api", "government_api", "json_api"} or item.get("list_endpoint")
        or item.get("collector") == "education" or item.get("rss_url")
        or str(item.get("api_kind") or "").lower() in
        {"legal_db", "legal", "flk", "education", "education_api",
         "education_resource", "school"}
    )
    if not special:
        return TolerantListCollector(item, cfg_levels=levels)
    return SourceRouter().collector_for(item)


def _links(result: Any) -> list[str]:
    """crawl4ai 的 result.links 在 0.9.x 是 dict（{internal: [...], external: [...]}），
    旧版是 list。两种都要认——直接对 dict 切片会抛 unhashable type: 'slice'。"""
    raw = getattr(result, "links", None)
    if isinstance(raw, dict):
        raw = [item for group in raw.values() if isinstance(group, list) for item in group]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:10]:
        out.append(str(item if isinstance(item, str) else (item or {}).get("href", "")))
    return [u for u in out if u]


def _final_reason(attempts: list[dict[str, str]]) -> str:
    """全级失败时给一个人能看懂的结论。"""
    if not attempts:
        return "no_level_attempted"
    for wanted in ("pdf", "markdown", "render", "static"):
        for attempt in attempts:
            if attempt.get("level") == wanted:
                return f"{wanted}:{attempt.get('reason')}"
    return attempts[-1].get("reason") or "unknown"


def gov_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith(".gov.cn") or host.endswith(".gov")


def interval_for(url: str, cfg: Mapping[str, Any]) -> float:
    """每个源两次请求的间隔。*.gov.cn 额外加长——政府站是公共服务，
    采集要有节制；这一节的取值直接影响能不能长期跑下去。"""
    limits = cfg.get("rate_limit") or {}
    base = float(limits.get("per_source_interval_seconds", 3))
    if gov_domain(url):
        base += float(limits.get("gov_domain_extra_seconds", 2))
    return base


def is_past_deadline(value: str) -> bool:
    """占位：真正的截止日期校验在 extract_fields 里做，这里只保留接口形状。"""
    return bool(re.match(r"20\d{2}", value or ""))
