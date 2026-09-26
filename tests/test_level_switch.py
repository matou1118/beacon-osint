from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from scheduler import apply_level_switch

ROOT = Path(__file__).resolve().parents[1]
SWITCH_PATH = ROOT / "config" / "scheduler.yaml"


def source(source_id: str, level: str, province: str | None = None) -> dict:
    return {
        "id": source_id,
        "status": "enabled",
        "enabled": True,
        "gov_level": level,
        "region_province": province,
    }


class LevelSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(SWITCH_PATH.read_text(encoding="utf-8"))
        self.switch = self.config["level_switch"]

    def test_config_declares_the_switch(self) -> None:
        for key in ("national_enabled", "province_enabled", "city_enabled", "county_enabled", "sar_enabled"):
            self.assertIn(key, self.switch)
        # Lower levels and the SARs are not a focus right now.
        self.assertFalse(self.switch["county_enabled"])
        self.assertFalse(self.switch["sar_enabled"])

    def test_county_sources_are_skipped_while_disabled(self) -> None:
        sources = [
            source("a_gov", "国家部委"),
            source("b_gov", "省级", "四川省"),
            source("c_county", "县级", "四川省"),
        ]
        kept, report = apply_level_switch(sources, self.switch)
        self.assertEqual([s["id"] for s in kept], ["a_gov", "b_gov"])
        self.assertEqual(report["dropped"], 1)

    def test_enabling_county_lets_them_through(self) -> None:
        sources = [source("c_county", "县级", "四川省")]
        switch = {**self.switch, "county_enabled": True}
        kept, _ = apply_level_switch(sources, switch)
        self.assertEqual(len(kept), 1)

    def test_sample_caps_bound_a_run(self) -> None:
        sources = [source(f"p{i}_gov", "省级", "四川省") for i in range(10)]
        switch = {**self.switch, "province_sample": 3}
        kept, report = apply_level_switch(sources, switch)
        self.assertEqual(len(kept), 3)
        self.assertEqual(report["dropped"], 7)

    def test_zero_sample_means_no_cap(self) -> None:
        sources = [source(f"p{i}_gov", "省级", "四川省") for i in range(8)]
        switch = {**self.switch, "province_sample": 0}
        kept, _ = apply_level_switch(sources, switch)
        self.assertEqual(len(kept), 8)

    def test_empty_switch_keeps_everything(self) -> None:
        sources = [
            source("a", "国家部委"),
            source("b", "县级", "四川省"),
            source("c", "市级", "四川省"),
        ]
        kept, _ = apply_level_switch(sources, {})
        self.assertEqual(len(kept), 3)

    def test_municipality_and_sar_follow_province_flag(self) -> None:
        sources = [
            source("bj", "直辖市", "北京市"),
            source("cd", "县级", "四川省"),
        ]
        kept, _ = apply_level_switch(sources, self.switch)
        self.assertEqual([s["id"] for s in kept], ["bj"])
        off = {**self.switch, "province_enabled": False}
        kept_off, _ = apply_level_switch(sources, off)
        self.assertEqual(kept_off, [])

    def test_special_administrative_regions_are_off_by_default(self) -> None:
        sources = [
            source("hk", "特别行政区", "香港特别行政区"),
            source("mo", "特别行政区", "澳门特别行政区"),
            source("gd", "省级", "广东省"),
        ]
        kept, _ = apply_level_switch(sources, self.switch)
        self.assertEqual([s["id"] for s in kept], ["gd"])
        on = {**self.switch, "sar_enabled": True}
        kept_on, _ = apply_level_switch(sources, on)
        self.assertEqual(len(kept_on), 3)


if __name__ == "__main__":
    unittest.main()
