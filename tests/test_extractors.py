from __future__ import annotations

import unittest

from extractors.category_rules import classify, load_category_rules
from extractors.gliner_extractor import Gliner2Extractor, build_schema
from extractors.regex_fallback import RegexFallbackExtractor


class FakeSchema:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _record(self, name: str, value: object) -> "FakeSchema":
        self.calls.append((name, value))
        return self

    def entities(self, value: object) -> "FakeSchema":
        return self._record("entities", value)

    def classification(self, name: str, values: object) -> "FakeSchema":
        return self._record("classification", (name, values))

    def structure(self, name: str) -> "FakeSchema":
        return self._record("structure", name)

    def field(self, name: str, dtype: str, description: str | None = None) -> "FakeSchema":
        return self._record("field", (name, dtype, description))


class FakeBackend:
    def __init__(self, result: dict) -> None:
        self.schema = FakeSchema()
        self.result = result
        self.extract_calls: list[tuple[str, object]] = []

    def create_schema(self) -> FakeSchema:
        return self.schema

    def extract(self, text: str, schema: object) -> dict:
        self.extract_calls.append((text, schema))
        return self.result


class ExtractorTests(unittest.TestCase):
    def test_builds_requested_gliner_schema(self) -> None:
        backend = FakeBackend({})
        extractor = Gliner2Extractor(backend=backend)
        schema = build_schema(extractor)
        self.assertIs(schema, backend.schema)
        names = [call[0] for call in schema.calls]
        self.assertIn("entities", names)
        self.assertIn("classification", names)
        self.assertIn("structure", names)
        self.assertEqual(names.count("field"), 13)

    def test_extraction_overrides_level_from_source_registry(self) -> None:
        backend = FakeBackend(
            {
                "title": "四川省市场监督管理局通知",
                "category_l1": "行业政策",
                "admission_sub_category": None,
                "key_points": ["通知正文中的原文句子"],
                "gov_level": "国家部委",
            }
        )
        extractor = Gliner2Extractor(backend=backend)
        result = extractor.extract(
            "四川省市场监督管理局通知正文",
            source={"gov_level": "省级", "jurisdiction_code": "510000"},
        )
        self.assertEqual(result["gov_level"], "省级")
        self.assertEqual(result["jurisdiction_code"], "510000")
        self.assertEqual(backend.extract_calls[0][1], backend.schema)

    def test_invalid_category_falls_back_and_marks_review(self) -> None:
        backend = FakeBackend({"category_l1": "不存在分类", "admission_sub_category": None})
        result = Gliner2Extractor(backend=backend).extract("正文")
        self.assertEqual(result["category_l1"], "其他")
        self.assertEqual(result["needs_review"], 1)

    def test_regex_fallback_extracts_date_amount_region_and_review_flag(self) -> None:
        result = RegexFallbackExtractor().extract(
            "<title>四川省项目通知</title>发布时间：2026-01-02，项目资金100万元，四川省成都市，联系电话：028-12345678。"
        )
        self.assertEqual(result["publish_date"], "2026-01-02")
        self.assertEqual(result["amount"], "100万元")
        self.assertEqual(result["region"], "四川省")
        self.assertEqual(result["needs_review"], 1)

    def test_regex_fallback_produces_a_summary_from_the_body(self) -> None:
        """extract() must hand the parsed body to the summariser.

        The result dict never carried a "content" key, yet the summarise step
        read result.get("content"), so every record collected through the normal
        pipeline came out with an empty summary and a no_usable_sentence repair
        -- 1409 of 1883 rows in the store, with summary_rate stuck at 0.25. Only
        a hand-run resummarize backfill produced summaries, so each collection
        round silently undid the last one's work.
        """
        body = (
            "<html><head><title>关于开展2026年度中小企业数字化转型试点工作的通知</title></head>"
            "<body><p>当前位置：首页 / 通知公告</p>"
            "<p>为深入推进中小企业数字化转型，四川省经济和信息化厅决定组织开展2026年度试点申报工作。</p>"
            "<p>申报截止日期为2026-04-30，试点补助资金为500万元，每个企业最高可获支持。</p>"
            "<p>申报企业须为省内注册的独立法人，成立满一年且上一年度营业收入不低于2000万元。</p>"
            "<p>请各申报企业按照通知要求准备材料，并于截止前提交至经信厅政务服务窗口。</p>"
            "<p>网站地图 联系我们 打印此页 主办单位 四川省经济和信息化厅</p>"
            "</body></html>"
        )
        result = RegexFallbackExtractor().extract(body)
        self.assertTrue(
            (result.get("summary") or "").strip(),
            "extract() 未产出摘要；正文没有被传给 build_summary",
        )
        self.assertNotIn("no_usable_sentence", result.get("summary_repairs") or [])
        self.assertTrue((result.get("content") or "").strip(), "清洗后正文不应为空")
        # 清洗后的正文必须还带着关键事实，否则摘要再短也没有对比价值。
        self.assertIn("2026-04-30", result["summary"] + result["content"])
        self.assertIn("500万元", result["summary"] + result["content"])

    def test_empty_summary_records_why(self) -> None:
        """空摘要必须带原因，否则库里 1409 条缺口无从解释。"""
        result = RegexFallbackExtractor().extract(
            "<title>网站地图</title><body>打印此页 网站地图 扫描二维码 当前位置</body>"
        )
        self.assertEqual((result.get("summary") or "").strip(), "")
        self.assertIn(
            result.get("summary_drop_reason"), {"chrome_only", "empty_body"},
            "空摘要必须记录 summary_drop_reason",
        )

    def test_category_rules_drill_into_l1_and_l2(self) -> None:
        rules = load_category_rules()
        cases = [
            ({"title": "关于2026年高职单招报名工作通知"}, "招生信息", "高职单招"),
            ({"title": "省市场监管局关于开展产品质量监督抽查的通告"}, "行业政策", "监管政策"),
            ({"title": "市人社局事业单位公开招聘工作人员公告"}, "公招信息", "事业单位招聘"),
            ({"title": "四川省2025年度考试录用公务员公告"}, "公务员考试", "省考招录"),
            ({"title": "中央机关及其直属机构2026年度考试录用公务员公告"}, "公务员考试", "国考招录"),
            ({"title": "关于面向服务基层项目人员定向招聘公务员的公告"}, "公务员考试", "选调生招录"),
            ({"title": "某企业面向社会招工岗位公告"}, "招聘信息", "招聘信息"),
            ({"title": "军队院校招生政治考核工作公告"}, "军队招录", "军队院校招生"),
            ({"title": "空军2026年招收普通高中毕业生飞行学员公告"}, "军队招录", "军队院校招生"),
            ({"title": "直招军官岗位报名公告"}, "军队招录", "军校直招"),
            ({"title": "2026年度人民警察招录公告"}, "公务员考试", "警察招录"),
            ({"title": "公安院校面向公安专业毕业生招生简章"}, "公务员考试", "警察招录"),
            ({"title": "监狱和戒毒人民警察招考公告"}, "公务员考试", "司法警官招录"),
            ({"title": "企业用工需求信息发布"}, "招聘信息", "招聘信息"),
            ({"title": "职业院校招生简章"}, "招生信息", "职业院校招生"),
            ({"title": "公务员笔试考试大纲及报名时间安排"}, "公务员考试", "考试安排"),
            ({"title": "某园区人才公寓优惠政策及招商公告"}, "商业机会", "招商引资"),
            ({"title": "高层次人才引进计划"}, "招聘信息", "人才引进"),
            ({"title": "关于2026年第7批新化学物质环境管理常规登记证申请审查情况的公示"}, "活动通知", "公示公告"),
            ({"title": "关于废止《塑料餐具》等13项企业标准的公告"}, "行业政策", "行业标准"),
            ({"title": "四川省市场监督管理局关于废止《塑料餐具》等13项企业标准的公告"}, "行业政策", "行业标准"),
            ({"title": "四川省物业服务领域行政处罚典型案例"}, "行业政策", "监管政策"),
            ({"title": "关于调整成品油价格的通知"}, "行业政策", "价格政策"),
            ({"title": "8月份全社会用电量再破万亿，负荷创历史新高"}, "新闻动态", "数据播报"),
            ({"title": "习近平：在加强基础研究座谈会上的讲话"}, "新闻动态", "时政要闻"),
            ({"title": "宝马（中国）汽车贸易有限公司召回部分进口汽车"}, "行业政策", "监管政策"),
            ({"title": "关于开展招商引资项目合作的通知"}, "商业机会", "招商引资"),
            ({"title": "产业园区招商公告"}, "商业机会", "园区招商"),
            ({"title": "关于开展适老化改造试点工作的通知"}, "养老与社区", "适老化改造"),
            ({"title": "会议通知"}, "其他", "未分类"),
        ]
        for record, expected_l1, expected_l2 in cases:
            with self.subTest(title=record["title"]):
                result = classify(record, rules)
                self.assertEqual(result["category_l1"], expected_l1)
                self.assertEqual(result["category_l2"], expected_l2)

    def test_category_rules_ignore_whitespace_and_case(self) -> None:
        rules = load_category_rules()
        self.assertEqual(
            classify({"title": "关于 2026 年 高职 单招 报名 的 通知"}, rules),
            classify({"title": "关于2026年高职单招报名的通知"}, rules),
        )

    def test_regex_fallback_assigns_category_l2_and_admission_sub_category(self) -> None:
        result = RegexFallbackExtractor().extract(
            "<title>关于2026年高职单招报名工作通知</title>",
            source={"name": "省教育厅", "gov_level": "省级"},
        )
        self.assertEqual(result["category_l1"], "招生信息")
        self.assertEqual(result["category_l2"], "高职单招")
        self.assertEqual(result["admission_sub_category"], "高职单招")

    def test_admission_sub_category_cleared_for_non_admission_categories(self) -> None:
        result = RegexFallbackExtractor().extract(
            "<title>省市场监管局关于开展产品质量监督抽查的通告</title>",
            source={"name": "省市场监管局", "gov_level": "省级"},
        )
        self.assertEqual(result["category_l1"], "行业政策")
        self.assertIsNone(result["admission_sub_category"])

    def test_title_outweighs_navigation_noise_in_body(self) -> None:
        rules = load_category_rules()
        noisy_nav = "十五五 规划 监管 质量安全 行政处罚 收费标准 产业发展 专栏 政务公开"
        result = classify({"title": "关于2026年高职单招报名工作通知", "content": noisy_nav}, rules)
        self.assertEqual(result["category_l1"], "招生信息")
        self.assertEqual(result["category_l2"], "高职单招")

    def test_statistics_mention_does_not_become_industry_standard(self) -> None:
        rules = load_category_rules()
        result = classify(
            {"title": "2026年8月全国电动汽车充电设施数据情况", "content": "全国充电基础设施运行情况通报"}, rules
        )
        self.assertNotEqual(result["category_l2"], "行业标准")

    def test_planning_document_in_title_still_classifies(self) -> None:
        rules = load_category_rules()
        result = classify(
            {"title": "文化和旅游部关于印发《文化产业发展“十五五”规划》的通知", "content": ""}, rules
        )
        self.assertEqual(result["category_l1"], "行业政策")
        self.assertEqual(result["category_l2"], "产业规划")


if __name__ == "__main__":
    unittest.main()
