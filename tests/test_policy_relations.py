from __future__ import annotations

import unittest

from policy_relations import link_status, normalize, score_link

TOPIC = {
    "name": "灵活就业人员社保补贴",
    "category": "民政保障",
    "keywords": ["灵活就业", "社保补贴", "就业困难人员"],
}


class ScoreLinkTests(unittest.TestCase):
    def test_title_hits_dominate_and_reach_auto(self) -> None:
        record = {
            "title": "关于做好灵活就业人员社保补贴工作的通知",
            "content": "重点支持就业困难人员，具体见附件。",
            "category_l1": "民政保障",
            "gov_level": "国家级",
        }
        confidence, evidence = score_link(record, TOPIC)
        # 标题 2 命中(60) + 正文 1 命中(15) + 分类匹配(10) + 国家级(5) = 90
        self.assertGreaterEqual(confidence, 90)
        self.assertIn("灵活就业", evidence["title_hits"])
        self.assertTrue(evidence["category_match"])

    def test_body_only_cooccurrence_reaches_pending(self) -> None:
        record = {
            "title": "某市政务服务优化提升工作方案",
            "content": "文件提到灵活就业群体参保，并要求落实社保补贴政策。",
            "category_l1": "其他",
            "gov_level": "市级",
        }
        confidence, evidence = score_link(record, TOPIC)
        self.assertLess(confidence, 60)
        self.assertGreaterEqual(len(evidence["body_hits"]), 2)
        self.assertEqual(link_status(confidence, len(evidence["body_hits"])), "pending")

    def test_single_body_mention_is_dropped(self) -> None:
        record = {
            "title": "住房城乡建设新闻发布会实录",
            "content": "灵活就业群体规模快速增长。",
            "category_l1": "其他",
            "gov_level": "国家部委",
        }
        confidence, evidence = score_link(record, TOPIC)
        self.assertIsNone(link_status(confidence, len(evidence["body_hits"])))

    def test_no_hits_scores_below_floor(self) -> None:
        record = {"title": "公路招标公告", "content": "项目施工招标。", "category_l1": "招标信息", "gov_level": "县级"}
        confidence, evidence = score_link(record, TOPIC)
        self.assertEqual(confidence, 0)
        self.assertEqual(evidence["title_hits"], [])

    def test_national_level_earns_bonus(self) -> None:
        base = {"title": "完全无关的标题", "content": "完全无关的正文", "category_l1": "其他"}
        national = dict(base, gov_level="国家级")
        county = dict(base, gov_level="县级")
        national_score, _ = score_link(national, TOPIC)
        county_score, _ = score_link(county, TOPIC)
        self.assertEqual(national_score - county_score, 5)

    def test_confidence_capped_at_100(self) -> None:
        record = {
            "title": "灵活就业 社保补贴 就业困难人员 灵活就业人员社保补贴实施细则",
            "content": "灵活就业 社保补贴 就业困难人员" * 20,
            "category_l1": "民政保障",
            "gov_level": "国家部委",
        }
        confidence, _ = score_link(record, TOPIC)
        self.assertLessEqual(confidence, 100)

    def test_link_status_thresholds(self) -> None:
        self.assertEqual(link_status(95), "auto")
        self.assertEqual(link_status(90), "auto")
        self.assertEqual(link_status(60), "pending")
        self.assertEqual(link_status(59), None)
        self.assertEqual(link_status(50, 2), "pending")
        self.assertEqual(link_status(50, 1), None)


class NormalizeTests(unittest.TestCase):
    def test_normalize_strips_whitespace_and_case(self) -> None:
        self.assertEqual(normalize(" Hello World "), "helloworld")

    def test_normalize_handles_none(self) -> None:
        self.assertEqual(normalize(None), "")


if __name__ == "__main__":
    unittest.main()
