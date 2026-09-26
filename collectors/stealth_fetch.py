"""Stealth fetching: cloudscraper + curl-cffi + CloakBrowser fallbacks.

When httpx gets HTTP 412/403 (WAF/anti-bot), these tools bypass the protection:
- cloudscraper: bypasses Cloudflare JS challenge
- curl-cffi: impersonates browser TLS fingerprint (ja3/ja4)
- CloakBrowser: drop-in Playwright replacement with C++ level fingerprint patches

All functions return httpx-like response objects with .text, .content, .status_code, .url.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse


class StealthResponse:
    """Minimal httpx-compatible response wrapper."""

    def __init__(self, text: str, status_code: int, url: str) -> None:
        self.text = text
        self.content = text.encode("utf-8", errors="replace")
        self.status_code = status_code
        self.url = url
        self.headers: dict[str, str] = {}


def fetch_with_cloudscraper(
    url: str,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    timeout: int = 30,
) -> StealthResponse | None:
    """Fetch with cloudscraper to bypass Cloudflare JS challenge.

    Returns None if cloudscraper is not available or the fetch fails.
    """
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True},
            delay=10,
        )
        resp = scraper.get(url, timeout=timeout)
        return StealthResponse(
            text=resp.text,
            status_code=resp.status_code,
            url=str(resp.url),
        )
    except Exception:
        return None


def fetch_with_curl_cffi(
    url: str,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    timeout: int = 30,
) -> StealthResponse | None:
    """Fetch with curl-cffi to impersonate browser TLS fingerprint.

    curl-cffi uses the same TLS handshake as a real Chrome browser, so WAF
    systems that check ja3/ja4 fingerprints let the request through.

    Returns None if curl-cffi is not available or the fetch fails.
    """
    try:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get(
            url,
            impersonate="chrome131",
            timeout=timeout,
            allow_redirects=True,
        )
        return StealthResponse(
            text=resp.text,
            status_code=resp.status_code,
            url=str(resp.url),
        )
    except Exception:
        return None


def fetch_stealth(url: str, user_agent: str = "", timeout: int = 30) -> StealthResponse | None:
    """Try cloudscraper first, then curl-cffi. Returns None if both fail.

    cloudscraper handles Cloudflare challenges specifically, while curl-cffi
    handles generic TLS fingerprint checks. Try both in order.
    """
    # Quick check: don't try stealth for non-HTTP URLs
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None

    # Try cloudscraper first (handles Cloudflare JS challenge)
    resp = fetch_with_cloudscraper(url, user_agent, timeout)
    if resp is not None and resp.status_code < 400:
        return resp

    # Fall back to curl-cffi (handles TLS fingerprint checks)
    resp = fetch_with_curl_cffi(url, user_agent, timeout)
    if resp is not None and resp.status_code < 400:
        return resp

    return None


def render_with_cloakbrowser(
    url: str,
    timeout_ms: int = 60000,
    wait_ms: int = 5000,
    wait_until: str = "networkidle",
    locale: str = "zh-CN",
) -> str | None:
    """Render a JS page with CloakBrowser (stealth Chromium).

    CloakBrowser is a drop-in Playwright replacement with C++ level fingerprint
    patches. It passes Cloudflare Turnstile, FingerprintJS, and other bot
    detection systems that block stock Playwright.

    Returns the page HTML, or None if CloakBrowser is not available or fails.
    """
    try:
        from cloakbrowser import launch

        browser = launch(headless=True, humanize=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            if wait_ms:
                page.wait_for_timeout(wait_ms)
            return page.content()
        finally:
            browser.close()
    except Exception:
        return None
