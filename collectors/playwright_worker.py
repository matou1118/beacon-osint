"""Render JS pages in a worker process.

Kept out of process on purpose. The collector calls crawl4ai, which uses
asyncio.run(); Playwright's sync API then refuses to start ("Sync API inside the
asyncio loop"). That single conflict blocked 46 of the 145 registered sources.

A worker process separates the two event loops, isolates crashes, and lets a
warm browser be reused across pages, which matters because a cold Chromium launch
costs seconds.

Only public pages are rendered: no auth, no proxy, no fingerprint spoofing, per
AGENTS.md 1.7 and 3.1.2.
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
import subprocess
import threading
import warnings
from typing import Any

# CloakBrowser downloads its binary via httpx, which fails SSL verification
# on some systems. Patch httpx to disable SSL verify before CloakBrowser imports.
try:
    import httpx as _httpx
    _orig_init = _httpx.Client.__init__
    def _patched_init(self, *args, **kwargs):
        kwargs["verify"] = False
        _orig_init(self, *args, **kwargs)
    _httpx.Client.__init__ = _patched_init
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
except Exception:
    pass

# CloakBrowser is tried first (see _launch), so this is only the fallback for
# stock Playwright. All 115 registered sources point playwright_executable_path
# at an ms-playwright build that is not installed here, so a configured path
# that does not exist falls back to this list rather than launching nothing.
BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
DEFAULT_CHROMIUM = next(
    (path for path in BROWSER_CANDIDATES if os.path.exists(path)),
    BROWSER_CANDIDATES[0],
)

# Under the spawn start method the child gets a fresh module namespace, so the
# pipe has to travel as an argument rather than through module state.
_INBOX: Any = None
_PAGE: Any = None


def _launch(executable_path: str | None) -> Any:
    # Try CloakBrowser first (stealth Chromium with C++ fingerprint patches),
    # fall back to stock Playwright if CloakBrowser is not available.
    try:
        from cloakbrowser import launch as cloak_launch

        browser = cloak_launch(headless=True, humanize=True)
        context = browser.new_context(locale="zh-CN", viewport={"width": 1366, "height": 900})
        # Return a tuple compatible with the original: (session, browser, context, page)
        # session is None for CloakBrowser since there's no separate session object.
        return None, browser, context, context.new_page()
    except Exception:
        pass

    from playwright.sync_api import sync_playwright

    session = sync_playwright().start()
    launch: dict[str, Any] = {"headless": True}
    path = executable_path or DEFAULT_CHROMIUM
    if not os.path.exists(path):
        # Configured path is dead (ms-playwright not installed): use a real one.
        path = DEFAULT_CHROMIUM
    if os.path.exists(path):
        launch["executable_path"] = path
    browser = session.chromium.launch(**launch)
    context = browser.new_context(locale="zh-CN", viewport={"width": 1366, "height": 900})
    return session, browser, context, context.new_page()


def _settle(page: Any, timeout_ms: int) -> None:
    """等页面真正停下来，再取 HTML。

    goto(wait_until="domcontentloaded") 只保证 DOM 就绪，子资源和跳转链还在跑，
    这时 page.content() 会抛 "the page is navigating and changing the content"。
    实测 chengdu.gov.cn 静态 412、渲染也拿不到内容，根因就是这个竞态：快照落在
    <body></body> 空页上，于是"被反爬拦截"和"页面还没加载完"表现完全一样。

    先补一个 load 状态（不抛错，慢页面就跳过），再对仍在跳转的情况重试取快照。
    """
    try:
        page.wait_for_load_state("load", timeout=min(timeout_ms, 20000))
    except Exception:  # noqa: BLE001 - 慢页面不是失败，取不到再说
        pass


def _safe_content(page: Any, timeout_ms: int, attempts: int = 3) -> str:
    """取页面 HTML，对"仍在跳转"重试。

    有上限而不是死等：真的在无限跳转的页面本来就该放弃，不能把 300s 预算耗光。
    """
    last: str = ""
    for attempt in range(attempts):
        try:
            return page.content()
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            try:
                page.wait_for_load_state("load", timeout=min(timeout_ms, 10000))
            except Exception:  # noqa: BLE001
                pass
            if attempt + 1 < attempts:
                page.wait_for_timeout(500 * (attempt + 1))
    raise RuntimeError(f"page stayed unstable after {attempts} attempts: {last}")


def _worker(inbox: Any) -> None:
    """Serve render tasks until the parent closes the pipe."""
    # worker 也要装 winflags：它自己会 spawn node 驱动，调用方的补丁传不进来。
    # 不 import lighthouse.winflags（免得主项目依赖工具目录），内联同样逻辑。
    if os.name == "nt":
        _no_window = 0x08000000
        _new_console = 0x00000010
        _detached = 0x00000008
        _orig = subprocess.Popen.__init__

        def _popen(self, *a, **kw):                  # type: ignore[no-untyped-def]
            flags = int(kw.get("creationflags") or 0)
            # 保 NEW_CONSOLE（Renderer 给本 worker 配的隐藏控制台），
            # 剥 DETACHED_PROCESS 并补 NO_WINDOW——playwright 启动 node 驱动时
            # 传的就是 DETACHED_PROCESS，Windows 会脱离控制台再自己分配一个，
            # conhost 起来即退，那就是桌面上每分钟一闪的窗口。
            if not flags & _new_console:
                flags = (flags & ~_detached) | _no_window
            kw["creationflags"] = flags
            _orig(self, *a, **kw)

        subprocess.Popen.__init__ = _popen          # type: ignore[assignment]

    global _PAGE
    while True:
        try:
            task = inbox.recv()
        except (EOFError, OSError):
            return
        if task is None:
            return
        url, executable_path, timeout_ms, wait_ms, wait_until, locale, pages, mode = task
        try:
            if _PAGE is None:
                _PAGE = _launch(executable_path)
            _, _, context, page = _PAGE
            context.set_default_timeout(timeout_ms)
            page.goto(url, wait_until=wait_until or "domcontentloaded", timeout=timeout_ms)
            # networkidle already means "500ms with no network activity", so a blind
            # sleep on top of it is pure cost: measured byte-identical HTML at
            # wait_ms=0 and 5000, 0.9s vs 8.0s per page.
            if wait_ms and wait_until != "networkidle":
                page.wait_for_timeout(wait_ms)
            # domcontentloaded 只等 DOM，子资源与跳转链仍在跑，这时 page.content()
            # 会抛 "the page is navigating and changing the content"。
            # chengdu.gov.cn 就是这样：快照拿到的是 <body></body> 空页，
            # 于是"反爬拦截"和"页面还没加载完"长得一模一样，排查绕了很久。
            _settle(page, timeout_ms)
            if mode == "collect":
                inbox.send({"ok": True, "data": _extract_from_dom(page, pages, timeout_ms)})
                continue
            collected = [_safe_content(page, timeout_ms)]
            seen = {collected[0]}
            for _ in range(max(0, pages - 1)):
                nxt = _go_to_next_page(page)
                if not nxt:
                    break
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
                except Exception:  # noqa: BLE001 - a slow widget is not fatal
                    pass
                html = _safe_content(page, timeout_ms)
                if html in seen:
                    break
                seen.add(html)
                collected.append(html)
            inbox.send({"ok": True, "htmls": collected})
        except Exception as exc:  # noqa: BLE001 - report to the parent, keep serving
            _PAGE = None
            try:
                inbox.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            except (OSError, ValueError):
                return


DEFAULT_EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    const href = a.getAttribute('href') || '';
    const box = a.closest('li, tr, .row, .item');
    const text = ((box ? box.innerText : a.innerText) || '').replace(/\s+/g, ' ').trim();
    if (!text || seen.has(href)) return;
    seen.add(href);
    const d = text.match(/(20\d{2})[-\/年.](\d{1,2})[-\/月.](\d{1,2})/);
    out.push({href: href, text: text.slice(0, 400),
              date: d ? d[1] + '-' + d[2].padStart(2, '0') + '-' + d[3].padStart(2, '0') : null});
  });
  return {rows: out, count: out.length};
}
"""


