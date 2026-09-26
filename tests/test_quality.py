from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quality import metrics_from_result, render_dashboard, update_quality


class QualityTests(unittest.TestCase):
    def test_daily_quality_aggregates_sources_and_renders_dashboard(self) -> None:
        result = {
            "sources": [{"source_id": "a", "records": 3, "errors": []}, {"source_id": "b", "records": 2, "errors": [{"error": "timeout"}]}],
            "records": [{}, {}, {}, {}, {}],
            "matches": [{}],
            "extraction_total": 5,
            "extraction_success": 4,
        }
        metrics = metrics_from_result(result)
        with tempfile.TemporaryDirectory() as directory:
            data = update_quality(directory, metrics, datetime(2026, 9, 25, tzinfo=timezone.utc))
            self.assertEqual(data["collected"], 5)
            self.assertEqual(data["extraction_success_rate"], 0.8)
            self.assertEqual(data["source_stats"]["b"]["failures"], 1)
            dashboard = Path(directory) / "dashboard.html"
            self.assertTrue(dashboard.exists())
            self.assertIn("数据质量看板", dashboard.read_text(encoding="utf-8"))
            render_dashboard(data, dashboard)

    def test_metrics_never_store_raw_records(self) -> None:
        metrics = metrics_from_result({"sources": [{"source_id": "a", "records": 1, "errors": []}], "records": [{"content": "secret"}]})
        self.assertNotIn("records", metrics)


if __name__ == "__main__":
    unittest.main()
