"""真实语料分类门禁。

为什么需要这个测试
------------------
仓库里已有 ``tests/test_synthetic_data.py``（L1 >=95% / L2 >=80%）。但它的
真���标签来自 ``scripts/generate_synthetic_data.py`` 的**随机拼词**：

    市北京市人民政府关于职业院校培训的公告
    关于锦麓区资质动态采购的公告
    关于岳麓区许可公示项目的通知   <- 标「商业机会」，标题里却写着「公示」

也就是说，它拿分类器去对**词组**打分，测的是「分类器能否还原生成器的规则」
（自洽性），不是「分类在真实标题上准不准」。2026-10-03 实测正是在这里
翻的车：为了保住合成基线的 95%，把明显更正确的改法拒之门外三轮
（准确率轨迹 76.2% / 41.2%）。

本测试用 ``tests/golden/category_golden.json``——从生产库里抽的**真实标题**，
且只收文种互斥、抽读验证过的类别（构建脚本见
``scripts/build_golden_set.py``，那里也写了为什么排除「名单」和「通知」）。

两个门禁并存，含义不同
----------------------
* 本测试 = **准确率**（真实标题 vs 人工确认的归属）
* 合成基线 = **自洽性**（规则改了之后生成器与分类器是否还对得上）

不要拿合成的准确率去否决真实数据上的改进，也不要拿本测试去给
「规则改动是否生效」下结论——两个问题问的不是一件事。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden" / "category_golden.json"

# 门槛来自真实基准的实测水平，留一点余量。
#
# 实测轨迹（都用同一份 240 条干净基准）：
#   起点                            L1 71.7%L2 67.5%   基准里混着栏目页，数字虚高
#   剔掉栏目页后                     L1 87.9%  L2 75.4%   ← 这是真���起点
#   强文种跨层查找（兼容扁平结构）      L1 87.9%  L2 75.4%   L1 已含在内
#   「公示」提到「名单」之前            L1 87.9%  L2 87.5%
#
# 注意起点那两行：剔脏样本之前测出的 94.2% 是**脏基准抬出来的**，
# 差点让我以为已经修好了（这已经是今天第四次靠脏基准得出错误结论）。
MIN_L1 = 0.85
MIN_L2 = 0.85


class GoldenCategoryTests(unittest.TestCase):
    """真实标题上的分类准确率。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not GOLDEN.exists():
            raise unittest.SkipTest(
                "缺少 %s，先跑 scripts/build_golden_set.py" % GOLDEN.name)
        cls.data = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.cases = cls.data["cases"]

    def test_golden_set_is_not_empty(self) -> None:
        self.assertGreaterEqual(len(self.cases), 100,
                                "基准太小，准确率没统计意义")
        self.assertEqual(len(self.data.get("threshold") or {}), 2,
                         "基准文件必须自带门禁值，避免测试里散落魔法数")

    def test_genres_are_mutually_exclusive(self) -> None:
        """基准里每个类必须只由文种定义，不含需要主题判断的类别。

        「通知」类标题的归属取决于主题（SPECIFIC_CATEGORIES 的加分机制），
        单看文种判断不了；「名单」和「公示」在真实标题里同时出现，
        「关于…名单的公示」归哪边是设计问题不是事实。这类标题不能进基准——
        它们会把「有歧义」伪装成「分类错误」。
        """
        pairs = {(c["category_l1"], c["category_l2"]) for c in self.cases}
        for l1, l2 in pairs:
            self.assertNotEqual(l2, "通知",
                                "「通知」需要主题信息，不该出现在高置信基准里")
            self.assertNotEqual(l2, "名单",
                                "「名单」与「公示」在真实标题里重叠，有歧义")

    def test_titles_look_like_real_announcements(self) -> None:
        """基准标题必须像真实公告，而不是随机拼词。

        合成数据的样本长这样：「市北京市人民政府关于职业院校培训的公告」
        ——「市」+「北京市」两个地域词撞在一起。基准里出现这种就说明构建脚本
        混进了合成数据，那样的标签毫无意义。
        """
        for case in self.cases:
            title = case["title"]
            self.assertGreaterEqual(len(title), 12,
                                    "标题过短，多半不是真实公告：%r" % title)
            for bogus in ("市北京市", "区资质动态", "许可公示项目", "审批公示项目"):
                self.assertNotIn(bogus, title,
                                 "基准里混进了合成数据的拼词标题：%r" % title)

    def test_l1_accuracy_on_real_titles(self) -> None:
        from extractors.category_rules import classify
        hits = sum(1 for case in self.cases
                   if classify({"title": case["title"], "content": ""})
                   .get("category_l1") == case["category_l1"])
        accuracy = hits / len(self.cases)
        self.assertGreaterEqual(
            accuracy, MIN_L1,
            "真实标题 L1 准确率 %.1f%%（门槛 %.0f%%）。基线是 %s。"
            % (accuracy * 100, MIN_L1 * 100, self.data.get("built_at")))

    def test_l2_accuracy_on_real_titles(self) -> None:
        from extractors.category_rules import classify
        hits = sum(1 for case in self.cases
                   if classify({"title": case["title"], "content": ""})
                   .get("category_l2") == case["category_l2"])
        accuracy = hits / len(self.cases)
        self.assertGreaterEqual(
            accuracy, MIN_L2,
            "真实标题 L2 准确率 %.1f%%（门槛 %.0f%%）。基线是 %s。"
            % (accuracy * 100, MIN_L2 * 100, self.data.get("built_at")))


if __name__ == "__main__":
    unittest.main()