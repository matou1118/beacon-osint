"""降频判据的回归测试。

排空模式最大的两个漏（scripts/audit_efficiency.py 实测）：
  - 26% 的请求是 SKIP：连败的源每轮都被打，每轮都失败
  - 少数源 818 秒/条，最好的源 3.1 秒/条，差 264 倍

最要紧的一条是**无历史时必须不过滤**。rounds.jsonl 里的 source_stats 是与这套
判据同一轮才加的，所以第一次跑的时候没有历史数据。如果那时候判据按"读不到就是
很慢"来处理，全部源都会被降频跳掉——比不优化糟糕得多。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import source_perf  # noqa: E402


class EfficiencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="beaconperf"))
        self._orig = source_perf.ROUNDS_LOG
        source_perf.ROUNDS_LOG = self.tmp / "rounds.jsonl"

    def tearDown(self) -> None:
        source_perf.ROUNDS_LOG = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, *objs) -> None:
        self.tmp.mkdir(parents=True, exist_ok=True)
        with (self.tmp / "rounds.jsonl").open("w", encoding="utf-8") as fh:
            for o in objs:
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")

    def test_missing_file_gives_no_data(self):
        """没有历史 -> 空 dict。调用方据此不过滤，而不是全跳。"""
        self.assertEqual(source_perf.load_efficiency(), {})

    def test_corrupt_lines_do_not_crash(self):
        self.tmp.mkdir(parents=True, exist_ok=True)
        (self.tmp / "rounds.jsonl").write_text(
            '{"round":1}\n坏 json\n\n{"round":2,"source_stats":[]}\n', encoding="utf-8")
        self.assertEqual(source_perf.load_efficiency(), {})

    def test_seconds_per_record(self):
        self._write({"round": 1, "source_stats": [
            {"id": "fast_gov", "records": 100, "seconds": 310.0},
            {"id": "slow_gov", "records": 3, "seconds": 2454.0},
        ]})
        perf = source_perf.load_efficiency()
        self.assertAlmostEqual(perf["fast_gov"].seconds_per_record, 3.1, places=2)
        self.assertAlmostEqual(perf["slow_gov"].seconds_per_record, 818.0, places=1)

    def test_zero_record_sources_are_excluded(self):
        """产出 0 的源算不出"秒/条"，必须排除而不是当成 0（那会让它显得最高效）。"""
        self._write({"round": 1, "source_stats": [
            {"id": "zero_gov", "records": 0, "seconds": 300.0},
        ]})
        self.assertNotIn("zero_gov", source_perf.load_efficiency())

    def test_accumulates_across_rounds(self):
        self._write(
            {"round": 1, "source_stats": [{"id": "a_gov", "records": 10, "seconds": 100.0}]},
            {"round": 2, "source_stats": [{"id": "a_gov", "records": 10, "seconds": 100.0}]},
        )
        perf = source_perf.load_efficiency()
        self.assertAlmostEqual(perf["a_gov"].seconds_per_record, 10.0, places=2)
        self.assertEqual(perf["a_gov"].records, 20)
        self.assertEqual(perf["a_gov"].rounds_seen, 2)

    def test_only_last_n_rounds_counted(self):
        rows = [{"round": i, "source_stats": [
            {"id": "a_gov", "records": 10, "seconds": 100.0}]} for i in range(10)]
        self._write(*rows)
        perf = source_perf.load_efficiency(max_rounds=3)
        self.assertEqual(perf["a_gov"].rounds_seen, 3)


class ThrottleSelectionTests(unittest.TestCase):
    """降频集合的选取：连败的 + 低效的，高产源不能被误伤。"""

    @staticmethod
    def _pick(perf, slow_threshold):
        return {p.sid for p in perf.values()
                if p.seconds_per_record > slow_threshold}

    def test_threshold_separates_fast_from_slow(self):
        perf = {
            "fast": source_perf.SourcePerf("fast", 3.1, 720, 5),
            "mid": source_perf.SourcePerf("mid", 22.0, 134, 5),
            "slow": source_perf.SourcePerf("slow", 818.0, 3, 5),
        }
        slow = self._pick(perf, 60.0)
        self.assertEqual(slow, {"slow"})
        self.assertNotIn("fast", slow)
        self.assertNotIn("mid", slow)

    def test_productive_source_never_lands_in_slow_set(self):
        """即使某个高产源偶尔抽风，也不能被判成低效——砍掉它损失更大。"""
        perf = {"big": source_perf.SourcePerf("big", 61.0, 900, 9)}
        self.assertEqual(self._pick(perf, 60.0), {"big"},
                         "阈值就是阈值，不按产出量开后门——否则行为不可预测")

    def test_failing_threshold_is_exclusive(self):
        rows = [("a", 4), ("b", 5), ("c", 21)]
        got = {str(s): int(n) for s, n in rows if int(n or 0) >= 5}
        self.assertNotIn("a", got)
        self.assertEqual(got["b"], 5)
        self.assertEqual(got["c"], 21)


if __name__ == "__main__":
    unittest.main()
