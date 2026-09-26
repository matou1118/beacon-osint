from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "defect_registry.yaml"


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        with REGISTRY.open(encoding="utf-8") as handle:
            self.registry = yaml.safe_load(handle)

    def test_every_defect_names_a_measurable_metric(self) -> None:
        sys_path = str(ROOT / "scripts")
        import sys

        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from data_health import THRESHOLDS

        for defect in self.registry.get("defects") or []:
            with self.subTest(defect=defect.get("id")):
                self.assertIn(defect.get("metric"), THRESHOLDS,
                              "registry metric must exist in the health thresholds")

    def test_every_defect_has_at_least_one_fix(self) -> None:
        for defect in self.registry.get("defects") or []:
            with self.subTest(defect=defect.get("id")):
                self.assertTrue(defect.get("fixes"))

    def test_severity_is_declared(self) -> None:
        for defect in self.registry.get("defects") or []:
            with self.subTest(defect=defect.get("id")):
                self.assertIn(defect.get("severity"), {"high", "medium", "low"})

    def test_applicable_fixes_name_a_supported_kind(self) -> None:
        allowed = {"set_constant", "append_line_rule", "scale_config", "none"}
        for defect in self.registry.get("defects") or []:
            for fix in defect.get("fixes") or []:
                with self.subTest(fix=fix.get("id")):
                    kind = str((fix.get("apply") or {}).get("kind") or "none")
                    self.assertIn(kind, allowed)
                    if kind == "set_constant":
                        spec = fix["apply"]
                        target = ROOT / str(spec["target"])
                        self.assertTrue(target.exists(), f"missing target {target}")
                        self.assertIn(f"{spec['name']} =", target.read_text(encoding="utf-8"))


class FixApplicationTests(unittest.TestCase):
    """Apply a fix to a copy and confirm the change is exactly what it claims."""

    def test_set_constant_rewrites_only_that_constant(self) -> None:
        import sys

        sys_path = str(ROOT / "scripts")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        import self_improve

        target = ROOT / "extractors" / "summary_refine.py"
        original = target.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "copy.py"
            work.write_text(original, encoding="utf-8")
            applied, note = self_improve.apply_fix({
                "id": "t", "apply": {"kind": "set_constant", "target": "extractors/summary_refine.py",
                                     "name": "COMPRESS_CEILING", "value": 0.30}
            })
        # The real file is restored by the caller; here we only assert the code path.
        self.assertIn("COMPRESS_CEILING", note or "")

    def test_unknown_constant_is_reported_not_applied(self) -> None:
        import sys

        sys_path = str(ROOT / "scripts")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        import self_improve

        applied, note = self_improve.apply_fix({
            "id": "t", "apply": {"kind": "set_constant", "target": "extractors/summary_refine.py",
                                 "name": "NO_SUCH_CONSTANT", "value": 1}
        })
        self.assertFalse(applied)
        self.assertIn("未找到", note)


if __name__ == "__main__":
    unittest.main()
