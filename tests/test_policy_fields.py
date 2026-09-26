from __future__ import annotations

import unittest

from policy_fields import FIELD_SCHEMA, extract_fields, key_measures_from


class ExtractFieldsTests(unittest.TestCase):
    def test_structured_column_wins_with_auto_confidence(self) -> None:
        record = {"amount": "每人每月 500 元", "content": "正文没有金额句式"}
        results = {r["field_key"]: r for r in extract_fields(record)}
        amount = results["subsidy_amount"]
        self.assertEqual(amount["value"], "每人每月 500 元")
        self.assertEqual(amount["status"], "auto")
        self.assertGreaterEqual(amount["confidence"], 80)
        self.assertIn("结构化字段 amount", amount["evidence_text"])

    def test_body_fallback_used_when_column_missing(self) -> None:
        record = {"content": "对符合条件人员给予补贴，标准为每人每月不超过300元。逾期不再受理。"}
        results = {r["field_key"]: r for r in extract_fields(record)}
        amount = results["subsidy_amount"]
        self.assertIsNotNone(amount["value"])
        self.assertIn("300", amount["value"])
        self.assertEqual(amount["method"], "rule")
        self.assertIn("正文命中", amount["evidence_text"])

    def test_missing_everywhere_lands_pending_with_null_value(self) -> None:
        record = {"content": "与补贴无关的普通通知内容"}
        results = {r["field_key"]: r for r in extract_fields(record)}
        for key in ("subsidy_amount", "deadline"):
            self.assertIsNone(results[key]["value"])
            self.assertEqual(results[key]["status"], "pending")
            self.assertEqual(results[key]["confidence"], 0)

    def test_schema_can_be_narrowed(self) -> None:
        record = {"region_province": "甘肃省"}
        schema = [{"field_key": "region", "label": "所属地区", "value_column": "region_province"}]
        results = extract_fields(record, schema)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["field_key"], "region")
        self.assertEqual(results[0]["value"], "甘肃省")

    def test_default_schema_covers_all_keys(self) -> None:
        keys = {spec["field_key"] for spec in FIELD_SCHEMA}
        self.assertEqual(keys, {"subsidy_amount", "target_group", "deadline", "institution", "region"})

    def test_key_measures_joins_points(self) -> None:
        record = {"key_points": ["凭身份证申报", "季度发放"]}
        item = key_measures_from(record)
        self.assertEqual(item["value"], "凭身份证申报；季度发放")
        self.assertEqual(item["status"], "pending")

    def test_key_measures_empty_is_pending(self) -> None:
        item = key_measures_from({"key_points": []})
        self.assertIsNone(item["value"])
        self.assertEqual(item["status"], "pending")


if __name__ == "__main__":
    unittest.main()
