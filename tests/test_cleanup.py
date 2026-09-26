from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cleanup import cleanup_runtime_artifacts, scrub_text


class CleanupTests(unittest.TestCase):
    def test_runtime_artifacts_are_removed_but_audit_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "page.html").write_text("temporary", encoding="utf-8")
            audit = root / "audit" / "run.log"
            audit.parent.mkdir()
            audit.write_text("source=official", encoding="utf-8")
            debug = root / "debug.log"
            debug.write_text("Cookie=secret\nAuthorization=Bearer token", encoding="utf-8")
            result = cleanup_runtime_artifacts([runtime], [audit, debug], preserve_audit=True)
            self.assertEqual(result["removed_files"], 1)
            self.assertFalse(runtime.exists())
            self.assertTrue(audit.exists())
            self.assertIn("[REDACTED]", debug.read_text(encoding="utf-8"))

    def test_scrub_text_redacts_common_secrets(self) -> None:
        value = scrub_text("api_key=abc token:def password=123")
        self.assertNotIn("abc", value)
        self.assertNotIn("def", value)
        self.assertNotIn("123", value)


if __name__ == "__main__":
    unittest.main()
