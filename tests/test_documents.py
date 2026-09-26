from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from storage.documents import DocumentPolicyError, download_public_file, extract_document_text, ingest_public_document


class DocumentPipelineTests(unittest.TestCase):
    def test_public_txt_download_is_bounded_and_hash_recorded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            return httpx.Response(200, content="政策原文".encode("utf-8"), headers={"content-type": "text/plain"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as directory:
            result = download_public_file("https://gov.example.cn/file.txt", directory, {"gov.example.cn"}, "gov", allowed_extensions={".txt"}, client=client)
            self.assertEqual(result.bytes, 12)
            self.assertTrue(Path(result.path).exists())
            extracted = extract_document_text(result.path)
            self.assertEqual(extracted["text"], "政策原文")

    def test_disallowed_domain_and_missing_ocr_are_explicit(self) -> None:
        with self.assertRaises(DocumentPolicyError):
            download_public_file("https://other.example/file.txt", "data", {"gov.example.cn"}, "gov", allowed_extensions={".txt"}, check_robots=False)
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "page.png"
            image.write_bytes(b"not-a-real-image")
            result = extract_document_text(image)
            self.assertTrue(result["needs_review"])
            self.assertEqual(result["ocr_status"], "not_configured")

    def test_ingest_returns_download_and_extraction_sections(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content="hello", headers={"content-type": "text/plain"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_public_document("https://gov.example.cn/a.txt", directory, {"gov.example.cn"}, "gov", {"max_bytes": 1000, "allowed_extensions": [".txt"], "check_robots": True}, client=client)
            self.assertEqual(result["extraction"]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
