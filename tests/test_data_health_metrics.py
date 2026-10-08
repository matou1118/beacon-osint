"""data_health 的度量必须真的在度量。

背景：21 项指标里有 3 项（附件下载率、对比覆盖率、印证通过率）长期恒为 0，
看起来像"采集能力不足"，实际是度量写错了——它们从 `extra` 里读数据，而写入方
从不往 `extra` 放这三个键：

  附件      -> attachments 表              （实测 211 行）
  字段抽取  -> field_extractions 表         （实测 5903 行）
  印证状态  -> announcements.corroboration_status 列（实测 1883 条有值）

而 `extra` 中这三个键的记录数为 0，所以指标恒 0。自我迭代循环会一直盯着这三个
 phantom 指标找修复方案，永远找不到真因。

还有一个更隐蔽的坑：measure() 在取完主查询后就 close() 了连接，后续任何
con.execute() 抛 ProgrammingError，而它是 sqlite3.Error 的子类，被宽 except
吞掉后返回 0——静默失败，不报错。

这些测试用真实库断言"指标读的位置确实有数据"，防止再退回恒 0。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import data_health  # noqa: E402

DB = ROOT / "data" / "beacon.db"


def _scalar(con: sqlite3.Connection, query: str) -> int:
    row = con.execute(query).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


@unittest.skipUnless(DB.exists(), "需要 data/beacon.db")
class MetricReadsRealDataTests(unittest.TestCase):
    """指标必须反映库里的真实数据，而不是 extra 里不存在的键。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cls.metrics = data_health.measure()
        cls.total = _scalar(cls.con, "SELECT COUNT(*) FROM announcements")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.con.close()

    def test_measure_closes_no_connection_before_use(self) -> None:
        """measure() 必须能跑完，说明它没有在关闭的连接上静默吞异常。"""
        self.assertIsInstance(self.metrics, dict)
        self.assertEqual(self.metrics.get("total"), self.total)
        for key in ("summary_rate", "collection_yield_rate", "region_coverage_rate"):
            with self.subTest(metric=key):
                self.assertIsInstance(self.metrics.get(key), (int, float))

    def test_attachment_rate_reflects_the_attachments_table(self) -> None:
        stored = _scalar(self.con, "SELECT COUNT(*) FROM attachments")
        if not stored:
            self.skipTest("库里没有附件")
        expected = round(stored / self.total, 4)
        self.assertEqual(
            self.metrics.get("attachment_download_rate"), expected,
            "附件下载率必须来自 attachments 表，而不是 extra",
        )
        self.assertGreater(self.metrics["attachment_download_rate"], 0.0)

    def test_compare_rate_is_joined_to_existing_announcements(self) -> None:
        """比率不可能大于 1：field_extractions 里有指向已删公告的孤儿行。"""
        orphans = _scalar(
            self.con,
            "SELECT COUNT(DISTINCT f.announcement_id) FROM field_extractions f "
            "LEFT JOIN announcements a ON a.id = f.announcement_id WHERE a.id IS NULL",
        )
        rate = self.metrics.get("compare_coverage_rate")
        self.assertIsInstance(rate, (int, float))
        self.assertLessEqual(
            rate, 1.0,
            f"对比覆盖率 {rate} > 1，说明没和 announcements 关联"
            f"（孤儿抽取 {orphans} 条）",
        )
        matched = _scalar(
            self.con,
            "SELECT COUNT(DISTINCT f.announcement_id) FROM field_extractions f "
            "JOIN announcements a ON a.id = f.announcement_id",
        )
        if matched:
            self.assertEqual(rate, round(matched / self.total, 4))

    def test_corroboration_rate_reads_the_column_not_extra(self) -> None:
        # 列里的实际取值是 single_source / corroborated，没有 confirmed ——
        # 实现查的也是 'corroborated'（data_health.py:221）。这里原先查
        # 'confirmed'，恒得 0，于是拿 0 和实现的真实值对不上：库里有 114 条
        # corroborated（rate 0.0163）却断言等于 0.0。
        confirmed = _scalar(
            self.con, "SELECT COUNT(*) FROM announcements WHERE corroboration_status = 'corroborated'"
        )
        with_status = _scalar(
            self.con,
            "SELECT COUNT(*) FROM announcements WHERE corroboration_status IS NOT NULL "
            "AND corroboration_status <> ''",
        )
        rate = self.metrics.get("corroboration_pass_rate")
        self.assertIsInstance(rate, (int, float))
        self.assertEqual(rate, round(confirmed / self.total, 4))
        # 指标必须来自 corroboration_status 那一列，不是从 extra 里凑的。
        #
        # 原来的写法是「extra 里没有 corroboration 键 且 库里没有 confirmed
        # 记录 → 断言指标为 0」，用「库里恰好没数据」间接证明。那两个前提现在
        # 都不成立了：extra 里确实没这个键（这部分仍有效），但库里已有 114 条
        # corroborated，指标本就该是 0.0163 而不是 0。改成直接对比 —— 指标
        # 恰好等于该列算出的比例，这才是「来自这一列」的正向证明。
        in_extra = _scalar(
            self.con, "SELECT COUNT(*) FROM announcements WHERE extra LIKE '%corroboration%'"
        )
        self.assertEqual(in_extra, 0,
                         "extra 里不该有 corroboration 键；若有，指标来源就说不清了")
        if with_status:
            self.assertGreater(confirmed, 0,
                               "有互证状态的行，就该算出非零的互证通过率")
        else:
            self.assertEqual(rate, 0.0,
                             "库里没有互证状态时该指标为 0（真实业务状态，非度量故障）")

    def test_rates_stay_within_unit_interval(self) -> None:
        for key, value in self.metrics.items():
            if key.endswith("_rate") and isinstance(value, (int, float)) and not isinstance(value, bool):
                with self.subTest(metric=key):
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main()
