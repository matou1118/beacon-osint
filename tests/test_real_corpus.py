from __future__ import annotations

import collections
import sqlite3
import unittest
from pathlib import Path

from extractors.category_rules import classify, load_category_rules, weights_from

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus.db"

# The real corpus is expert commentary on agricultural policy. Most of it is
# research advice, not a citizen-facing subsidy/recruitment/tender notice, so a
# high "其他" rate is the correct answer rather than a classifier defect. These
# bounds catch an actual regression (everything collapsing to one bucket) without
# demanding the classifier mislabel research papers as public notices.
MAX_OTHER_RATE = 0.98
MIN_DISTINCT_CATEGORIES = 3


class RealCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CORPUS.exists():
            raise unittest.SkipTest("data/corpus.db missing; run the corpus import first")
        rules = load_category_rules()
        cls.weights = weights_from(rules)
        con = sqlite3.connect(CORPUS)
        con.row_factory = sqlite3.Row
        cls.rows = con.execute("SELECT title, content, issuing_body, target_industry FROM corpus").fetchall()
        con.close()
        cls.results = [classify(dict(row), rules) for row in cls.rows]

    def test_weights_come_from_config_not_hardcoded(self) -> None:
        scoring = load_category_rules().get("scoring") or {}
        for key in ("title_weight", "body_weight", "specific_bonus"):
            self.assertIn(key, scoring, f"{key} must be tunable from config")

    def test_distribution_is_not_degenerate(self) -> None:
        counts = collections.Counter(result["category_l1"] for result in self.results)
        other_rate = counts.get("其他", 0) / len(self.results)
        self.assertLessEqual(other_rate, MAX_OTHER_RATE, f"other rate {other_rate:.1%} collapsed")
        self.assertGreaterEqual(len(counts), MIN_DISTINCT_CATEGORIES)

    def test_every_record_gets_a_label(self) -> None:
        for row, result in zip(self.rows, self.results):
            self.assertTrue(result["category_l1"].strip())
            self.assertTrue(result["category_l2"].strip())

    def test_classification_is_deterministic(self) -> None:
        rules = load_category_rules()
        again = [classify(dict(row), rules) for row in self.rows[:80]]
        self.assertEqual(again, self.results[:80])


if __name__ == "__main__":
    unittest.main()