def _extract_from_dom(page: Any, pages: int, timeout_ms: int) -> dict[str, Any]:
    """Run the default extractor on the page, walking pagination between pages."""
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(max(1, pages)):
        try:
            data = page.evaluate(DEFAULT_EXTRACT_JS)
        except Exception as exc:  # noqa: BLE001
            return {"rows": all_rows, "count": len(all_rows), "error": str(exc)[:200]}
        for row in (data or {}).get("rows") or []:
            key = str(row.get("href"))
            if key not in seen:
                seen.add(key)
                all_rows.append(row)
        if not _go_to_next_page(page):
            break
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
        except Exception:  # noqa: BLE001
            pass
    return {"rows": all_rows, "count": len(all_rows)}


# Pagination widgets in the order they are tried. Each returns True when it moved
# to another page. Selector lists cover layui laypage, common Chinese CMS markup
# and plain "下一页" links.
NEXT_SELECTORS = (
    "a.layui-laypage-next:not(.layui-disabled)",
    "a[title='下一页']:not(.layui-disabled)",
    "a:has-text('下一页')",
    ".pagination a.next:not(.disabled)",
    "a[title='Next']",
)
PAGE_INPUT_SELECTORS = (
    "input.layui-laypage-input",
    "input.page-jump",
    "input[name='pageNo']",
)


