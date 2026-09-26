from __future__ import annotations

import unittest

from map_dashboard import _safe_script_json, build_map_context, map_geojson


class MapDashboardTests(unittest.TestCase):
    def test_context_contains_hierarchy_without_raw_content(self) -> None:
        context = build_map_context(
            [
                {
                    "id": 1,
                    "title": "政策通知",
                    "content": "不应进入地图数据",
                    "source_url": "https://example.gov.cn/1",
                    "publish_date": "2026-01-01",
                    "region_province": "四川省",
                    "region_city": "成都市",
                    "region_county": "武侯区",
                    "category_l1": "行业政策",
                    "issuing_body": "官方部门",
                    "needs_review": False,
                }
            ]
        )
        self.assertEqual(len(context["geojson"]["province"]["features"]), 35)
        self.assertEqual(len(context["geojson"]["city"]["features"]), 372)
        self.assertEqual(context["lookups"]["province"]["四川省"], "510000")
        self.assertNotIn("content", context["records"][0])
        self.assertEqual(context["years"], ["2026"])

    def test_geojson_normalizes_codes(self) -> None:
        province = map_geojson("province")
        feature = next(item for item in province["features"] if item["properties"]["adcode"] == "510000")
        self.assertEqual(feature["properties"]["name"], "四川省")
        self.assertEqual(feature["properties"]["level"], "province")

    def test_script_json_escapes_script_terminator(self) -> None:
        value = _safe_script_json({"title": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", value)
        self.assertIn("<\\/script>", value)


if __name__ == "__main__":
    unittest.main()
