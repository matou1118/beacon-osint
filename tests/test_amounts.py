"""金额抽取的回归测试。每条用例都对应一个实测踩过的坑，不是凑覆盖率。

最重要的一类坑是**静默抽错**：正则加了分组、换了单位、挪了切片位置，都不报错，
只是抽出来的东西变了。所以用例大多断言"不该出现的那个数确实没出现"。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.amounts import (  # noqa: E402
    MONEY_RE,
    AmountHit,
    _context,
    cross_verify,
    extract_amounts,
    parse_table_amounts,
)


class UnitNormalizationTests(unittest.TestCase):
    """单位归一：时间轴对比的前提。1.92亿元 与 19200万元 必须是同一个点。"""

    def test_yi_yuan_equals_wan_yuan(self):
        hits, _ = extract_amounts("安排资金1.92亿元支持建设", "")
        self.assertEqual(hits[0].value_yuan, 192_000_000.0)
        hits, _ = extract_amounts("安排资金19200万元支持建设", "")
        self.assertEqual(hits[0].value_yuan, 192_000_000.0)

    def test_thousands_separator_and_decimal(self):
        hits, _ = extract_amounts("项目总投资108,795.31万元", "")
        self.assertEqual(hits[0].value_yuan, 1_087_953_100.0)

    def test_yuan_prefix_symbol(self):
        # 实测 id=2303：「预算金额：¥98,850.00元（大写：…）」
        hits, _ = extract_amounts("预算金额：¥98,850.00元", "")
        self.assertEqual(hits[0].value_yuan, 98_850.0)
        self.assertEqual(hits[0].label, "预算金额")


class NonAmountTests(unittest.TestCase):
    """非金额必须挡住。错误金额会污染时间轴，比没有金额更糟。"""

    def test_wan_renci_is_not_money(self):
        # 实测 id=1603：「筛查患者及高危人群管理不少于25万人次」被记成 250,000 元
        self.assertIsNone(MONEY_RE.search("不少于25万人次"))
        hits, _ = extract_amounts("筛查患者及高危人群管理不少于25万人次。", "")
        self.assertEqual([h for h in hits if h.value_yuan == 250_000], [])

    def test_wan_kilowatt_is_not_money(self):
        # 实测 id=2296：「30万千瓦及以上热电联产机组」被记成 30万元
        self.assertIsNone(MONEY_RE.search("30万千瓦及以上"))

    def test_wan_ton_is_not_money(self):
        # 实测 id=735：「退出6000万吨/年以下生产线」被记成 6000万元
        self.assertIsNone(MONEY_RE.search("6000万吨/年以下"))

    def test_kilowatt_hour_with_space_still_rejected(self):
        # 实测 id=183：「用电量172亿千瓦时」被记成 172亿元。
        # 单位与量词之间有空格时也必须挡住，所以 lookahead 用了 \s*
        self.assertIsNone(MONEY_RE.search("172亿 千瓦时"))

    def test_rate_units_are_kept(self):
        """单价/费率是真金额，不能误杀。"""
        self.assertIsNotNone(MONEY_RE.search("不低于5.67万元/亩"))
        self.assertIsNotNone(MONEY_RE.search("评审收费标准为300元/人"))


class LabelPairingTests(unittest.TestCase):
    """标签决定这个数是什么。要回答"环保投资多少"，只知道有个金额不够。"""

    def test_one_document_multiple_amounts_keep_own_labels(self):
        # 实测 id=86 环评公示：三个项目的投资 + 各自的环保投资
        body = ("本项目动态投资总计1923610万元，其中环保投资约18252.5万元。"
                "本工程总投资390622万元，其中环保设施及措施投资24196.75万元。")
        hits, _ = extract_amounts(body, "")
        by_label = {h.label: h.value_yuan for h in hits}
        self.assertEqual(by_label["投资总额"], 390_622_0000.0)
        self.assertEqual(by_label["环保投资"], 241_967_500.0)
        self.assertEqual(len(hits), 4, "四个金额都要留下，不能只留一个")

    def test_table_pairs_labels_by_column_position(self):
        # 实测 id=137：表头和数据挤在同一行
        # |分包号|分包名称|采购预算|最高限价|项目属性|备注||第一包|舆情监测服务|495000元|495000元|服务|
        line = ("|分包号|分包名称|采购预算|最高限价|项目属性|备注|"
                "|第一包|舆情监测服务|495000元|495000元|服务|")
        hits = parse_table_amounts(line)
        self.assertEqual({h.label for h in hits}, {"采购预算", "最高限价"})
        self.assertEqual({h.value_yuan for h in hits}, {495_000.0})

    def test_multiple_values_same_label_are_not_treated_as_contradiction(self):
        """同标签多个不同值 = 多个项目，不是自相矛盾。

        原来的实现一律降 20 分，结果 54 分掉到 60 门槛以下，
        那份环评公示里三个项目的投资被全丢掉——丢的正是最该留的数据。
        """
        hits = [
            AmountHit("投资总额", "1923610万元", 19_236_100_000.0, "万元", 74, "prose"),
            AmountHit("投资总额", "390622万元", 3_906_220_000.0, "万元", 74, "prose"),
            AmountHit("投资总额", "441128万元", 4_411_280_000.0, "万元", 74, "prose"),
        ]
        out = cross_verify(hits)
        self.assertTrue(all(h.confidence >= 60 for h in out),
                        "降分可以，但不能降到采信门槛以下")
        self.assertEqual(len({h.value_yuan for h in out}), 3, "三个值都要留")

    def test_cross_source_agreement_raises_confidence(self):
        """表格与散文都报同一金额 = 两条独立路径互证，提分。"""
        hits = [
            AmountHit("投资总额", "2.55亿元", 255_000_000.0, "亿元", 74, "prose"),
            AmountHit("投资总额", "255000000元", 255_000_000.0, "元", 88, "table"),
        ]
        out = cross_verify(hits)
        self.assertTrue(all(h.confidence >= 84 for h in out))


class SoleAmountTests(unittest.TestCase):
    """全文唯一金额 → 采信。这是"没表格时交叉印证"的落地形态。"""

    def test_single_unlabelled_money_is_taken(self):
        hits, _ = extract_amounts("全省服务业增加值力争突破11万亿元。", "")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].label, "主要金额")
        self.assertEqual(hits[0].value_yuan, 11_000_000_000_000.0)

    def test_several_unlabelled_money_are_not_taken(self):
        """多个无标签金额歧义太大，不采信，列进 candidates 供人工看。"""
        hits, cands = extract_amounts("安排1.2亿元用于建设，另配套5000万元。", "")
        self.assertEqual([h for h in hits if h.label == "主要金额"], [])
        self.assertTrue(cands, "歧义项应作为待确认候选返回")


class EvidenceTests(unittest.TestCase):
    """evidence 必须含命中处，否则人工没法复核——这是可审计性，不是装饰。"""

    def test_context_always_contains_the_match(self):
        """早先写 text[start-60:end+24][:90]，总长超 90 时 [:90] 把命中处切掉了。

        实测 373 处里有 108 处 evidence 里找不到那个金额。用 raw in evidence
        就能发现，肉眼看不出来。
        """
        text = "前置说明。" * 40 + "项目总投资108795.31万元" + "后续说明。" * 40
        hits, _ = extract_amounts(text, "")
        self.assertTrue(hits)
        for hit in hits:
            self.assertIn(hit.raw.replace(" ", ""),
                          (hit.evidence or "").replace(" ", ""))

    def test_table_evidence_uses_the_right_cell(self):
        """循环变量泄漏：只存了 (idx, m)，引用的 cell 已是本行最后一个格。

        行末常常是空单元格，于是 evidence 变成空壳。
        """
        line = "|序号|项目名称|环评机构|受理日期||1|某项目|某机构|2026-09-07|3.2万元|"
        hits = parse_table_amounts(line)
        self.assertTrue(hits)
        for hit in hits:
            self.assertIn(hit.raw.replace(" ", ""),
                          (hit.evidence or "").replace(" ", ""))

    def test_context_marks_the_match(self):
        # 「总投资2.55亿元」：总0 投1 资2 2:3 .:4 5:5 5:6 亿7 元8
        got = _context("总投资2.55亿元，其中环保投资0.23亿元", 3, 9)
        self.assertIn("2.55亿元", got)


class SoleDocumentGuardTests(unittest.TestCase):
    def test_phone_and_postcode_never_count(self):
        body = "联系电话：028-86702309，邮编：610000，面积1200平方米。"
        hits, _ = extract_amounts(body, "")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
