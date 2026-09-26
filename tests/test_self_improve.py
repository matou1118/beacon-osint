from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "defect_registry.yaml"
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def _restore_after(test: unittest.TestCase, relative: str) -> None:
    """Undo edits apply_fix makes to real repo files when the test ends."""
    target = ROOT / relative
    original = target.read_bytes()
    test.addCleanup(target.write_bytes, original)


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        with REGISTRY.open(encoding="utf-8") as handle:
            self.registry = yaml.safe_load(handle)

    def test_every_defect_names_a_measurable_metric(self) -> None:
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
        import self_improve

        target = "extractors/summary_refine.py"
        _restore_after(self, target)
        before = (ROOT / target).read_text(encoding="utf-8")
        applied, note = self_improve.apply_fix({
            "id": "t", "apply": {"kind": "set_constant", "target": target,
                                 "name": "COMPRESS_CEILING", "value": 0.42}
        })
        self.assertTrue(applied, note)
        after = (ROOT / target).read_text(encoding="utf-8")
        self.assertIn("COMPRESS_CEILING = 0.42", after)
        # Everything else untouched: only that one line differs.
        changed = [a for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(len(changed), 1, f"expected one changed line, got {changed}")

    def test_unknown_constant_is_reported_not_applied(self) -> None:
        import self_improve

        applied, note = self_improve.apply_fix({
            "id": "t", "apply": {"kind": "set_constant", "target": "extractors/summary_refine.py",
                                 "name": "NO_SUCH_CONSTANT", "value": 1}
        })
        self.assertFalse(applied)
        self.assertIn("未找到", note)


class RevertTests(unittest.TestCase):
    """revert() must put every snapshotted file back, wherever it lives."""

    def test_revert_restores_a_repo_root_file(self) -> None:
        import self_improve

        # policy_relations.py sits at the repo root, outside the
        # collectors/ extractors/ config/ directories the old search looked in,
        # so its "reverted" edits were silently left in place.
        relative = "policy_relations.py"
        _restore_after(self, relative)
        target = ROOT / relative
        original = target.read_text(encoding="utf-8")
        self.assertIn("AUTO_THRESHOLD", original)

        applied, note = self_improve.apply_fix({
            "id": "t", "apply": {"kind": "set_constant", "target": relative,
                                 "name": "AUTO_THRESHOLD", "value": 61}
        })
        self.assertTrue(applied, note)
        self.assertIn("AUTO_THRESHOLD = 61", target.read_text(encoding="utf-8"))

        self_improve.revert()
        self.assertEqual(target.read_text(encoding="utf-8"), original,
                         "revert() must restore a repo-root file exactly")

    def test_revert_ignores_snapshots_from_earlier_runs(self) -> None:
        import self_improve

        relative = "policy_relations.py"
        _restore_after(self, relative)
        target = ROOT / relative
        original = target.read_text(encoding="utf-8")

        # First step edits and reverts cleanly.
        self_improve.apply_fix({
            "id": "a", "apply": {"kind": "set_constant", "target": relative,
                                 "name": "AUTO_THRESHOLD", "value": 62}
        })
        self_improve.revert()
        self.assertEqual(target.read_text(encoding="utf-8"), original)

        # A later unrelated step must not roll the first one back a second time.
        target.write_text(original.replace("AUTO_THRESHOLD = 90", "AUTO_THRESHOLD = 77"),
                          encoding="utf-8")
        newer = target.read_text(encoding="utf-8")
        self_improve.apply_fix({
            "id": "b", "apply": {"kind": "set_constant", "target": relative,
                                 "name": "AUTO_THRESHOLD", "value": 63}
        })
        self_improve.revert()
        self.assertEqual(target.read_text(encoding="utf-8"), newer,
                         "revert() must restore only the current step's snapshot")


class MeasurementTests(unittest.TestCase):
    """measure() must see the current code, not a cached import."""

    def test_measure_reloads_data_health_and_extractors(self) -> None:
        import self_improve

        self_improve.measure()
        first_health = sys.modules.get("data_health")
        first_refine = sys.modules.get("extractors.summary_refine")
        self.assertIsNotNone(first_health, "data_health should be imported")
        self.assertIsNotNone(first_refine, "extractors.summary_refine should be imported")

        self_improve.measure()
        self.assertIsNot(
            sys.modules.get("data_health"), first_health,
            "measure() reused the cached data_health module: constant edits would "
            "never be observed and every fix would be reverted",
        )
        self.assertIsNot(
            sys.modules.get("extractors.summary_refine"), first_refine,
            "measure() reused cached extractors modules",
        )


class RegressionDirectionTests(unittest.TestCase):
    """A drop is only a regression on metrics where higher is better."""

    def test_le_metric_dropping_is_an_improvement(self) -> None:
        import self_improve
        from data_health import THRESHOLDS

        le_metrics = [m for m, (_limit, d) in THRESHOLDS.items() if d == "le"]
        self.assertTrue(le_metrics, "expected some 'le' metrics in the panel")
        for metric in le_metrics:
            with self.subTest(metric=metric):
                self.assertFalse(
                    self_improve.is_regression(metric, 0.60, 0.40),
                    f"{metric} is 'le': 0.60 -> 0.40 is an improvement",
                )
                self.assertTrue(
                    self_improve.is_regression(metric, 0.40, 0.60),
                    f"{metric} is 'le': 0.40 -> 0.60 is a regression",
                )

    def test_ge_metric_dropping_is_a_regression(self) -> None:
        import self_improve
        from data_health import THRESHOLDS

        ge_metrics = [m for m, (_limit, d) in THRESHOLDS.items() if d == "ge"]
        self.assertTrue(ge_metrics, "expected some 'ge' metrics in the panel")
        for metric in ge_metrics:
            with self.subTest(metric=metric):
                self.assertTrue(
                    self_improve.is_regression(metric, 0.60, 0.40),
                    f"{metric} is 'ge': 0.60 -> 0.40 is a regression",
                )
                self.assertFalse(
                    self_improve.is_regression(metric, 0.40, 0.60),
                    f"{metric} is 'ge': 0.40 -> 0.60 is an improvement",
                )

    def test_change_within_tolerance_is_not_a_regression(self) -> None:
        import self_improve

        self.assertFalse(self_improve.is_regression("summary_rate", 0.900, 0.895, tolerance=0.02))
        self.assertTrue(self_improve.is_regression("summary_rate", 0.900, 0.800, tolerance=0.02))


if __name__ == "__main__":
    unittest.main()
