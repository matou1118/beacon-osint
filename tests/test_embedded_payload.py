"""内置仪表盘的体积护栏。

echarts/three.js 与三级 geojson 归一化后仍有 20MB，内联进 iframe srcdoc 时每次
rerun 都要重传+重解析（实测切到中国地图要 8 秒）。它们必须走 /app/static/ 由浏览器
缓存 —— 下面的断言就是防止有人改回内联。
"""

from __future__ import annotations

import json
import unittest

from home_dashboard import _compute_home_stats, _home_html
from map_dashboard import _map_script, build_map_context

PAYLOAD_BUDGET = 200_000


class EmbeddedPayloadTests(unittest.TestCase):
    def test_home_html_refers_to_static_vendor(self) -> None:
        stats = _compute_home_stats([], [], {}, {})
        html = _home_html(stats, "cyber_neon")
        self.assertIn('src="/app/static/vendor/echarts.min.js"', html)
        self.assertIn('src="/app/static/vendor/three.min.js"', html)
        self.assertLess(len(html), PAYLOAD_BUDGET, "首页 HTML 超过 200KB：vendor 脚本又被内联回来了？")

    def test_map_script_refers_to_static_geojson(self) -> None:
        context = build_map_context([])
        script = _map_script("cyber_neon", context)
        self.assertIn("const GEO_BASE = '/app/static/geo';", script)
        self.assertNotIn('"geometry"', script, "geojson 边界又被内联进地图脚本了")
        self.assertLess(len(script), PAYLOAD_BUDGET, "地图脚本超过 200KB：数据被内联回来了？")

    def test_map_script_injects_records(self) -> None:
        """DATA 占位符只能被替换一次：替换两次会让前端拿到空对象，地图无数据。"""
        record = {
            "id": 1,
            "title": "政策通知",
            "publish_date": "2026-01-01",
            "region_province": "四川省",
            "category_l1": "行业政策",
            "needs_review": False,
        }
        script = _map_script("cyber_neon", build_map_context([record]))
        payload = json.loads(script.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual([item["id"] for item in payload["records"]], [1])


if __name__ == "__main__":
    unittest.main()
