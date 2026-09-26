from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from rules.corroboration import corroborate_records, corroboration_summary
from rules.engine import evaluate_rules, hard_filter_passes
from rules.tracker import apply_focus_tracking, find_focus_tags

CORROBORATION_CONFIG = {
    "corroboration": {"enabled": True, "min_distinct_sources": 2, "topic_terms": ["养老", "电力", "专项债"]}
}
GOV_RECORD = {
    "title": "关于开展2026年养老服务设施建设专项的通知",
    "source_id": "mca_gov",
    "source_type": "gov",
    "source_url": "https://www.mca.gov.cn/n1/2026/1.html",
    "region_province": "",
    "category_l1": "民生保障",
    "publish_date": "2026-01-15",
}
LIVELIHOOD_MEDIA = {
    "title": "养老服务设施建设专项落地：中央资金2.4亿元支持地方改造",
    "source_id": "yicai",
    "source_type": "media",
    "source_url": "https://www.yicai.com/news/1001.html",
    "region_province": "",
    "category_l1": "民生保障",
    "publish_date": "2026-01-20",
}
POLITICAL_MEDIA = {
    "title": "习近平会见外国领导人，就养老服务财政资金安排交换意见",
    "source_id": "xinhua_policy",
    "source_type": "media",
    "source_url": "http://www.news.cn/politics/2026-01/20/c_1.html",
    "region_province": "",
    "category_l1": "民生保障",
    "publish_date": "2026-01-20",
}


class CorroborationGateTests(unittest.TestCase):
    """AGENTS.md 1.6: media corroboration is limited to industry, livelihood and
    economic reporting, so a 时政 wire story must not raise the source count."""

    def _status(self, *extra: dict) -> dict:
        return corroborate_records([GOV_RECORD, *extra], CORROBORATION_CONFIG)[0]

    def test_single_government_source(self) -> None:
        result = self._status()
        self.assertEqual(result["corroboration_status"], "single_source")
        self.assertEqual(result["corroboration_count"], 1)

    def test_livelihood_media_corroborates(self) -> None:
        result = self._status(LIVELIHOOD_MEDIA)
        self.assertEqual(result["corroboration_status"], "corroborated")
        self.assertEqual(result["corroboration_count"], 2)
        self.assertEqual(result["corroboration_excluded_media"], 0)

    def test_political_media_is_excluded(self) -> None:
        result = self._status(POLITICAL_MEDIA)
        self.assertEqual(result["corroboration_status"], "single_source")
        self.assertEqual(result["corroboration_count"], 1)
        self.assertEqual(result["corroboration_excluded_media"], 1)
        self.assertIn("§1.6", result["corroboration_reason"])

    def test_political_media_does_not_inflate_a_valid_pair(self) -> None:
        result = self._status(LIVELIHOOD_MEDIA, POLITICAL_MEDIA)
        self.assertEqual(result["corroboration_status"], "corroborated")
        self.assertEqual(result["corroboration_count"], 2)
        self.assertEqual(result["corroboration_excluded_media"], 1)

    def test_disabled_config_is_reported(self) -> None:
        out = corroborate_records([GOV_RECORD], {"corroboration": {"enabled": False}})
        self.assertEqual(out[0]["corroboration_status"], "disabled")
        self.assertEqual(corroboration_summary(out)["disabled"], 1)


class FocusTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "focus_tracking": {
                "enabled": True,
                "source_focus_levels": ["critical", "high", "standard"],
                "tags": ["重大项目", "项目申报", "资金补贴", "民生服务"],
            },
            "project_tracking": {
                "enabled": True,
                "source_role": "development_reform_commission",
                "tags": ["重大项目", "项目申报", "项目核准", "开工"],
            },
        }

    def test_tracks_controlled_tags_from_source_text(self) -> None:
        record = {
            "focus_level": "critical",
            "title": "某省重大项目申报通知",
            "content": "符合条件的企业可以提交项目申报材料。",
            "category_l1": "商业机会",
        }
        self.assertEqual(find_focus_tags(record, self.config), ["重大项目", "项目申报"])
        tracked = apply_focus_tracking(record, self.config)
        self.assertTrue(tracked["focus_tracked"])
        self.assertEqual(tracked["focus_tags"], ["重大项目", "项目申报"])
        self.assertEqual(tracked["focus_reasons"], ["重点标签命中：重大项目", "重点标签命中：项目申报"])

    def test_does_not_track_unmatched_or_disabled_sources(self) -> None:
        ordinary = {"focus_level": "high", "title": "日常办事提醒", "content": "请查看办理指南。"}
        self.assertFalse(apply_focus_tracking(ordinary, self.config)["focus_tracked"])
        disabled = {**self.config, "focus_tracking": {**self.config["focus_tracking"], "enabled": False}}
        self.assertFalse(apply_focus_tracking(ordinary, disabled)["focus_tracked"])
    def test_tracks_development_reform_project_source(self) -> None:
        record = {
            "project_role": "development_reform_commission",
            "title": "省发展改革委关于重大项目开工的公告",
            "content": "项目已完成核准并进入建设阶段。",
            "focus_level": "critical",
        }
        tracked = apply_focus_tracking(record, self.config)
        self.assertTrue(tracked["project_source"])
        self.assertTrue(tracked["project_tracked"])
        self.assertIn("重大项目", tracked["project_tags"])

    def test_scores_rules_and_renders_reasons(self) -> None:
        config = yaml.safe_load(Path("config/rules.yaml").read_text(encoding="utf-8"))
        announcement = {
            "gov_level": "省级",
            "region_province": "四川省",
            "category_l1": "行业补贴",
            "target_group": "企业",
            "target_industry": "房地产",
            "content": "租房和补贴项目申报通知",
            "institution_type": None,
            "employment_guarantee": None,
            "tuition_info": None,
        }
        user = {
            "region": "四川省",
            "categories": ["行业补贴"],
            "target_group": "企业",
            "target_industry": "房地产",
            "keywords": ["租房", "补贴"],
            "institution_preference": None,
        }
        result = evaluate_rules(announcement, user, config)
        self.assertEqual(result["score"], 105)
        self.assertEqual(
            result["reasons"],
            [
                "层级匹配：省级",
                "地域匹配：四川省",
                "分类匹配：行业补贴",
                "人群匹配：企业",
                "行业匹配：房地产",
                "关键词匹配：租房、补贴",
            ],
        )

    def test_region_filter_does_not_admit_every_national_announcement(self) -> None:
        config = yaml.safe_load(Path("config/rules.yaml").read_text(encoding="utf-8"))
        user = {"region": "四川省", "categories": ["人才政策"], "levels": ["国家部委"]}
        unrelated = {
            "gov_level": "国家部委",
            "category_l1": "人才政策",
            "title": "关于开展全国职业培训示范评选的通知",
            "content": "各地人力资源社会保障部门按要求报送候选单位。",
            "region_province": None,
            "region_city": None,
            "region_county": None,
        }
        self.assertFalse(hard_filter_passes(unrelated, user, config))

    def test_region_filter_admits_national_announcement_naming_the_region(self) -> None:
        config = yaml.safe_load(Path("config/rules.yaml").read_text(encoding="utf-8"))
        user = {"region": "四川省", "categories": ["人才政策"], "levels": ["国家部委"]}
        related = {
            "gov_level": "国家部委",
            "category_l1": "人才政策",
            "title": "关于支持四川省人才振兴工作的实施意见",
            "content": "四川省人力资源和社会保障厅负责组织实施。",
            "region_province": None,
            "region_city": None,
            "region_county": None,
        }
        self.assertTrue(hard_filter_passes(related, user, config))

    def test_no_region_filter_admits_everything(self) -> None:
        config = yaml.safe_load(Path("config/rules.yaml").read_text(encoding="utf-8"))
        record = {
            "gov_level": "国家部委",
            "category_l1": "人才政策",
            "title": "全国通知",
            "content": "",
            "region_province": None,
        }
        self.assertTrue(hard_filter_passes(record, {"region": None, "categories": ["人才政策"]}, config))


if __name__ == "__main__":
    unittest.main()
