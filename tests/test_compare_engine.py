from __future__ import annotations

import unittest

from compare_engine import build_matrix, has_extra_clause, normalize_value, to_csv_rows

SCHEMA = [
    {"field_key": "subsidy_amount", "label": "补贴标准"},
    {"field_key": "deadline", "label": "申报截止"},
]


def _docs() -> list[dict]:
    return [
        {"id": 1, "region": "甘肃省", "title": "甘肃省方案", "retention_class": "active", "url": "u1", "published_at": "2026-01-01"},
        {"id": 2, "region": "陕西省", "title": "陕西省方案", "retention_class": "active", "url": "u2", "published_at": "2026-01-02"},
        {"id": 3, "region": "青海省", "title": "青海省方案", "retention_class": "active", "url": "u3", "published_at": "2026-01-03"},
        {"id": 4, "region": "宁夏", "title": "宁夏方案", "retention_class": "revoked", "url": "u4", "published_at": "2026-01-04"},
    ]


def _extractions() -> dict[int, dict[str, dict]]:
    return {
        1: {"subsidy_amount": {"value": "每月500元", "status": "auto", "confidence": 90, "evidence_text": "结构化字段 amount"}},
        2: {"subsidy_amount": {"value": "每月600元", "status": "auto", "confidence": 90, "evidence_text": "结构化字段 amount"}},
        3: {"subsidy_amount": {"value": "每月500元。同时需参加养老保险", "status": "auto", "confidence": 85, "evidence_text": "正文命中"}},
    }


class BuildMatrixTests(unittest.TestCase):
    def test_diff_detected_for_diverging_values(self) -> None:
        matrix = build_matrix(_docs(), _extractions(), SCHEMA)
        amount = matrix["rows"][0]
        self.assertEqual(amount["cells"]["甘肃省"]["diff_type"], "diff")
        self.assertEqual(amount["cells"]["陕西省"]["diff_type"], "diff")
        self.assertEqual(amount["counts"]["diff"], 3)  # 青海口径也与其他地不同

    def test_same_after_normalization(self) -> None:
        docs = [d for d in _docs() if d["region"] != "青海省"]  # 去掉无提取的青海，避免 pending 干扰
        extractions = {
            1: {"deadline": {"value": "12月31日前申报。", "status": "auto", "confidence": 80, "evidence_text": ""}},
            2: {"deadline": {"value": "12 月 31 日前申报", "status": "auto", "confidence": 80, "evidence_text": ""}},
        }
        matrix = build_matrix(docs, extractions, SCHEMA)
        deadline = matrix["rows"][1]
        self.assertEqual(deadline["cells"]["甘肃省"]["diff_type"], "same")
        self.assertEqual(deadline["cells"]["陕西省"]["diff_type"], "same")
        self.assertIn("一致", deadline["summary"])

    def test_missing_region_gets_missing_cell(self) -> None:
        docs = [d for d in _docs() if d["region"] != "青海省"]  # 青海无文档
        matrix = build_matrix(docs, _extractions(), SCHEMA, expected_regions=["青海省"])
        amount = matrix["rows"][0]
        self.assertEqual(amount["cells"]["青海省"]["diff_type"], "missing")
        self.assertIsNone(amount["cells"]["青海省"]["announcement_id"])
        self.assertIn("未出台", amount["summary"])

    def test_pending_extraction_stays_pending(self) -> None:
        extractions = {
            1: {"subsidy_amount": {"value": "每月500元", "status": "pending", "confidence": 55, "evidence_text": "正文命中"}},
            2: {"subsidy_amount": {"value": None, "status": "pending", "confidence": 0, "evidence_text": "未命中"}},
        }
        matrix = build_matrix(_docs(), extractions, SCHEMA)
        cells = matrix["rows"][0]["cells"]
        self.assertEqual(cells["甘肃省"]["diff_type"], "pending")
        self.assertEqual(cells["陕西省"]["diff_type"], "pending")

    def test_revoked_document_marks_cell(self) -> None:
        matrix = build_matrix(_docs(), _extractions(), SCHEMA)
        self.assertEqual(matrix["rows"][0]["cells"]["宁夏"]["diff_type"], "revoked")

    def test_extra_clause_flags_overlay(self) -> None:
        matrix = build_matrix(_docs(), _extractions(), SCHEMA)
        qinghai = matrix["rows"][0]["cells"]["青海省"]
        self.assertTrue(qinghai["extra"])
        self.assertFalse(matrix["rows"][0]["cells"]["甘肃省"]["extra"])

    def test_counts_aggregate_across_rows(self) -> None:
        matrix = build_matrix(_docs(), _extractions(), SCHEMA)
        counts = matrix["counts"]
        # amount 行：甘/陕/青 3 地口径不一 + 宁夏 revoked；deadline 行：3 地无提取 pending + 宁夏 revoked
        self.assertEqual(counts["revoked"], 2)
        self.assertEqual(counts["missing"], 0)
        self.assertEqual(counts["pending"], 3)
        self.assertEqual(counts["diff"], 3)
        self.assertIsNone(matrix["rows"][1]["cells"]["青海省"].get("value"))


class CsvExportTests(unittest.TestCase):
    def test_to_csv_rows_shape(self) -> None:
        matrix = build_matrix(_docs(), _extractions(), SCHEMA)
        rows = to_csv_rows(matrix)
        self.assertEqual(len(rows), 3)
        header = rows[0]
        self.assertEqual(header[0], "字段")
        self.assertIn("甘肃省·状态", header)
        self.assertEqual(rows[1][0], "补贴标准")


class NormalizeValueTests(unittest.TestCase):
    def test_punctuation_and_space_insensitive(self) -> None:
        self.assertEqual(normalize_value("每月 500 元。"), normalize_value("每月500元"))
        self.assertEqual(normalize_value("ABC"), "abc")

    def test_extra_clause_detection(self) -> None:
        self.assertTrue(has_extra_clause("需先参保。同时不得重复领取"))
        self.assertFalse(has_extra_clause("正常表述"))


if __name__ == "__main__":
    unittest.main()
