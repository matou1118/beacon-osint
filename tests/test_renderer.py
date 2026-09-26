from __future__ import annotations

import unittest

from output.renderer import Renderer


class RendererTests(unittest.TestCase):
    def test_renders_ai_summary_markdown_template(self) -> None:
        payload = {
            "url": "https://example.gov.cn/policy/1",
            "source_name": "示例政府",
            "title": "政策标题",
            "summary": "这是经过证据约束的政策摘要。",
            "key_points": ["要点一", "要点二"],
            "data_points": [{"type": "GDP增速", "value": "5.2", "unit": "%", "context": "上半年"}],
            "entities": {"orgs": ["示例政府"], "industries": ["新能源汽车"]},
            "info_type": "政策文件",
            "crawl_time": "2026-09-25T14:30:00+08:00",
            "needs_review": 0,
        }
        result = Renderer().render_summary(payload)
        self.assertIn("# 政策标题", result)
        self.assertIn("## 📋 总体摘要", result)
        self.assertIn("GDP增速", result)
        self.assertIn("本摘要由 AI 生成", result)


if __name__ == "__main__":
    unittest.main()
