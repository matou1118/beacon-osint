from __future__ import annotations

import unittest

from scripts.probe_sources import build_probe_summary, failure_reason, source_mode


class ProbeReportTests(unittest.TestCase):
    def test_mode_and_failure_classification(self) -> None:
        self.assertEqual(source_mode({"access_method": "api", "list_endpoint": "https://example.gov.cn/api"}), "API")
        self.assertEqual(source_mode({"rss_url": "https://example.gov.cn/rss"}), "RSS")
        self.assertEqual(source_mode({"access_method": "static_html"}), "WEB")
        self.assertEqual(failure_reason([{"error": "robots.txt access denied"}]), "robots_denied")
        self.assertEqual(failure_reason([{"error": "HTTP 412 for page"}]), "http_412")

    def test_summary_counts_modes_and_outputs_plan(self) -> None:
        sources = [{"id": "api", "access_method": "api"}, {"id": "web", "access_method": "static_html"}]
        result = {"record_count": 3, "extraction_total": 3, "extraction_success": 2, "sources": [{"source_id": "api", "records": 2, "errors": []}, {"source_id": "web", "records": 1, "errors": [{"error": "HTTP 404 for page"}]}]}
        summary = build_probe_summary(sources, result)
        self.assertEqual(summary["total_sources"], 2)
        self.assertEqual(summary["total_records"], 3)
        self.assertEqual(summary["by_mode"]["API"]["records"], 2)
        self.assertEqual(summary["by_mode"]["WEB"]["failed"], 1)
        self.assertIn("detail_pattern", " ".join(summary["improvement_plan"]))


if __name__ == "__main__":
    unittest.main()
