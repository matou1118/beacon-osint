from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

try:
    import pymupdf
except ImportError:
    pymupdf = None


class DocumentPolicyError(RuntimeError):
    pass


@dataclass
class DownloadResult:
    source_id: str
    source_url: str
    final_url: str
    path: str
    content_type: str
    bytes: int
    sha256: str
    status_code: int


def _allowed_url(url: str, allowed_domains: set[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def _robots_allows(url: str, client: httpx.Client, user_agent: str) -> None:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    response = client.get(robots_url, headers={"User-Agent": user_agent})
    if response.status_code in {404, 410}:
        return
    if response.status_code >= 400:
        raise DocumentPolicyError(f"robots.txt unavailable: {response.status_code}")
    parser = RobotFileParser(robots_url)
    parser.parse(response.text.splitlines())
    if not parser.can_fetch(user_agent, url):
        raise DocumentPolicyError(f"robots.txt disallows: {url}")


def download_public_file(
    url: str,
    output_dir: str | Path,
    allowed_domains: set[str],
    source_id: str,
    max_bytes: int = 20 * 1024 * 1024,
    allowed_extensions: set[str] | None = None,
    check_robots: bool = True,
    client: httpx.Client | None = None,
    user_agent: str = "BeaconOSINT/0.1",
) -> DownloadResult:
    if not _allowed_url(url, allowed_domains):
        raise DocumentPolicyError(f"URL outside source allowlist: {url}")
    suffix = Path(urlparse(url).path).suffix.lower()
    if allowed_extensions and suffix not in {item.lower() for item in allowed_extensions}:
        raise DocumentPolicyError(f"file extension is not allowed: {suffix or 'none'}")
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True, timeout=30, headers={"User-Agent": user_agent})
    temporary_path = None
    try:
        if check_robots:
            _robots_allows(url, active_client, user_agent)
        with active_client.stream("GET", url, headers={"User-Agent": user_agent}) as response:
            if response.status_code >= 400:
                raise DocumentPolicyError(f"HTTP {response.status_code} for {url}")
            final_url = str(response.url)
            if not _allowed_url(final_url, allowed_domains):
                raise DocumentPolicyError(f"redirect outside source allowlist: {final_url}")
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise DocumentPolicyError("download exceeds max_bytes")
            directory = Path(output_dir)
            directory.mkdir(parents=True, exist_ok=True)
            filename = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + (suffix or ".bin")
            target = directory / filename
            temporary_path = target.with_suffix(target.suffix + ".part")
            digest = hashlib.sha256()
            total = 0
            with temporary_path.open("wb") as handle:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise DocumentPolicyError("download exceeds max_bytes")
                    digest.update(chunk)
                    handle.write(chunk)
            os.replace(temporary_path, target)
            return DownloadResult(source_id=source_id, source_url=url, final_url=final_url, path=str(target), content_type=response.headers.get("content-type", ""), bytes=total, sha256=digest.hexdigest(), status_code=response.status_code)
    finally:
        if owns_client:
            active_client.close()
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def extract_document_text(path: str | Path, ocr_engine: Callable[[Path], str] | None = None) -> dict[str, Any]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        if pymupdf is None:
            return {"text": "", "pages": [], "ocr_status": "unavailable", "needs_review": True}
        document = pymupdf.open(file_path)
        pages = [page.get_text("text") for page in document]
        text = "\n\n".join(pages).strip()
        result = {"text": text, "pages": pages, "ocr_status": "not_needed", "needs_review": False}
        if not text and ocr_engine is None:
            result.update({"ocr_status": "not_configured", "needs_review": True})
        elif not text and ocr_engine is not None:
            result["text"] = ocr_engine(file_path)
            result.update({"ocr_status": "local_engine", "needs_review": False})
        return result
    if suffix in {".txt", ".csv", ".md", ".html"}:
        return {"text": file_path.read_text(encoding="utf-8", errors="replace"), "pages": [], "ocr_status": "not_needed", "needs_review": False}
    if suffix in {".png", ".jpg", ".jpeg"}:
        if ocr_engine is None:
            return {"text": "", "pages": [], "ocr_status": "not_configured", "needs_review": True}
        return {"text": ocr_engine(file_path), "pages": [], "ocr_status": "local_engine", "needs_review": False}
    return {"text": "", "pages": [], "ocr_status": "unsupported", "needs_review": True}


def ingest_public_document(url: str, output_dir: str | Path, allowed_domains: set[str], source_id: str, config: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    download = download_public_file(url, output_dir, allowed_domains, source_id, max_bytes=int(config.get("max_bytes", 20 * 1024 * 1024)), allowed_extensions=set(config.get("allowed_extensions") or []), check_robots=bool(config.get("check_robots", True)), client=client)
    extraction = extract_document_text(download.path)
    return {"download": asdict(download), "extraction": extraction}