def _go_to_next_page(page: Any) -> bool:
    """Click through to the next archive page. Returns False when exhausted."""
    for selector in NEXT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            if not locator.is_visible():
                continue
            locator.click(timeout=8000)
            return True
        except Exception:  # noqa: BLE001 - try the next strategy
            continue
    return False


class Renderer:
    """A warm Chromium in a worker process, reused across pages."""

    def __init__(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe()
        self._inbox = parent
        self._process = ctx.Process(target=_worker, args=(child,), daemon=True)
        # CREATE_NO_WINDOW (0x08000000) on the worker.
        #
        # multiprocessing's spawn inherits the parent's console, but when the
        # parent has none — which is the normal case now that the collectors are
        # launched detached with CREATE_NO_WINDOW — the child allocates a fresh
        # one, so a console window flashes on the user's desktop every time a
        # Renderer is created. Measured: the popup appeared once per repair.py
        # / run.py process, which matches "a window flashes and is gone".
        #
        # multiprocessing has no public flag for this, but Process reads
        # _creationflags on Windows before Popen, so setting it before start()
        # is the supported-by-behaviour way.
        # 给 worker 一个**隐藏的控制台**。
        #
        # 这是弹窗的最后一环。worker 自己会 spawn playwright 的 node 驱动，
        # 而调用方装的 winflags 只在本进程生效，传不进这个 spawn 出来的子进程。
        # worker 没有控制台时，node 会自己分配一个（conhost 起来就退），
        # 那就是桌面上每分钟一闪的窗口。
        #
        # multiprocessing 的 start() 不接受 startupinfo，所以在启动那一瞬
        # 临时给 subprocess.Popen 挂上 SW_HIDE，start 完立刻还原——只影响这一次。
        if os.name == "nt":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 0                       # SW_HIDE
            self._process._creationflags = 0x00000010 | 0x00000400
            _orig = subprocess.Popen.__init__

            def _hidden(self, *a, **kw):               # type: ignore[no-untyped-def]
                kw["creationflags"] = int(kw.get("creationflags") or 0) | 0x00000400
                kw["startupinfo"] = info
                _orig(self, *a, **kw)

            subprocess.Popen.__init__ = _hidden        # type: ignore[assignment]
            try:
                self._process.start()
            finally:
                subprocess.Popen.__init__ = _orig      # type: ignore[assignment]
        else:
            self._process.start()
        child.close()

    def render(
        self,
        url: str,
        executable_path: str | None = None,
        timeout_ms: int = 60000,
        wait_ms: int = 5000,
        wait_until: str = "networkidle",
        locale: str = "zh-CN",
        pages: int = 1,
    ) -> list[str]:
        """Render one page, or up to `pages` pages of the same paginated list.

        Government portals paginate with client-side widgets (layui laypage,
        href="javascript:;" plus data-page), so the archive URL cannot be
        derived from a pattern -- the real request is made by the widget. Driving
        the page and clicking through is the only reliable way, and it also works
        for static pagination. ZJ publishes 325 items on one column at 15 per page;
        reading only page 1 loses 95% of the history.
        """
        if not self._process.is_alive():
            raise RuntimeError("render worker died")
        self._inbox.send((url, executable_path, timeout_ms, wait_ms, wait_until, locale, int(pages), "html"))
        if not self._inbox.poll(timeout_ms / 1000 * max(1, int(pages)) + 60):
            raise TimeoutError(f"render timed out after {timeout_ms}ms: {url}")
        payload = self._inbox.recv()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "render failed"))
        return [str(page) for page in (payload.get("htmls") or [])]

    def collect(self, url: str, pages: int = 3, timeout_ms: int = 60000,
                wait_ms: int = 6000, wait_until: str = "networkidle") -> dict[str, Any]:
        """Open the page, then run a JS extractor inside it and return its result.

        Some portals fetch their data with a client-side signature, so the markup
        holds no links. The page can still render them for any visitor, so the
        extraction happens inside the page: nothing is forged, we just read what a
        browser displays.
        """
        if not self._process.is_alive():
            raise RuntimeError("render worker died")
        self._inbox.send((url, None, timeout_ms, wait_ms, wait_until, "zh-CN", int(pages), "collect"))
        if not self._inbox.poll(timeout_ms / 1000 * max(1, int(pages)) + 90):
            raise TimeoutError(f"collect timed out: {url}")
        payload = self._inbox.recv()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "collect failed"))
        return dict(payload.get("data") or {})

    def close(self) -> None:
        if not self._process.is_alive():
            return
        try:
            self._inbox.send(None)
            self._process.join(timeout=5)
        except (OSError, ValueError):
            pass
        if self._process.is_alive():
            self._process.terminate()

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


