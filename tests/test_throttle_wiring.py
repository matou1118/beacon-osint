"""降频在 run_round 里到底接没接上——直接跑真实代码，mock 掉数据库和网络。

前面 test_source_throttle.py 验的是判据本身（算得对不对）。这里验的是**接线**：
run_round 的 todo 构建是不是真的把连败源和低效源踢出了本轮。
接线错了判据再准也没用，而这种错不会报错，只会让排空模式照旧白跑 40 轮。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import batch_collect  # noqa: E402
import source_perf  # noqa: E402


def _sources():
    return [
        {"id": "good_gov", "name": "高产源", "status": "enabled", "enabled": True},
        {"id": "failing_gov", "name": "连败源", "status": "enabled", "enabled": True},
        {"id": "slow_gov", "name": "低效源", "status": "enabled", "enabled": True},
    ]


def _args(**over):
    base = dict(limit=5, workers=2, timeout=1, force=True, all_statuses=False,
                rounds=1, pause=0, stop_when_dry=0,
                fail_backoff=5, backoff_every=5,
                slow_threshold=60.0, slow_every=4, sources="config/sources.yaml")
    base.update(over)
    return argparse.Namespace(**base)


class FakeDatabase:
    """够 run_round 用：不真的开库，source_due 一律 True。"""

    def __init__(self, *a, **k):
        self.closed = False

    def source_due(self, sid, now=None):
        return True

    def upsert_announcements(self, records):
        return {"inserted": 0, "updated": 0, "unchanged": 0}

    def save_field_extractions(self, records):
        return {}

    def close(self):
        self.closed = True

    def session(self):
        raise AssertionError("测试里不该真的开 session")


PERF = {
    "good_gov": source_perf.SourcePerf("good_gov", 3.1, 720, 5),
    "failing_gov": source_perf.SourcePerf("failing_gov", 5.0, 10, 5),
    "slow_gov": source_perf.SourcePerf("slow_gov", 818.0, 3, 5),
}


def _run(round_no, failing, perf, args):
    """跑一轮 run_round，返回它记录的 todo 数与实际被采集的源 id。"""
    seen: list[str] = []

    def fake_collect_one(source, limit, timeout_sec, now, database):
        seen.append(source["id"])
        return source["id"], [], None, 0.1

    with mock.patch.object(batch_collect, "Database", FakeDatabase), \
         mock.patch.object(batch_collect, "collect_one", fake_collect_one), \
         mock.patch.object(batch_collect, "record_round", lambda s: None), \
         mock.patch.object(batch_collect, "load_rules", lambda p: {}), \
         mock.patch.object(batch_collect, "failing_sources",
                           lambda db, thr: failing), \
         mock.patch.object(batch_collect, "load_efficiency", lambda: perf):
        summary = batch_collect.run_round(args, _sources(), round_no, 40)
    return summary, seen


class WiringTests(unittest.TestCase):
    def test_first_round_collects_everything(self):
        """第 1 轮必须全打——否则刚恢复的源永远没有机会回来。"""
        summary, seen = _run(1, {"failing_gov": 21}, PERF, _args())
        self.assertEqual(sorted(seen), ["failing_gov", "good_gov", "slow_gov"])

    def test_second_round_drops_failing_and_slow(self):
        summary, seen = _run(2, {"failing_gov": 21}, PERF, _args())
        self.assertIn("good_gov", seen)
        self.assertNotIn("failing_gov", seen, "连败源第 2 轮起应被降频")
        self.assertNotIn("slow_gov", seen, "低效源第 2 轮起应被降频")

    def test_throttled_source_returns_on_its_turn(self):
        """降频不是永久拉黑：每 N 轮仍要给一次机会。

        注意轮次取模：backoff_every=5 -> 第 5/10/15 轮；
        slow_every=4 -> 第 4/8/12 轮。所以第 5 轮 failing 回来了、slow 还没轮到，
        第 8 轮两个都回来了。两条判据的周期不同，别混。
        """
        args = _args(backoff_every=5, slow_every=4)
        _, seen5 = _run(5, {"failing_gov": 21}, PERF, args)
        self.assertIn("failing_gov", seen5, "backoff_every=5 -> 第 5 轮该回来")
        self.assertNotIn("slow_gov", seen5, "slow_every=4 -> 第 5 轮还没轮到")

        _, seen8 = _run(8, {"failing_gov": 21}, PERF, args)
        self.assertIn("slow_gov", seen8, "slow_every=4 -> 第 8 轮该回来")
        self.assertNotIn("failing_gov", seen8, "backoff_every=5 -> 第 8 轮该继续跳")

    def test_threshold_is_inclusive_at_boundary(self):
        """60.0 秒/条 正好等于阈值时不算低效（判据是 > 不是 >=）。"""
        perf = dict(PERF, slow_gov=source_perf.SourcePerf("slow_gov", 60.0, 100, 5))
        _, seen = _run(2, {}, perf, _args(slow_threshold=60.0))
        self.assertIn("slow_gov", seen)

    def test_disabling_switches_restores_full_pass(self):
        """--fail-backoff 0 / --slow-threshold 0 必须关掉降频，否则没法排查。"""
        args = _args(fail_backoff=0, slow_threshold=0.0)
        _, seen = _run(9, {"failing_gov": 21}, PERF, args)
        self.assertEqual(sorted(seen), ["failing_gov", "good_gov", "slow_gov"])

    def test_no_perf_history_means_no_throttling(self):
        """没有历史时不能降频——第一轮跑完才有 source_stats。"""
        _, seen = _run(2, {}, {}, _args())
        self.assertEqual(sorted(seen), ["failing_gov", "good_gov", "slow_gov"])


if __name__ == "__main__":
    unittest.main()
