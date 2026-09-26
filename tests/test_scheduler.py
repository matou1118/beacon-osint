from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scheduler import create_scheduler, run_source_with_retry, update_failure_report


class SchedulerTests(unittest.TestCase):
    def test_retry_attempts_are_bounded(self) -> None:
        calls = []
        sleeps = []

        def run(_source):
            calls.append(1)
            if len(calls) <= 3:
                raise RuntimeError("temporary failure")
            return {"sources": [{"source_id": "demo", "records": 1, "errors": []}]}

        outcome = run_source_with_retry({"id": "demo"}, {"retry_attempts": 3, "retry_backoff_seconds": 1}, run, sleep=sleeps.append)
        self.assertTrue(outcome["success"])
        self.assertEqual(outcome["attempts"], 4)
        self.assertEqual(len(calls), 4)
        self.assertEqual(sleeps, [1, 2, 4])

    def test_failure_report_preserves_source_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failures.json"
            update_failure_report(path, "demo", {"attempts": 4, "last_error": "timeout"})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["sources"]["demo"]["attempts"], 4)
            update_failure_report(path, "demo", None)
            self.assertNotIn("demo", json.loads(path.read_text(encoding="utf-8"))["sources"])

    def test_scheduler_registers_independent_source_intervals(self) -> None:
        scheduler = create_scheduler(
            [{"id": "daily", "cadence_seconds": 86400}, {"id": "weekly", "cadence_seconds": 604800}],
            {"retry_attempts": 1, "misfire_grace_time": 60, "max_instances": 1, "coalesce": True, "backup_hour": 2, "backup_minute": 30, "quality_hour": 23, "quality_minute": 55},
            lambda _source: None,
            lambda: None,
            lambda: None,
            lambda: None,
        )
        jobs = scheduler.get_jobs()
        ids = {job.id for job in jobs}
        self.assertTrue({"source:daily", "source:weekly", "backup", "quality", "cleanup"}.issubset(ids))
        intervals = {job.id: job.trigger.interval.total_seconds() for job in jobs if job.id.startswith("source:")}
        self.assertEqual(intervals["source:daily"], 86400)
        self.assertEqual(intervals["source:weekly"], 604800)


if __name__ == "__main__":
    unittest.main()
