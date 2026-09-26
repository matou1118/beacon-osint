"""Tests for the workbench comparison layer and the collector ops layer.

The comparison numbers come from real scraped text, so the cases below are the
failures that actually happened on the store rather than invented ones:

* "1000万吨/年" (coal-mine capacity) normalised to a multi-hundred-million-yuan
  subsidy, because a number followed by 万/亿 is usually not money.
* "海南省人民政府网" (a portal page) contributed 1.7 trillion yuan to a
  comparison, because list-page scraping let site names into the table.
* A news interview yielded "5万元", which is not a comparable policy fact.
"""
from __future__ import annotations

import unittest

from collector_ops import (
    STATUS_LABEL,
    provinces_without_enabled,
    read_improve_log,
    read_progress,
    skew_warning,
    summarize_registry,
)
from compare_topics import (
    build_comparison,
    build_topics,
    is_document,
    is_portal_page,
    parse_amount,
    to_csv,
    to_markdown,
)


class AmountParsingTests(unittest.TestCase):
    def test_money_with_adjacent_context_is_accepted(self) -> None:
        self.assertEqual(parse_amount("支持标准为500万元，申报主体为农药经营企业"), 5_000_000)
        self.assertEqual(parse_amount("安排资金2.4亿元支持养老服务设施建设"), 240_000_000)

    def test_capacity_is_not_money(self) -> None:
        # 1000万吨/年 is annual capacity. Both of these were real records.
        for text in (
            "晋能控股集团有限公司大同矿区潘家窑矿井及选煤厂项目（1000万吨/年）环境影响评价",
            "拟对内蒙古永和氟化工有限公司2026年度氢氟碳化物生产配额（100万吨）",
        ):
            with self.subTest(text=text[:24]):
                self.assertIsNone(parse_amount(text))

    def test_number_without_money_word_is_not_money(self) -> None:
        self.assertIsNone(parse_amount("共有221家企业取得民用核安全设备许可证"))
        self.assertIsNone(parse_amount("截至2023年12月31日"))

    def test_document_number_and_phone_are_not_money(self) -> None:
        self.assertIsNone(parse_amount("文号：文旅非遗发〔2026〕6号"))
        self.assertIsNone(parse_amount("联系电话：010-12345678"))

    def test_out_of_bounds_is_rejected(self) -> None:
        # Below the floor it is a count; above the ceiling it is a concatenation.
        self.assertIsNone(parse_amount("安排资金5元"))
        self.assertIsNone(parse_amount("安排资金99999999999999万元"))


class PageClassificationTests(unittest.TestCase):
    def test_portal_pages_are_detected(self) -> None:
        for title in ("海南省人民政府网", "四川省商务厅-外贸全周期服务专栏",
                      "关于本站", "宁波12345政务服务", "政务联播 - 四川省人民政府网站"):
            with self.subTest(title=title):
                self.assertTrue(is_portal_page({"title": title}))

    def test_real_documents_are_kept(self) -> None:
        for title in (
            "国务院关于印发《全国统一大市场建设指引》的通知",
            "民政部办公厅关于印发2026年养老服务设施建设专项的通知",
            "文化和旅游部关于印发《非物质文化遗产保护传承“十五五”规划》的通知",
        ):
            with self.subTest(title=title[:20]):
                self.assertFalse(is_portal_page({"title": title}))

    def test_news_is_not_a_document(self) -> None:
        self.assertFalse(is_document({"title": "《顶流》访谈院士：新型电网“新”在哪里",
                                      "category_l1": "新闻动态"}))
        self.assertTrue(is_document({"title": "关于开展2026年申报工作的通知",
                                     "category_l1": "行业政策"}))


def _record(**over):
    base = {
        "id": 1, "title": "关于开展2026年养老服务设施建设专项的通知",
        "category_l1": "民生保障", "category_l2": "养老服务",
        "gov_level": "省级", "region_province": "四川省", "region_city": None,
        "region_county": None, "publish_date": "2026-03-01",
        "content": "安排资金500万元支持养老服务设施建设。", "amount": None,
        "deadline": "2026-09-30", "source_url": "https://example.gov.cn/a.html",
        "issuing_body": "四川省民政厅", "summary": "",
        "_amount_value": 5_000_000, "_portal": False, "_document": True,
    }
    base.update(over)
    return base


class TopicBuildingTests(unittest.TestCase):
    def test_junk_bucket_is_not_a_topic(self) -> None:
        records = [
            _record(id=i, title=f"无关记录{i}", category_l1="其他", category_l2="未分类")
            for i in range(5)
        ]
        records += [
            _record(id=90, category_l1="民生保障", category_l2="养老服务", region_province="四川省"),
            _record(id=91, category_l1="民生保障", category_l2="养老服务", region_province="广东省"),
        ]
        topics = build_topics(records)
        # The 5 其他 rows are dropped entirely, not merely ranked lower.
        self.assertEqual([t["name"] for t in topics], ["民生保障 · 养老服务"])
        self.assertEqual(topics[0]["doc_count"], 2)

    def test_portal_rows_never_enter_a_topic(self) -> None:
        records = [
            _record(id=1, _portal=True, title="云南省人民政府门户网站", region_province="云南省"),
            _record(id=2, region_province="四川省"),
            _record(id=3, region_province="广东省"),
        ]
        topics = build_topics(records)
        self.assertEqual(topics[0]["doc_count"], 2)
        titles = [r["title"] for r in topics[0]["documents"]]
        self.assertNotIn("云南省人民政府门户网站", titles)

    def test_cross_region_topic_is_marked_comparable(self) -> None:
        records = [
            _record(id=1, region_province="四川省"),
            _record(id=2, region_province="广东省", gov_level="国家级",
                    _amount_value=8_000_000),
        ]
        topics = build_topics(records)
        self.assertTrue(topics[0]["comparable"])
        self.assertEqual(topics[0]["region_count"], 2)

    def test_single_document_topic_is_dropped(self) -> None:
        self.assertEqual(build_topics([_record()]), [])


