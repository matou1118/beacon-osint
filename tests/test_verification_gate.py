from __future__ import annotations

import unittest

from extractors.verification_gate import (
    PENDING_MARK,
    VERIFY_MARK,
    classify_for_verification,
    partition_for_verification,
)


class GateTests(unittest.TestCase):
    def test_livelihood_industry_with_facts_is_accepted(self) -> None:
        verdict = classify_for_verification({
            "title": "民政部：加强普惠性基础性兜底性养老服务供给",
            "summary": "中央财政安排资金2.4亿元支持养老服务设施建设，申报主体为地方民政部门，覆盖试点地区。",
        })
        self.assertTrue(verdict["usable"])
        self.assertEqual(verdict["mark"], VERIFY_MARK)
        self.assertIn("amount", verdict["facts"])

    def test_politics_is_rejected(self) -> None:
        # AGENTS.md 1.6: media corroboration covers industry, livelihood and
        # economic reporting only.
        for title in (
            "习近平抵达华盛顿对美国进行国事访问",
            "工业和信息化部召开党组会议和干部大会",
            "国务院召开常务会议",
        ):
            with self.subTest(title=title):
                verdict = classify_for_verification({"title": title, "summary": "会议在京举行。"})
                self.assertFalse(verdict["usable"])

    def test_named_holder_reporting_is_flagged_political(self) -> None:
        # A political item that also mentions money and a sector must not slip
        # through on the sector/fact check alone.
        verdict = classify_for_verification({
            "title": "习近平会见外宾",
            "summary": "会见期间提到财政资金安排5000万元支持能源项目。",
        })
        self.assertFalse(verdict["usable"])
        self.assertIn("政治类", verdict["reasons"][0])

    def test_background_reporting_without_facts_is_rejected(self) -> None:
        verdict = classify_for_verification({
            "title": "养老服务产业发展观察",
            "summary": "养老产业近年来发展迅速，各方面都在积极探索。",
        })
        self.assertFalse(verdict["usable"])
        self.assertIn("无可比对事实", verdict["reasons"][0])

    def test_unrelated_topic_is_rejected(self) -> None:
        verdict = classify_for_verification({"title": "某地举办园艺展", "summary": "现场花卉展示。"})
        self.assertFalse(verdict["usable"])
        self.assertIn("未命中", verdict["reasons"][0])

    def test_single_fact_is_pending_not_verified(self) -> None:
        verdict = classify_for_verification({
            "title": "能源领域动态：电力保供安排",
            "summary": "安排资金5000万元用于电力保供。",
        })
        self.assertTrue(verdict["usable"])
        self.assertEqual(verdict["mark"], PENDING_MARK)

    def test_require_fact_can_be_disabled(self) -> None:
        verdict = classify_for_verification(
            {"title": "养老服务观察", "summary": "发展迅速。"}, require_fact=False
        )
        self.assertTrue(verdict["usable"])


class PartitionTests(unittest.TestCase):
    def test_partition_counts_and_reasons(self) -> None:
        rows = [
            {"title": "能源领域电力保供安排", "summary": "安排资金5000万元，申报主体为能源局。"},
            {"title": "习近平会见外宾", "summary": "会谈在北京举行。"},
            {"title": "养老服务产业观察", "summary": "发展迅速。"},
        ]
        out = partition_for_verification(rows)
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["accepted_count"], 1)
        self.assertEqual(out["rejected_count"], 2)
        self.assertEqual(out["verified_count"], 1)
        self.assertTrue(any("政治类" in key for key in out["rejection_reasons"]))


if __name__ == "__main__":
    unittest.main()