_RENDERER: Renderer | None = None
# One warm browser serves every source, so concurrent collectors must not drive
# the same page at once. A page costs ~1s now that the redundant sleep is gone,
# so serialising renders is cheap while static fetches stay fully parallel.
_RENDER_LOCK = threading.Lock()


def render_page(
    url: str,
    executable_path: str | None = None,
    timeout_ms: int = 60000,
    wait_ms: int = 5000,
    wait_until: str = "networkidle",
    locale: str = "zh-CN",
    pages: int = 1,
) -> str:
    global _RENDERER
    with _RENDER_LOCK:
        if _RENDERER is None:
            _RENDERER = Renderer()
        renderer = _RENDERER
        # ★ 整个 render 必须在锁内，不能只锁「取 renderer」那一步。
        #
        # 实测 2026-10-02（4 线程并发，串行同样的 4 个全部成功）：
        #   并发 -> AssertionError / UnpicklingError: pickle data was truncated，全灭
        #   串行 -> 4/4 成功
        #
        # 根因是 Renderer 用 ``multiprocessing.Pipe`` 和 worker 进程通信，
        # 而 Pipe **不是线程安全的**：两个线程同时往同一个 inbox 读，
        # 就会各自拿到半截 pickle —— 报的正是 UnpicklingError。
        # 之前 ``_RENDER_LOCK`` 只保护「惰性创建 renderer」，render() 在锁外跑，
        # 等于没锁。
        #
        # 为什么 worker 进程隔离挡不住这个：进程隔离解决的是**事件循环**冲突
        # （Playwright sync API vs crawl4ai 的 asyncio.run），那是原先那个
        # 「Sync API inside the asyncio loop」；这里是**同一个 Pipe 被多线程读**，
        # 是另一回事，两者互不相干。
        #
        # 渲染本来就慢，6 个采集线程抢一个浏览器，串行化不会让整体更慢；
        # 而失败是全灭——不成串行就等于没有渲染能力。
        try:
            return renderer.render(url, executable_path, timeout_ms, wait_ms,
                                   wait_until, locale, pages)[0]
        except Exception:
            # 句柄可能已经废了（EPIPE / pickle 截断都是它的症状）：
            # 丢掉它，下次重新启动一个干净的浏览器，而不是永远对着死句柄报错。
            _RENDERER = None
            raise


def render_pages(
    url: str,
    executable_path: str | None = None,
    timeout_ms: int = 60000,
    wait_ms: int = 5000,
    wait_until: str = "networkidle",
    locale: str = "zh-CN",
    pages: int = 5,
) -> list[str]:
    """Render up to `pages` archive pages. Needed for 1-10 year history."""
    global _RENDERER
    with _RENDER_LOCK:
        if _RENDERER is None:
            _RENDERER = Renderer()
        renderer = _RENDERER
        # 同 render_page：整个 render 必须在锁内。Pipe 不是线程安全的，
        # 原因见 render_page 里那段注释。
        try:
            return renderer.render(url, executable_path, timeout_ms, wait_ms,
                                   wait_until, locale, pages)
        except Exception:
            _RENDERER = None
            raise


def shutdown_renderer() -> None:
    global _RENDERER
    with _RENDER_LOCK:
        if _RENDERER is not None:
            _RENDERER.close()
            _RENDERER = None


# The browser is shared and deliberately outlives any single collector, so it is
# released once when the process ends instead of on every collector.close().
atexit.register(shutdown_renderer)