class ComparisonTests(unittest.TestCase):
    def _topic(self, records):
        return build_topics(records)[0]

    def test_differing_amounts_are_marked_diff(self) -> None:
        records = [
            _record(id=1, region_province="四川省", _amount_value=5_000_000),
            _record(id=2, region_province="广东省", _amount_value=8_000_000),
        ]
        result = build_comparison(self._topic(records))
        self.assertEqual(result["field_states"]["amount"], "diff")
        self.assertEqual(len(result["rows"]), 2)

    def test_identical_amounts_are_marked_same(self) -> None:
        records = [
            _record(id=1, region_province="四川省", _amount_value=5_000_000),
            _record(id=2, region_province="广东省", _amount_value=5_000_000),
        ]
        result = build_comparison(self._topic(records))
        self.assertEqual(result["field_states"]["amount"], "same")

    def test_missing_amount_is_missing_not_zero(self) -> None:
        records = [
            _record(id=1, region_province="四川省", _amount_value=5_000_000),
            _record(id=2, region_province="广东省", _amount_value=None),
        ]
        result = build_comparison(self._topic(records))
        row = next(r for r in result["rows"] if r["region"] == "广东省")
        self.assertIsNone(row["amount"])
        self.assertIn("未出台", to_markdown(result))

    def test_news_amount_is_pending_not_a_difference(self) -> None:
        # Same category so both rows land in one topic; only the 广东 row is news.
        records = [
            _record(id=1, region_province="四川省", _amount_value=5_000_000),
            _record(id=2, region_province="广东省", _amount_value=5_000_000,
                    _document=False, category_l1="民生保障", category_l2="养老服务"),
        ]
        result = build_comparison(self._topic(records))
        self.assertEqual(result["field_states"]["amount"], "pending")
        self.assertIn("待确认", to_markdown(result))
        news_row = next(r for r in result["rows"] if r["region"] == "广东省")
        self.assertEqual(news_row["amount_state"], "pending")
        self.assertEqual(news_row["amount"], None)

    def test_exports_carry_regions_and_links(self) -> None:
        records = [
            _record(id=1, region_province="四川省"),
            _record(id=2, region_province="广东省", _amount_value=8_000_000),
        ]
        result = build_comparison(self._topic(records))
        markdown, csv_text = to_markdown(result), to_csv(result)
        self.assertIn("四川省", markdown)
        self.assertIn("广东省", markdown)
        self.assertIn("https://example.gov.cn/a.html", markdown)
        self.assertIn("原文", markdown)
        self.assertEqual(len(csv_text.strip().splitlines()), 3)  # header + 2 regions


class CollectorOpsTests(unittest.TestCase):
    def _groups(self):
        return {"政府源": [
            {"id": "a", "status": "enabled", "enabled": True, "gov_level": "省级",
             "region_province": "四川省", "notes": ""},
            {"id": "b", "status": "enabled", "enabled": True, "gov_level": "省级",
             "region_province": "四川省", "notes": ""},
            {"id": "c", "status": "blocked", "enabled": False, "gov_level": "省级",
             "region_province": "江西省", "notes": "robots.txt disallows /gkml/"},
            {"id": "d", "status": "pending_review", "enabled": False, "gov_level": "省级",
             "region_province": "山东省", "notes": ""},
            {"id": "e", "status": "disabled", "enabled": False, "gov_level": "特别行政区",
             "region_province": "香港特别行政区", "notes": "特区关闭"},
        ]}

    def test_summary_counts_robots_block(self) -> None:
        summary = summarize_registry(self._groups())
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["by_status_labeled"]["启用"], 2)
        self.assertEqual(summary["robots_blocked"], 1)

    def test_skew_is_reported(self) -> None:
        warning = skew_warning(summarize_registry(self._groups()))
        self.assertIsNotNone(warning)
        self.assertIn("四川", warning)

    def test_sar_is_not_a_coverage_gap(self) -> None:
        gaps = provinces_without_enabled(self._groups())
        self.assertIn("江西省", gaps)
        self.assertIn("山东省", gaps)
        self.assertNotIn("香港特别行政区", gaps)

    def test_progress_may_be_absent_but_not_fatal(self) -> None:
        progress = read_progress()
        self.assertTrue(progress is None or isinstance(progress, dict))

    def test_improve_log_is_a_list(self) -> None:
        self.assertIsInstance(read_improve_log(), list)

    def test_status_labels_cover_registry_values(self) -> None:
        self.assertIn("enabled", STATUS_LABEL)
        self.assertIn("blocked", STATUS_LABEL)


if __name__ == "__main__":
    unittest.main()
