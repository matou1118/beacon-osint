from __future__ import annotations

import unittest

from pipeline import run_native_pipeline


class PipelineTests(unittest.TestCase):
    def test_native_pipeline_runs_extraction_and_metadata_in_parallel(self) -> None:
        result = run_native_pipeline(
            {"url": "https://example.gov.cn"},
            crawl=lambda payload: {"content": "正文", "url": payload["url"]},
            summary=lambda crawled: "要点",
            extraction=lambda crawled: {"title": "标题"},
            metadata=lambda crawled: {"source": crawled["url"]},
            formatter=lambda result: result,
        )
        self.assertEqual(result["summary"], "要点")
        self.assertEqual(result["structured"], {"title": "标题"})
        self.assertEqual(result["metadata"], {"source": "https://example.gov.cn"})


if __name__ == "__main__":
    unittest.main()
