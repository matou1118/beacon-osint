from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from output.markdown import build_rule_summary, render_record_markdown, write_markdown_records


class MarkdownOutputTests(unittest.TestCase):
    def test_rule_summary_and_markdown_are_deterministic(self) -> None:
        record = {
            "id": 7,
            "source_id": "sc_gov",
            "title": "政策通知",
            "issuing_body": "四川省人民政府",
            "source_url": "https://example.gov.cn/notice/7",
            "publish_date": "2026-01-01",
            "content": "面向企业提供政策支持。",
            "content_hash": "abc",
            "key_points": ["面向企业", "截止日期为2026-04-30"],
            "target_group": "企业",
            "amount": "100万元",
        }
        self.assertIn("面向企业", build_rule_summary(record))
        markdown = render_record_markdown(record)
        self.assertIn("## 规则摘要", markdown)
        self.assertIn("## 原文", markdown)
        with tempfile.TemporaryDirectory() as directory:
            result = write_markdown_records([record], directory)
            self.assertEqual(result["files"], 1)
            self.assertTrue(Path(result["index"]).exists())
            self.assertTrue((Path(directory) / "sc_gov_7.md").exists())


if __name__ == "__main__":
    unittest.main()
