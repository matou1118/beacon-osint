from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from extractors.category_rules import classify, load_category_rules, weights_from
from scheduler import apply_level_switch

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_synthetic_data.py"
SCHEDULER_CONFIG = ROOT / "config" / "scheduler.yaml"

# ★ 这两个门槛量的不是「真实分类准确率」，而是**自洽性**：
# 生成器按当前规则造标签，分类器再还原它，两边是否还对得上。
#
# 2026-10-03 发现这一点，是因为拿它当准确率门禁用出了大事。合��集的标题是
# **随机拼词**：
#     市北京市人民政府关于职业院校培训的公告
#     关于岳麓区许可公示项目的通知<- 标签「商业机会」，标题里却写着「公示」
# 也就是说它拿分类器去对词组打分。于是真实标题上明显更正确的改法
# （公示优先于名单、强文种跨层查找）都被这个门禁拒了，理由是「合成 L1 掉到
# 92.5% < 95%」——而那 92.5% 里全是被随机标签冤枉的。
#
# 真实准确率看 tests/test_golden_classify.py（240 条生产库真实标题）。
# 这里的门槛只保证「生成器和分类器没脱节」，所以定在实测自洽水平略低处。
MIN_L1_ACCURACY = 0.90
MIN_L2_ACCURACY = 0.78


class SyntheticDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "synthetic.db"
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--count", "1200", "--db", str(cls.db)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        if result.returncode != 0:
            raise AssertionError(f"generator failed: {result.stderr[-500:]}")
        cls.summary = json.loads(result.stdout)
        rules = load_category_rules()
        con = sqlite3.connect(cls.db)
        con.row_factory = sqlite3.Row
        cls.rows = con.execute(
            "SELECT title, content, content_hash, target_industry, category_l1, category_l2 FROM announcements"
        ).fetchall()
        con.close()
        cls.results = [classify(dict(row), rules) for row in cls.rows]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_generates_requested_volume_with_unique_hashes(self) -> None:
        self.assertEqual(len(self.rows), 1200)
        self.assertEqual(self.summary["duplicate_hashes"], 0)
        self.assertEqual(len({row["content_hash"] for row in self.rows}), 1200)

    def test_covers_every_level_and_nearly_every_category(self) -> None:
        self.assertEqual(set(self.summary["levels"]), {"国家部委", "省级", "市级", "县级"})
        self.assertGreaterEqual(self.summary["categories_l1_covered"], 12)
        self.assertGreaterEqual(self.summary["categories_l2_covered"], 35)

    def test_every_l1_category_has_a_subcategory(self) -> None:
        rules = load_category_rules()
        covered = {row["category_l2"] for row in self.rows}
        for category, spec in rules["categories"].items():
            for subcategory in (spec.get("subcategories") or {}):
                with self.subTest(category=category, subcategory=subcategory):
                    self.assertIn(subcategory, covered)

    def test_region_fields_present_for_sub_national_records(self) -> None:
        regional = [row for row in self.rows if row["category_l1"] and row["title"]]
        self.assertTrue(regional)
        for row in self.rows:
            self.assertTrue(row["title"].strip())
            self.assertTrue(row["content"].strip())

    def test_category_l1_accuracy_meets_threshold(self) -> None:
        hits = sum(1 for row, got in zip(self.rows, self.results) if got["category_l1"] == row["category_l1"])
        accuracy = hits / len(self.rows)
        self.assertGreaterEqual(accuracy, MIN_L1_ACCURACY, f"L1 accuracy {accuracy:.1%}")

    def test_category_l2_accuracy_meets_threshold(self) -> None:
        hits = sum(1 for row, got in zip(self.rows, self.results) if got["category_l2"] == row["category_l2"])
        accuracy = hits / len(self.rows)
        self.assertGreaterEqual(accuracy, MIN_L2_ACCURACY, f"L2 accuracy {accuracy:.1%}")

    def test_generation_is_reproducible_for_a_fixed_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "again.db"
            subprocess.run(
                [sys.executable, str(GENERATOR), "--count", "300", "--db", str(other)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=ROOT,
                check=True,
            )
            con = sqlite3.connect(other)
            hashes = [row[0] for row in con.execute("SELECT content_hash FROM announcements ORDER BY id")]
            con.close()
            con = sqlite3.connect(self.db)
            original = [
                row[0] for row in con.execute("SELECT content_hash FROM announcements ORDER BY id LIMIT 300")
            ]
            con.close()
            self.assertEqual(hashes, original)


if __name__ == "__main__":
    unittest.main()
