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
        for source in enabled:
            self.assertTrue(source.get("url") or source.get("rss_url"),
                            f"启用的源 {source.get('id')} 没有 url")
            self.assertTrue(source.get("access_method"),
                            f"启用的源 {source.get('id')} 没有 access_method")

    def test_enabled_sources_match_configured_scope(self) -> None:
        """启用的源必须和 config 的 collect_scope 白名单一致。

        原来的守卫是写死的「≥100 个」。2026-09-30 按用户要求改成白名单制
        （只采部委/北京/上海/广州/深圳/成都/重庆/浙江/江苏/武汉），enabled 源
        从 114 降到 34，写死的数字就变成了挡路的东西 —— 但「范围不许漂移」
        这个守卫本身是对的，它得跟着配置走。

        这样改还有个好处：以后往白名单里加地方，这个测试立刻会因为「注册表还没
        同步」而失败，指向 scripts/apply_collect_scope.py，而不是默默跑偏。
        """
        import yaml

        collector_cfg = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "lighthouse" / "config"
             / "web_collector_config.yaml").read_text(encoding="utf-8")) or {}
        scope = (collector_cfg.get("collect_scope") or {}).get("include") or {}
        gov_levels = {str(x) for x in (scope.get("gov_level") or [])}
        regions = {str(x) for x in (scope.get("region") or [])}
        if not gov_levels and not regions:
            self.skipTest("config 里没配 collect_scope，跳过范围一致性检查")

        def wanted(source: dict) -> bool:
            province = str(source.get("region_province") or "")
            city = str(source.get("region_city") or "")
            return (str(source.get("gov_level") or "") in gov_levels
                    or province in regions or city in regions
                    or any(province.startswith(r) or city.startswith(r)
                           for r in regions))

        registry = load_config("config/sources.yaml")
        on = [s for s in resolve_sources(registry)
              if s.get("status") == "enabled" and s.get("enabled", True)]
        out_of_scope = [s["id"] for s in on if not wanted(s)]
        self.assertEqual(out_of_scope, [],
                         f"这些启用源不在 collect_scope 白名单里：{out_of_scope}")

        excluded = {str(x) for x in
                    ((collector_cfg.get("collect_scope") or {}).get("exclude") or [])}
        leaked = [s["id"] for s in on
                  if str(s.get("region_province") or "") in excluded
                  or str(s.get("region_city") or "") in excluded]
        self.assertEqual(leaked, [], f"这些被排除地区仍在采：{leaked}")


if __name__ == "__main__":
    unittest.main()
