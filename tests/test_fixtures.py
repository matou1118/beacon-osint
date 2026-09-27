from __future__ import annotations

import json
import unittest
from pathlib import Path

from collectors.municipal import MunicipalCollector
from collectors.provincial import ProvincialCollector
from main import load_config, resolve_sources


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTests(unittest.TestCase):
    def test_local_records_cover_all_scopes(self) -> None:
        records = json.loads((FIXTURES / "sample_announcements.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(records), 8)
        self.assertEqual(
            {record["gov_level"] for record in records},
            {"国家部委", "省级", "市级", "县级", None, "其他官方公共信息"},
        )
        self.assertIn("fixture_school", {record["source_id"] for record in records})
        self.assertIn("official_media", {record["source_type"] for record in records})
        self.assertTrue(all(record["category_l1"] for record in records))

    def test_provincial_css_selector(self) -> None:
        html = (FIXTURES / "provincial_list.html").read_text(encoding="utf-8")
        source = {
            "id": "fixture_province",
            "domain": "example.invalid",
            "url": "https://example.invalid/list",
            "list_selector": "main.notice-list",
            "detail_pattern": "/notice/",
        }
        collector = ProvincialCollector(source, client=object())
        self.assertEqual(
            collector._extract_links(html, "https://example.invalid/list"),
            [
                "https://example.invalid/sc/notice/1",
                "https://example.invalid/sc/notice/2",
            ],
        )
        collector.close()

    def test_municipal_css_selector_and_config_resolution(self) -> None:
        html = (FIXTURES / "municipal_list.html").read_text(encoding="utf-8")
        source = {
            "id": "fixture_city",
            "domain": "example.invalid",
            "url": "https://example.invalid/list",
            "list_selector": "section.list",
        }
        collector = MunicipalCollector(source, client=object())
        self.assertEqual(len(collector._extract_links(html, "https://example.invalid/list")), 2)
        collector.close()
        config = load_config("config/sources.yaml")
        sources = {source["id"]: source for source in resolve_sources(config)}
        self.assertEqual(sources["ndrc_gov"]["focus_level"], "critical")
        self.assertEqual(sources["nea_gov"]["focus_level"], "critical")
        self.assertEqual(sources["sc_gov"]["region_province"], "四川省")
        self.assertEqual(sources["cd_gov"]["gov_level"], "市级")

    def test_source_status_and_enabled_flag_never_disagree(self) -> None:
        """status 与 enabled 是同一概念的两个字段，必须始终一致。

        踩过的坑：批量补选择器时只把 status 改成 enabled，enabled 仍是原来
        blocked 时的 False，于是 49 个源处于「看起来已启用、实际采集器按
        enabled 过滤会跳过」的矛盾态。配置显示 101 个启用，真实生效只有 52 个。
        这类不一致从配置上完全看不出来，只能靠断言钉住。
        """
        config = load_config("config/sources.yaml")
        for source in resolve_sources(config):
            want = source.get("status") == "enabled"
            self.assertEqual(
                bool(source.get("enabled")), want,
                f"源 {source.get('id')} 的 status={source.get('status')} 与 "
                f"enabled={source.get('enabled')} 矛盾",
            )

    def test_enabled_sources_have_enough_config_to_collect(self) -> None:
        """启用的源至少要有可抓的入口。

        不要求 list_selector：`fetch_list` 在没有选择器时会走通用启发式，
        实测 51 个无选择器的启用源里有 50 个能正常产出记录（mof_gov、js_kjt 等），
        所以"缺选择器"不等于"不可用"，写死成断言反而会拦住正常配置。
        """
        config = load_config("config/sources.yaml")
        enabled = [s for s in resolve_sources(config)
                   if s.get("status") == "enabled" and s.get("enabled", True)]
        # 数量回退守卫：2026-09-27 从 52 扩到 101，掉回去说明配置被覆盖了
        self.assertGreaterEqual(len(enabled), 100, "启用源数量回退了")
        for source in enabled:
            self.assertTrue(source.get("url") or source.get("rss_url"),
                            f"启用的源 {source.get('id')} 没有 url")
            self.assertTrue(source.get("access_method"),
                            f"启用的源 {source.get('id')} 没有 access_method")


if __name__ == "__main__":
    unittest.main()
