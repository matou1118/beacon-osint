"""验证 cloakbrowser → CDP → crawl4ai 链路能否跑通。

要证伪/证实的三件事（每件都可能是坑，之前踩过）
  1. --remote-debugging-port 会不会被 cloakbrowser 的 stealth_args 挤掉。
     stealth_args 会拼一批 --fingerprint-* 与 --load-extension，谁最后写进 argv
     决定这个端口还在不在。
  2. crawl4ai 挂上外部浏览器到底认不认 cdp_url。
     BrowserConfig.browser_mode 只有 builtin/docker/custom/dedicated 四个分支被
     消费（async_configs.py:949-962），写成 "cdp" 会被静默接受但不匹配任何分支，
     use_managed_browser 停在 False，于是 crawl4ai 自己再开一个 Chromium。
     正确值是 "custom"，且必须同时给 cdp_url。
  3. 挂上外部浏览器后，crawl4ai 自己的预处理 JS 还跑不跑（影响 fit_markdown）。

对比基准：同一页面同时跑 trafilatura txt 路线。已测湖北那页 txt=6322 字、0 残留，
所以这里不比字数，只比表格/标题等结构是否保住。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_PORT = 9243
# 湖北省政府：静态请求 412，是判定"能否破反爬"的硬样本。
# 广东发改委：普通站点，判定"接入 CDP 后正常站点有没有倒退"。
PROBES = {
    "hubei_412": "https://www.hubei.gov.cn/xxgk/zd/file",
    "hubei_doc": "https://www.hubei.gov.cn/zfwj/ezf/202609/t20260924_6022841.shtml",
    "gd_normal": "https://drc.gd.gov.cn/ywtz/index.html",
}
CHROME_WORDS = ("字号", "打印", "上一篇", "下一篇", "版权所有", "备案", "分享到",
                "当前位置", "访问量", "无障碍", "字号：")


def probe_cdp(port: int) -> dict:
    """读 CDP 端点。返回 version 信息，确认端口真的活着。"""
    import httpx

    url = f"http://127.0.0.1:{port}/json/version"
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def md_structure(markdown: str) -> dict:
    """MD 相对 txt 唯一多出来的东西就是结构，所以只量结构。"""
    return {
        "标题行": len(re.findall(r"(?m)^#{1,6} ", markdown or "")),
        "表格行": len(re.findall(r"(?m)^\s*\|", markdown or "")),
        "列表项": len(re.findall(r"(?m)^\s*[-*+] ", markdown or "")),
        "链接": len(re.findall(r"(?m)\[[^\]]+\]\([^)]+\)", markdown or "")),
    }


def chrome_hits(text: str) -> list[str]:
    return [word for word in CHROME_WORDS if word in (text or "")]


async def run(args: argparse.Namespace) -> int:
    from cloakbrowser import launch_async

    port = args.port
    print(f"[1/4] 启动 cloakbrowser（headless={not args.headful}），端口 {port} ...", flush=True)
    started = time.time()
    browser = await launch_async(
        headless=not args.headful,
        humanize=True,
        args=[
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    print(f"      启动耗时 {time.time() - started:.1f}s", flush=True)

    try:
        # 坑 1：端口会不会被 stealth_args 挤掉
        try:
            version = probe_cdp(port)
        except Exception as exc:  # noqa: BLE001
            print(f"[1/4] ✗ CDP 端口不通：{type(exc).__name__}: {exc}")
            print("      → --remote-debugging-port 被 cloakbrowser 覆盖或未生效")
            return 2
        ws = version.get("webSocketDebuggerUrl", "")
        print(f"[1/4] ✓ CDP 活着：{version.get('Browser')}  ws={ws[:60]}", flush=True)

        # 坑 2/3：crawl4ai 能不能挂上去
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        import trafilatura

        # browser_mode 必须是 "custom"：源码 async_configs.py:949-962 只消费
        # builtin / docker / custom / dedicated，写 "cdp" 不匹配任何分支，
        # use_managed_browser 会停在 False，crawl4ai 就自己再开一个浏览器。
        config = BrowserConfig(browser_mode="custom", cdp_url=f"http://127.0.0.1:{port}")
        print(f"[2/4] crawl4ai browser_mode={config.browser_mode!r} "
              f"use_managed_browser={config.use_managed_browser} "
              f"cdp_url={config.cdp_url}", flush=True)
        if not config.use_managed_browser:
            print("      ✗ use_managed_browser=False，会另开浏览器，链路没按预期接上")
            return 3

        run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            scan_full_page=args.scan_full_page,
            wait_until="networkidle",
        )

        results: dict[str, dict] = {}
        async with AsyncWebCrawler(config=config) as crawler:
            for name, url in PROBES.items():
                print(f"\n[3/4] {name}  {url}", flush=True)
                t0 = time.time()
                try:
                    result = await crawler.arun(url=url, config=run_config)
                except Exception as exc:  # noqa: BLE001
                    print(f"      ✗ 抛错 {type(exc).__name__}: {str(exc)[:140]}")
                    results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    continue
                elapsed = time.time() - t0
                if not result.success:
                    print(f"      ✗ success=False  {result.error_message}  ({elapsed:.1f}s)")
                    results[name] = {"ok": False, "error": result.error_message}
                    continue
                markdown = result.markdown or ""
                status = (result.metadata or {}).get("status_code")
                title = (result.metadata or {}).get("title")
                print(f"      ✓ {elapsed:.1f}s  status={status}  md={len(markdown)} 字")
                print(f"        title: {str(title)[:60]}")
                print(f"        结构: {md_structure(markdown)}")
                hits = chrome_hits(markdown)
                print(f"        残留框架文字: {hits if hits else '无'}")
                print(f"        开头: {markdown[:160]!r}")
                results[name] = {
                    "ok": True, "status": status, "md_chars": len(markdown),
                    "structure": md_structure(markdown), "chrome": hits,
                    "elapsed": round(elapsed, 1), "title": title,
                }

            # 坑 3 的直接证据：crawl4ai 拿到的原始 DOM 与 markdown 都在，
            # 说明它的清洗链路在外部浏览器上也跑了
            print(f"\n[4/4] 取一个页面的原始 HTML 做 txt 基线对比", flush=True)
            baseline_html = await _raw_html(crawler, PROBES["hubei_doc"])
            if baseline_html:
                txt = trafilatura.extract(
                    baseline_html, include_comments=False, include_tables=True,
                    output_format="txt") or ""
                md2 = trafilatura.extract(
                    baseline_html, include_comments=False, include_tables=True,
                    output_format="markdown") or ""
                print(f"      原始 HTML      {len(baseline_html):>6}")
                print(f"      trafilatura txt       {len(txt):>6}  残留 {chrome_hits(txt) or '无'}")
                print(f"      trafilatura markdown  {len(md2):>6}  结构 {md_structure(md2)}")
                results["trafilatura_baseline"] = {
                    "html": len(baseline_html), "txt": len(txt), "markdown": len(md2),
                    "txt_chrome": chrome_hits(txt), "md_structure": md_structure(md2),
                }
    finally:
        try:
            await browser.close()
            print("\n已关闭 cloakbrowser", flush=True)
        except Exception:  # noqa: BLE001
            pass

    out = Path(__file__).resolve().parents[1] / "data" / "cdp_probe.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果写入 {out}")
    return 0 if any(v.get("ok") for v in results.values() if isinstance(v, dict)) else 4


async def _raw_html(crawler, url: str) -> str:
    """借 crawl4ai 的 session 拿一次原始 HTML，用于 txt 基线对比。"""
    try:
        page = await crawler.get_page(url)
        return await page.content()
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--headful", action="store_true",
                        help="有头模式。Windows 带桌面可直接试，强检测站点才需要")
    parser.add_argument("--no-full-page", dest="scan_full_page", action="store_false",
                        help="关掉整页扫描（政务页可能很长，会明显变慢）")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
