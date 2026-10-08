"""分类枚举一致性：config/categories.yaml 必须覆盖分类器可能产出的全部一级分类。

为什么需要这个测试
  前端分类下拉（map_dashboard.py 的 _categories()）和 GET /api/categories 都
  只读 config/categories.yaml；订阅匹配（rules/engine.py 的
  `record.category_l1 not in user.categories`）也用它做硬过滤。但 classify()
  还会从 category_rules.yaml 的 fallback_categories（政务文种）和
  department_map.yaml（主管部门）产出别的词。两套词表一旦脱节，记录就会被归进
  一个「用户勾不到、也匹配不到」的一级分类。

  实测发生过：6391 条记录里 2411 条（37.7%）的 category_l1 不在 categories.yaml
  的 14 类枚举内（公示公告 637、政策发布 524、政策法规 491、商务贸易 161…），
  而当时所有单元测试都是绿的——因为没有任何测试比对这两套词表。

  这个测试就是那道缺失的比对：只要 classify() 的输出空间超出 categories.yaml
  的枚举，立刻失败。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TAXONOMY = ROOT / "config" / "categories.yaml"
CATEGORY_RULES = ROOT / "config" / "category_rules.yaml"
DEPARTMENT_MAP = ROOT / "config" / "department_map.yaml"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class CategoryTaxonomyTest(unittest.TestCase):
    """枚举必须覆盖分类器的输出空间，且部门只做行业领域。"""

    def setUp(self) -> None:
        self.taxonomy = _load(TAXONOMY)
        self.rules = _load(CATEGORY_RULES)
        self.departments = _load(DEPARTMENT_MAP)
        self.enum = set((self.taxonomy.get("l1") or {}).keys())
        self.department_names = set((self.departments.get("department_map") or {}).keys())

    def _classifier_l1_space(self) -> set[str]:
        """classify() 可能返回的一级分类全集（按修复后的实现口径）。"""
        space = set((self.rules.get("categories") or {}).keys())
        space |= set((self.rules.get("fallback_categories") or {}).keys())
        # 部门归类固定落「行业政策」，部门名只做 category_l2。
        space.add("行业政策")
        default = (self.rules.get("defaults") or {}).get("category_l1")
        if default:
            space.add(str(default))
        return space

    def test_enum_covers_classifier_output(self) -> None:
        missing = sorted(self._classifier_l1_space() - self.enum)
        self.assertEqual(
            [],
            missing,
            "classify() 能产出这些一级分类，但 config/categories.yaml 没有："
            f"{missing}。前端下拉和订阅匹配只认 categories.yaml，"
            "缺一个就有一批记录勾不到、也匹配不到。",
        )

    def test_enum_has_no_dead_classes(self) -> None:
        """反向检查：枚举里不应留有分类器永远产不出的死类。"""
        extra = sorted(self.enum - self._classifier_l1_space())
        self.assertEqual([], extra, f"categories.yaml 里有分类器产不出的一级分类：{extra}")

    def test_required_subcategories_present(self) -> None:
        """AGENTS §4.3 的条件必填子类必须存在且标记 required。"""
        l1 = self.taxonomy.get("l1") or {}
        for name, expected in (
            ("养老与社区", {"高龄津贴", "失能补贴", "老年助餐", "探访关爱",
                            "适老化改造", "社区活动", "养老机构", "长期护理保险"}),
            ("招生信息", {"高职单招", "公费定向培养", "行业院校招生",
                          "职业院校招生", "事业单位定向培养", "招生政策解读"}),
        ):
            spec = l1.get(name) or {}
            self.assertTrue(spec.get("required"), f"{name} 必须 required: true")
            self.assertTrue(
                expected.issubset(set(spec.get("subcategories") or [])),
                f"{name} 缺少必填子类：{sorted(expected - set(spec.get('subcategories') or []))}",
            )

    def test_department_classification_lands_in_industry_policy(self) -> None:
        """部门只决定行业领域：category_l1 必须是行业政策，部门名落到 category_l2。"""
        from extractors.category_rules import classify_by_department

        self.assertIn("行业政策", self.enum)
        checked = 0
        for department, markers in (self.departments.get("department_map") or {}).items():
            for marker in markers or []:
                # 用带前缀的正文，避免"部"这类单字前缀在别的部门标记上误命中。
                result = classify_by_department({"issuing_body": f"中华人民共和国{marker}"})
                if result is None:
                    continue
                self.assertEqual(
                    "行业政策",
                    result["category_l1"],
                    f"部门「{department}」的一级分类应为行业政策，实际 {result['category_l1']}",
                )
                self.assertIn(result["category_l2"], self.department_names)
                self.assertNotIn(result["category_l1"], self.department_names)
                checked += 1
        self.assertGreater(checked, 0, "department_map.yaml 里没有任何标记被命中")

    def test_industry_policy_subcategories_document_departments(self) -> None:
        """行业政策的二级词表应登记全部部门名，保证 category_l2 有归宿。"""
        subs = set(((self.taxonomy.get("l1") or {}).get("行业政策") or {}).get("subcategories") or [])
        missing = sorted(self.department_names - subs)
        self.assertEqual([], missing, f"行业政策 二级词表缺少部门名：{missing}")

    def test_enum_covers_classifier_subcategories(self) -> None:
        """**二级**分类也必须全覆盖（父类 + 子类同时比对）。

        2026-10-06 实测：只查一级时这套检查是绿的，而库里 989 条记录的
        category_l2 挂在分类表没有的子類下 —— 比选采购 338、课题承担 226、
        能源电力 125、交通物流 91…… 规则表（category_rules.yaml 的
        categories 与 fallback_categories）里这些子类一直都有，是
        categories.yaml 在一次「搬迁」时漏登记了它们。

        后果比一级更隐蔽：一级分类照常能勾、能订阅，只是订阅「能源电力」的
        用户一条都匹配不上——分类在库里看起来是齐的。
        """
        enum_subs = {
            name: set((spec.get("subcategories") or []))
            for name, spec in (self.taxonomy.get("l1") or {}).items()
        }
        missing = sorted(
            f"{l1}/{l2}"
            for section in ("categories", "fallback_categories")
            for l1, body in (self.rules.get(section) or {}).items()
            for l2 in (body.get("subcategories") or {})
            if l2 not in enum_subs.get(l1, set())
        )
        self.assertEqual(
            [],
            missing,
            "classify() 能产出这些二级分类，但 config/categories.yaml 没有："
            f"{missing}。前端下拉和订阅匹配只认 categories.yaml，缺一个订阅就匹配不上。",
        )


if __name__ == "__main__":
    unittest.main()
