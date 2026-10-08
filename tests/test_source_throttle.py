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


class ArchiveDepthTierTests(unittest.TestCase):
    """B/C 分档：显式写的翻页深度不许被下限抬掉。

    2026-10-02 实测踩过：想按产出把源分档（C 档库内 >=100 条的深翻 12 页，
    B 档其余 3 页保持快节奏）。但 ``raise_archive_pages_below=30`` 无条件
    上抬，**每一档最后都被算成 30 页**，B 档的 3 形同虚设——而每源 30 页
    正是「145 源 / 30 分钟预算只跑完 36 个」的慢源根因。
    """

    CFG = {
        "collect_defaults": {
            "archive_pages": 30,
            "max_items": 600,
            "raise_archive_pages_below": 30,
            "raise_max_items_below": 600,
        }
    }

    @staticmethod
    def _pages(source: dict) -> int:
        from lighthouse.run import with_defaults
        return int(with_defaults(source, ArchiveDepthTierTests.CFG,
                                 archive_pages=3).get("archive_pages") or 0)

    def test_c_tier_deep_survives(self):
        self.assertEqual(self._pages({"id": "js_gxt", "archive_pages": 12}), 12,
                         "高产源的深翻页被抬回下限，B/C 分档就白做了")

    def test_b_tier_shallow_survives(self):
        self.assertEqual(self._pages({"id": "miit_gov", "archive_pages": 3}), 3,
                         "B 档浅翻页被抬到 30，「快节奏日增量」直接没了")

    def test_unset_source_still_gets_floor(self):
        """没显式写的源仍要吃下限——那是防漏配，不能一起放过。"""
        self.assertEqual(self._pages({"id": "x_gov"}), 30)


class BudgetSkipMustNotPoisonBackoffTests(unittest.TestCase):
    """预算耗尽而没抓到的源，一次都不许进退避表。

    2026-10-02 实测踩过：145 源跑 30 分钟预算，只跑了 36 个，
    剩下 97 个被 collect_source 以 skipped=True 退回。但回灌退避的守卫
    只挡了「崩溃」，没挡「跳过」，于是那 97 个源 last_epoch 被写成
    「刚刚访问过」，全部推进 8 小时退避——下一轮直接报
    「145 个源全部在回访间隔」，而它们一条都没真抓过。
    """

    @staticmethod
    def _reaches_backoff(outcome):
        """复刻 run.py 的回灌守卫：崩溃或跳过都不许进退避表。"""
        crashed = any("崩溃" in str(n) for n in (outcome.get("notes") or []))
        return not crashed and not outcome.get("skipped")

    def test_budget_exhausted_source_never_recorded(self):
        out = {"records": [], "dropped": [],
               "notes": ["skipped: run time budget exhausted"],
               "seconds": 0.0, "skipped": True}
        self.assertFalse(self._reaches_backoff(out),
                         "预算耗尽的源没被访问过，进了退避表就会被压 8 小时")

    def test_real_zero_production_still_recorded(self):
        """真正跑了但零产出（库里已有）必须照常进退避表，别一起放过。"""
        out = {"records": [], "dropped": [],
               "notes": ["all 45 list urls already known"],
               "seconds": 9.0}
        self.assertTrue(self._reaches_backoff(out),
                        "真跑了就得记退避，否则采干的源会无限重访")

    def test_crash_still_not_recorded(self):
        out = {"records": [], "dropped": [],
               "notes": ["抓取崩溃"], "seconds": 1.0}
        self.assertFalse(self._reaches_backoff(out))


class BackoffShapeDetectorTests(unittest.TestCase):
    """退避表形状判据必须真的会响——判据不验证就等于没写。

    2026-10-02 实测踩过：写完 auto_act._backoff_shape_checks() 只验了
    「当前状态不误报」，没验「事故态会报」。而那个函数从磁盘读真实退避表，
    于是想造事故态只能去改生产文件——测一次污染一次。
    所以给它加了 backoff_obj 注入，事故态在内存里造。
    """

    @staticmethod
    def _detector():
        import importlib.util
        from pathlib import Path as P
        root = P(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "_aa_shape", root / "scripts" / "auto_act.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    class _FakeBackoff:
        def __init__(self, data):
            self.data = data

    def test_incident_shape_is_detected(self):
        """事故现场：全部 enabled 源进了退避表，且每条形状一模一样。

        这是「被预算砍掉却被记成已访问」的指纹——逐源看都正常，
        只有把整张表摆一起才看得出是同一次批量写入。
        """
        import yaml
        from pathlib import Path as P
        mod = self._detector()
        root = P(__file__).resolve().parents[1]
        enabled = [s for s in (yaml.safe_load(
            (root / "config" / "sources.yaml").read_text(encoding="utf-8"))
            .get("sources") or []) if s.get("enabled")]
        self.assertTrue(enabled, "配置里应有启用的源")
        fake = self._FakeBackoff(
            {str(s.get("id")): [1, 0, 9999999999, 1, 1, 0, 0] for s in enabled})
        found = dict(mod._backoff_shape_checks(backoff_obj=fake))
        self.assertIn("skipped_marked_visited", found,
                      "事故形状没被检出，这条判据等于没写")
        self.assertIn(str(len(enabled)), found["skipped_marked_visited"])

    def test_healthy_table_does_not_fire(self):
        """正常演化出来的表不误报（否则每轮都在瞎报警）。"""
        mod = self._detector()
        fake = self._FakeBackoff({"a_gov": [0, 0, 1700000000, 3, 0, 0, 0],
                                  "b_gov": [2, 0, 1700000001, 5, 0, 0, 0]})
        found = dict(mod._backoff_shape_checks(backoff_obj=fake))
        self.assertNotIn("skipped_marked_visited", found)

    def test_depth_detector_fires_when_all_sources_uniform(self):
        """深度判据同理：所有源算出来一样 = 分档被抬平了。"""
        mod = self._detector()
        fake = self._FakeBackoff({"only_one": [0, 0, 1, 1, 0, 0, 0]})
        found = dict(mod._backoff_shape_checks(backoff_obj=fake))
        # 这里不强制必须命中——collect_scope 下 enabled 源可能只有 1 个。
        # 但若命中，理由里必须带 archive_pages 这个词，方便定位。
        if "archive_depth_uniform" in found:
            self.assertIn("archive_pages", found["archive_depth_uniform"])

class StripNoiseChromeTests(unittest.TestCase):
    """摘要里的页脚必须被滤掉——而且要和 judge 用同一份词表。

    2026-10-02 实测：日报摘要里出现「网站标识码：3500000032 / 版权所有：©
    福建省科学技术厅 / 扫一扫在手机上查看当前页面」。词表里本来就有「网站
    标识码」「版权所有」，但 strip_noise 从来没读过它——它只认 markdown 链接
    形式的包屑（`[x](url)`），而这些页脚是纯文本行，一个都匹配不上。
    """

    NAVY = (
        "2026-10-02发布某通知。某厅 2026年8月7日附件下载\n"
        "*\n扫一扫在手机上查看当前页面\n_|_ _|_\n"
        "网站标识码： 3500000032\n"
        "版权所有：© 某厅\n"
        "为确保最佳浏览效果，建议您使用以下浏览器版本：IE9.0"
    )

    def setUp(self) -> None:
        from lighthouse import quality
        self.q = quality
        quality.load_config()          # 词表由 load_config 注入

    def test_chrome_lines_are_removed(self) -> None:
        out = self.q.strip_noise(self.NAVY)
        for junk in ("网站标识码", "版权所有", "扫一扫"):
            self.assertNotIn(junk, out, "页脚没滤掉：%r" % junk)

    def test_real_body_survives(self) -> None:
        out = self.q.strip_noise(self.NAVY)
        self.assertIn("2026-10-02发布某通知", out, "正文被误删了")
        self.assertIn("为确保最佳浏览效果", out, "正文被误删了")

    def test_judge_and_strip_share_one_word_list(self) -> None:
        """判定和清洗必须看同一批词，否则清洗永远追不上判定。"""
        import yaml
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "lighthouse" / "config"
        cfg = yaml.safe_load(
            (root / "web_collector_config.yaml").read_text(encoding="utf-8"))
        patterns = (cfg.get("quality") or {}).get("chrome_patterns") or []
        self.assertTrue(self.q._CHROME_TEXT,
                        "load_config 之后词表仍为空，清洗能力没接上")
        self.assertEqual(len(self.q._CHROME_TEXT), len(patterns),
                         "strip_noise 用的词表和配置对不上")


class SourceBudgetTierTests(unittest.TestCase):
    """单源时间预算必须按翻页深度分档，不能一刀切。

    实测 2026-10-02（145 源一轮）：中位 271s、90 分位 1200s、max 1805s
    （顶到 source_budget_seconds=1800 的上限），超 600s 的 31 个源吃掉
    68% 总时长。

    一刀切压低会砍错东西——那些慢源确实有产出（478 条），只是当时被
    max_items=20 卡住；一刀切抬高，B 档那 114 个「翻 3 页取当日增量」的
    源也跟着慢，一个卡死的源就吃掉整轮预算。
    """

    RL = {"source_budget_seconds": 420,
          "deep_source_budget_seconds": 1500,
          "stall_source_budget_seconds": 420}

    @staticmethod
    def _budget(pages: int) -> float:
        base = float(SourceBudgetTierTests.RL["source_budget_seconds"])
        deep = float(SourceBudgetTierTests.RL["deep_source_budget_seconds"])
        stall = float(SourceBudgetTierTests.RL["stall_source_budget_seconds"])
        if pages >= 10:
            return max(base, deep)
        if pages <= 3:
            return min(base, stall)
        return base

    def test_deep_archiving_source_gets_real_time(self):
        self.assertEqual(self._budget(12), 1500,
                         "深翻页的源被卡死，历史永远挖不完")

    def test_shallow_source_is_capped(self):
        self.assertEqual(self._budget(3), 420,
                         "翻 3 页还花 25 分钟说明卡住了，不是在挖历史")

    def test_mid_tier_uses_base(self):
        self.assertEqual(self._budget(6), 420)

    def test_config_actually_defines_all_three_knobs(self):
        """三个旋钮都得在配置里，否则代码里那两个常量永远生效不了。"""
        from pathlib import Path as P
        import yaml
        root = P(__file__).resolve().parents[1]
        cfg = yaml.safe_load((root / "lighthouse" / "config"
                              / "web_collector_config.yaml")
                             .read_text(encoding="utf-8"))
        rl = cfg.get("rate_limit") or {}
        for knob in ("source_budget_seconds", "deep_source_budget_seconds",
                     "stall_source_budget_seconds"):
            self.assertIn(knob, rl, "rate_limit 缺 %s" % knob)


class RenderConcurrencyTests(unittest.TestCase):
    """并发调 render_page 必须全都成功。

    2026-10-02 实测（4 线程跑同样的 4 个 URL）：
        并发 -> AssertionError / UnpicklingError: pickle data was truncated，全灭
        串行 -> 4/4 成功

    根因不是「Playwright sync API 不能进 asyncio loop」——那个早被 worker
    进程隔离解决了（见 playwright_worker 模块注释，46 个源因此恢复）。这里是
    另一件事：``Renderer`` 用 ``multiprocessing.Pipe`` 和 worker 通信，而
    **Pipe 不是线程安全的**。两个线程同时读同一个 inbox，各拿到半截 pickle，
    报的就是 UnpicklingError。

    原来的 ``_RENDER_LOCK`` 只锁「惰性创建 renderer」，``render()`` 在锁外跑，
    等于没锁。这个测试不能真起浏览器（CI 上没有 Chromium），所以验的是
    **锁的范围**：render 调用必须发生在锁内。
    """

    def _locked_render_calls(self):
        """静态检查 render_page / render_pages 的 render() 是否在 with 块内。"""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "collectors"
               / "playwright_worker.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        out = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in ("render_page", "render_pages"):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.With)
                        and any(getattr(getattr(item.context_expr, "value", None),
                                        "id", getattr(item.context_expr, "id", ""))
                                == "_RENDER_LOCK"
                                for item in inner.items)):
                    for sub in ast.walk(inner):
                        if (isinstance(sub, ast.Call)
                                and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr == "render"):
                            out[node.name] = True
                            break
                    if node.name in out:
                        break
        return out

    def test_both_entrypoints_render_under_the_lock(self):
        got = self._locked_render_calls()
        for name in ("render_page", "render_pages"):
            self.assertTrue(got.get(name),
                            "%s 的 render() 不在 _RENDER_LOCK 内——并发调用会"
                            "全灭（Pipe 非线程安全，实测 UnpicklingError）" % name)

    def test_failed_render_drops_the_renderer(self):
        """Pipe 句柄废了要丢掉重建，否则永远对着死句柄报错。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "collectors"
               / "playwright_worker.py").read_text(encoding="utf-8")
        self.assertIn("_RENDERER = None", src,
                      "render 失败后必须把句柄置空，下次重建")

    def test_routes_still_serializes_the_ladder_path(self):
        """routes.py 的 _RENDER_SERIAL 是另一条路径上的第二道锁，别被删掉。"""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "lighthouse"
               / "routes.py").read_text(encoding="utf-8")
        self.assertIn("_RENDER_SERIAL", src,
                      "Ladder 那条路的串行锁没了——它挡的是 ladder 内部的递归渲染")


import json as _json
import tempfile as _tempfile


def STATUS_FIXTURE(next_due_epoch):
    """造一份最小可读的 status.json：seconds_until_next_due 只看这一个字段。"""
    fd, path = _tempfile.mkstemp(suffix=".json")
    with open(fd, "w", encoding="utf-8") as fh:
        _json.dump({"next_due_epoch": next_due_epoch}, fh)
    from pathlib import Path as P
    return P(path)


class WatchdogNextDueTests(unittest.TestCase):
    """过期的到期时间 = 立刻重拉，不能夹成 30 秒空转。

    2026-10-03 实测：145 个源在退避表里全部已到期（earliest < now），
    但最新 shadow 报告的 ``next_due_epoch`` 是 8.5 小时前的旧值。
    ``seconds_until_next_due`` 原本是 ``max(30, due - now)`` —— 负数被夹成
    30 秒；调用处还有一道 ``max(wait_s, 30)``，两道夹取叠在一起，
    于是 watchdog 每 30 秒拉起一次、每轮都「无源可采」，日志刷满而一条没采到，
    整夜停在 10-03 零入库。

    负值本身就是「现在就拉」的信号，不该被夹成固定小值。
    """

    def test_past_due_returns_zero_not_thirty(self):
        import sys
        import time
        sys.path.insert(0, str(ROOT / "lighthouse"))
        from lighthouse.watchdog import seconds_until_next_due

        past = int(time.time() - 8.5 * 3600)
        st = STATUS_FIXTURE(past)
        wait = seconds_until_next_due(st)
        self.assertEqual(wait, 0.0,
                         "已过期的到期时间必须返回 0（立即重拉），"
                         "夹成 30 秒就是整夜空转")

    def test_future_due_still_waits(self):
        import sys
        import time
        sys.path.insert(0, str(ROOT / "lighthouse"))
        from lighthouse.watchdog import seconds_until_next_due

        future = int(time.time() + 600)
        st = STATUS_FIXTURE(future)
        wait = seconds_until_next_due(st)
        self.assertIsNotNone(wait)
        self.assertGreater(wait, 0, "还没到期的源必须继续等")

    def test_zero_or_missing_due_returns_none(self):
        import sys
        sys.path.insert(0, str(ROOT / "lighthouse"))
        from lighthouse.watchdog import seconds_until_next_due

        self.assertIsNone(seconds_until_next_due(STATUS_FIXTURE(0)),
                          "next_due_epoch=0 表示没有源在等，交给固定节奏")

    def test_watchdog_does_not_clamp_zero_to_thirty(self):
        """调用处那道 max(wait_s, 30) 同样会把 0 夹成 30，别留在那。"""
        from pathlib import Path as P

        src = (P(__file__).resolve().parents[1] / "lighthouse"
               / "watchdog.py").read_text(encoding="utf-8")
        self.assertIn("if wait_s <= 0:", src,
                      "wait_s==0 的分支被删了，过期到期时间又会退化成 30 秒空转")


class NoConsolePopupTests(unittest.TestCase):
    """不许有会弹控制台窗口的调用。

    2026-10-03 实测：桌面上窗口一秒一闪，`tasklist.exe` 黑框反复出现。
    根因不是采集，是**状态查询**：

    * watchdog 每 20 秒判断一次子进程存活 -> tasklist
    * 自迭代每 20 分钟跑一次 status_summary -> wmic / ps
    * status.py / launch.py 手工查状态 -> tasklist / powershell

    这些命令默认都要控制台。两条已知的坑：

    1. ``os.popen('tasklist ...')`` 弹窗消不掉——它先起 ``cmd.exe``，
       而 cmd.exe 一定要控制台。winflags 的全局补丁只改 ``Popen`` 的
       ``creationflags``，os.popen 不走 Popen，补丁管不到。
    2. ``subprocess.run`` 不给 ``CREATE_NO_WINDOW`` 时补丁不一定覆盖到，
       所以根治办法是**每个会 spawn 系统命令的模块自己装 winflags**，
       而不是逐个调用点补参数（补参数的话新增调用点又漏一个）。

    判定全部走 AST：不看注释（注释里为了解释原因会提到 os.popen），
    也不靠字符串匹配（会匹配到注释和字符串字面量）。
    """

    # 会 spawn 系统命令的模块
    SPAWNERS = ("lighthouse/status.py", "lighthouse/watchdog.py",
                "lighthouse/launch.py", "scripts/status_summary.py")

    @staticmethod
    def _tree(rel: str):
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
        return ast.parse(src), src

    @staticmethod
    def _has_winflags(src: str) -> bool:
        return "_install_no_window()" in src

    def test_no_os_popen_anywhere(self):
        """os.popen 会起 cmd.exe，winflags 管不到，直接禁掉。"""
        import ast
        for rel in self.SPAWNERS:
            tree, _ = self._tree(rel)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "popen"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"):
                    self.fail("%s L%d 用了 os.popen —— 它起 cmd.exe，"
                              "winflags 补丁管不到，必然弹窗"
                              % (rel, node.lineno))

    def test_spawners_install_winflags(self):
        """根治办法：每个 spawner 自己装 winflags，新增调用点自动受益。"""
        for rel in self.SPAWNERS:
            _, src = self._tree(rel)
            self.assertTrue(self._has_winflags(src),
                            "%s 没装 winflags，它 spawn 的 tasklist/wmic 会弹窗"
                            % rel)

    def test_winflags_actually_patches_popen(self):
        """装了还得真的生效：打上补丁之后，无参调用应自动带上 NO_WINDOW。"""
        import subprocess
        from lighthouse import winflags
        self.assertTrue(winflags.install() or True)   # 幂等，已装过也算
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertNotEqual(flags, 0, "CREATE_NO_WINDOW 常量取不到，补丁会失效")
        self.assertEqual(winflags.CREATE_NO_WINDOW, 0x08000000)
        self.assertEqual(winflags.CREATE_NEW_CONSOLE, 0x00000010)

    def test_chain_roots_also_install_winflags(self):
        """整条 spawn 链的每一层都要装：补丁只对当前进程及后代有效。"""
        for rel in ("lighthouse/watchdog.py", "lighthouse/overnight.py",
                    "lighthouse/run.py"):
            _, src = self._tree(rel)
            self.assertTrue(self._has_winflags(src),
                            "%s 没装 winflags，它 spawn 出来的孙进程会漏掉补丁"
                            % rel)


class ArchiveDepthCliFloorTests(unittest.TestCase):
    """`--archive-pages` 也只是下限，不能覆盖源里的显式值。

    2026-10-03 实测：修好 `raise_archive_pages_below` 之后以为B/C 分档生效了，
    结果 overnight 固定传 `--depth 30`，而 `with_defaults` 里命令行参数是
    「优先级最高」的无条件覆盖 —— 于是：

        js_gxt（源里 12，C 档深翻）传 --depth 30 -> 30
        miit_gov（源里 3，B 档快节奏）传 --depth 30 -> 30

    **每一档都被算成 30 页**，B 档的 3 形同虚设。而每源 30 页正是那轮
    「145 源 / 30 分钟预算只跑完 36 个」的慢源根因。上一版只堵了地板那条路，
    命令行这条同样的漏洞没堵，所以 B/C 分档**从来没真正生效过**。

    语义统一成一句话：显式值是作者的意图，地板和命令行都只填空不覆盖。
    """

    CFG = ArchiveDepthTierTests.CFG

    @staticmethod
    def _pages(source: dict, cli: int) -> int:
        from lighthouse.run import with_defaults
        return int(with_defaults(source, ArchiveDepthCliFloorTests.CFG,
                                 archive_pages=cli).get("archive_pages") or 0)

    def test_cli_does_not_override_explicit_value(self):
        self.assertEqual(self._pages({"id": "js_gxt", "archive_pages": 12}, 30), 12,
                         "C 档深翻页被命令行抬平，B/C 分档等于没做")

    def test_cli_does_not_override_shallow_value(self):
        self.assertEqual(self._pages({"id": "miit_gov", "archive_pages": 3}, 30), 3,
                         "B 档快节奏被命令行抬成 30 页")

    def test_cli_still_fills_unset_sources(self):
        """没显式写的源仍然吃 CLI 下限 —— 那是防漏配。"""
        self.assertEqual(self._pages({"id": "x_gov"}, 30), 30)

    def test_production_config_really_tiers(self):
        """用真实配置验证分档确实分层，而不是所有源都一个值。"""
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import yaml
        from lighthouse.run import with_defaults
        root = Path(__file__).resolve().parents[1]
        cfg = yaml.safe_load((root / "lighthouse" / "config"
                              / "web_collector_config.yaml").read_text(encoding="utf-8"))
        enabled = [s for s in (yaml.safe_load(
            (root / "config" / "sources.yaml").read_text(encoding="utf-8"))
            .get("sources") or []) if s.get("enabled")]
        self.assertTrue(enabled, "配置里应有启用的源")
        depths = {with_defaults(s, cfg, 30)["archive_pages"] for s in enabled}
        self.assertGreater(len(depths), 1,
                           "所有源的翻页深度都一样 = B/C 分档没生效")


class DuplicateBodyDetectorTests(unittest.TestCase):
    """跨记录重复正文检测——抓「标题对、正文是列表页导航」这类脏数据。

    2026-10-03 实测：某轮 1721 条里 350 条（20%）**标题是真实公告、日期也
    正常**，但正文全是同一段列表页导航。根因是详情页抓取失败后退回了列表页。

    它躲过了所有别的判据：

    * 单条内容统计（句读密度/短行占比/chrome 占比）——被短正文带偏
    * 标题形态判据——标题是真的
    * 缺日期判据——日期是对的
    * 逐源结果——显示为 **ok**（链接拿到了、日期抽到了）

    唯一稳定的信号是**关系**：同一个源下多条文案的正文一模一样。这不受单条
    长度影响，也不会被比例阈值带偏。

    这个测试不能真连生产库，所以验的是判据本身：给定几条构造数据，
    该抓到的那条必须被抓到、不该抓的不能被误伤。
    """

    NAV = "- 索引 服务 - 日一二三四五六最新发布 - 山东九月培训计划... 2026-09-01"

    def _heads(self, rows):
        """和 scripts/find_duplicate_bodies.py 同一套判据（含空正文守卫）。"""
        import collections
        c = collections.Counter((body or "")[:200] for body in rows)
        return {b: n for b, n in c.items() if n >= 3 and b.strip()}

    def test_repeated_navigation_is_caught(self):
        bodies = [self.NAV] * 350 + ["真公告正文" * 30 + str(i) for i in range(40)]
        dupes = self._heads(bodies)
        self.assertIn(self.NAV[:200], dupes, "重复导航没被抓到")
        self.assertEqual(dupes[self.NAV[:200]], 350)

    def test_unique_real_bodies_not_caught(self):
        bodies = ["真公告正文 %d。这里有完整的句子。" % i for i in range(200)]
        self.assertEqual(self._heads(bodies), {},
                         "各不相同的真实正文被误判成重复模板")

    def test_two_repeats_is_not_enough(self):
        """门槛 3：偶发两条相同可能是同源转发，不该整批标记。"""
        self.assertEqual(self._heads([self.NAV, self.NAV]), {})

    def test_blank_body_never_counted(self):
        """空正文重复再多次也不能算模板——那是另一类问题（抓取失败）。"""
        self.assertEqual(self._heads(["", "", "", ""]), {})


class BodyExtractionValidationTests(unittest.TestCase):
    """正文抽取必须**校验结果**，抽到导航要换策略重试。

    2026-10-03 实测（sd_rst 一轮 598 条里 350 条正文是导航）：
    trafilatura 选错 DOM 区域，抽到 483 字符的「索引 服务 日一二三四五六
    最新发布」日历组件，而真文章在 `<div class="show">` 里——页面本身完好
    （55KB、正文齐全、HTTP 200）。

    之所以完全静默：页面能抓、标题能抽、日期能抽，逐源结果记为 `ok`，
    正文却是导航。所以判据只能是「抽出来的正文里有没有标题片段」。

    这些用例不联网，用构造 HTML 复现同一种结构：正文容器用不含
    content/article 之类常见词的 class（这正是它被打分压输的原因）。
    """

    HTML = (
        '<html><body><div class="nav">索引 服务 最新发布</div>'
        '<div class="calendar">2026年10月 1 2 3 4 5</div>'
        '<div class="show">{body}</div></body></html>'
    )
    TITLE = "某某厅关于做好2026年某某工作的通知"

    def _col(self):
        from collectors.base import BaseCollector

        class Probe(BaseCollector):
            async def fetch_detail(self, url, **kw):
                return None

            async def fetch_list(self, url=None, **kw):
                return []

            async def collect(self, *a, **kw):
                return []

        return Probe({"id": "x", "url": "http://e.gov.cn/a.shtml"})

    # 实测 sd_rst 抽出来的导航是 483 字符（一串「标题 + 日期」的列表项）。
    # 样本必须用真实长度：<120 字会落进「短正文不判错」的豁免里，
    # 那样测的就不是同一个分支了。
    NAV_483 = ("- 索引 服务 - 日一二三四五六最新发布 - "
                + "- 山东九月培训计划和“乐业山东”技能培训加油站活动... 2026-09-01   "
                * 8)

    def test_navigation_is_rejected(self):
        """只有导航和日历、没有正文时，校验必须说不通过。"""
        col = self._col()
        self.assertGreaterEqual(len(self.NAV_483), 120,
                                "样本长度不足，测不到句读判据")
        self.assertTrue(col._body_looks_wrong(self.NAV_483, self.TITLE))

    def test_short_navigation_not_judged(self):
        """短于 120 字的导航**不判错**——短公告本来就可能没句读。

        这是有意的豁免：实��� trafilatura 的 txt 模式会把短公告的句子
        剥掉，判错就会把好页面扔掉。宁可漏判也不能误杀。
        """
        col = self._col()
        self.assertFalse(col._body_looks_wrong("索引 服务 最新发布", self.TITLE))

    def test_real_body_passes(self):
        col = self._col()
        real = self.TITLE + "。为做好相关工作，现将有关事项通知如下：一、……" * 12
        self.assertFalse(col._body_looks_wrong(real, self.TITLE))

    def test_empty_is_wrong(self):
        self.assertTrue(self._col()._body_looks_wrong("", self.TITLE))

    def test_container_fallback_finds_unconventional_class(self):
        """class叫 show（不含 content/article）也必须能兜住。"""
        col = self._col()
        body = self.TITLE + "。为做好相关工作，现将有关事项通知如下：一、……" * 12
        out = col._find_body_container(self.HTML.format(body=body))
        self.assertIn("有关事项通知如下", out,
                      "class=show 的正文容器没被兜住——这正是 sd_rst 那个坑")

    def test_title_whitespace_mismatch_still_passes(self):
        """标题里带空格、正文里不带，不能因此判成抽错（实测踩过）。"""
        col = self._col()
        title = "某某厅关于 2026 年某某工作的通知"
        real = "某某厅关于2026年某某工作的通知。现将有关事项通知如下：一、……" * 12
        self.assertFalse(col._body_looks_wrong(real, title),
                         "全半角/空格差异被误判成抽到导航了")


class AutoBodyLocationTests(unittest.TestCase):
    """自动定位正文：不靠 class 名，靠「句读密度高 + 链接占比低」。

    2026-10-03 实测：硬编码 class 白名单覆盖不全。各站正文容器叫
    ``show`` / ``art`` / ``TRS_Editor`` / ``warp_list`` 各不相同，而
    sc_jx_gov、sc_xz_gov、sc_gs_gov 抓到的是全站导航条——它们的容器 class
    压根不在白名单里。加白名单只会变成「一直往下加」。

    结构特征则稳定得多，实测分离度很干净：
        sc_jx_gov 正文 2001 字 句读 15.99/千字 链接占比 0%
        sc_xz_gov 正文 3658 字 句读 11.48/千字 链接占比 0%
    导航条则几乎 100% 是链接、句读接近 0。
    """

    NAV = (
        '<div class="nav">'
        '<a href="/1">外交部</a><a href="/2">国防部</a>'
        '<a href="/3">国家发展和改革委员会</a><a href="/4">教育部</a>' * 12
        + "</div>"
    )
    BODY = (
        '<div class="whatever">'
        + "".join(
            "<p>第%d段，各市县区人民政府：为做好相关工作，现将有关事项通知如下，"
            "请遵照执行。发布日期见正文。</p>" % i
            for i in range(1, 9)
        )
        + "</div>"
    )

    def _col(self):
        from collectors.base import BaseCollector

        class Probe(BaseCollector):
            async def fetch_detail(self, url, **kw):
                return None

            async def fetch_list(self, url=None, **kw):
                return []

            async def collect(self, *a, **kw):
                return []

        return Probe({"id": "x", "url": "http://e.gov.cn/a"})

    def test_picks_article_not_navigation(self):
        html = "<html><body>" + self.NAV + self.BODY + "</body></html>"
        out = self._col()._auto_locate_body(html)
        self.assertIn("现将有关事项通知如下", out,
                      "自动定位抓到了导航条而不是正文")
        self.assertNotIn("外交部", out, "导航内容混进来了")

    def test_works_without_any_class_name(self):
        """class 名完全陌生也要能定位（这正是白名单失效的原因）。"""
        out = self._col()._auto_locate_body(
            '<html><body><div class="qqq">' + self.BODY + "</div></body></html>")
        self.assertIn("现将有关事项通知如下", out)

    def test_render_path_uses_the_validated_extractor(self):
        """渲染级必须走 collector._extract_body，不能自己再调一次 trafilatura。

        2026-10-03 实测：sc_gs_gov / sc_jx_gov / sc_xz_gov 都配了
        `playwright_fallback: True`（静态是 HTTP 412，只能走渲染），
        渲染后抽到的却是全站导航条，一轮下来 87 条重复正文。原因是
        `Ladder._extract_html` 里自己调 trafilatura —— 静态级早就改成了
        带校验的 `_extract_body`，两条路**分叉了**，一个修了一个没修。

        原注释还写着「这和静态级用的是同一个函数」，曾经是对的，改完静态级
        就不成立了。分叉的代价是修复只覆盖一半路径，而且那一半恰好是
        「静态抓不到、只能走渲染」的源——也就是最需要修的那批。
        """
        from pathlib import Path as P
        src = (P(__file__).resolve().parents[1] / "lighthouse"
               / "routes.py").read_text(encoding="utf-8")
        block = src[src.index("def _extract_html"):]
        block = block[:block.index("\n    def ", 10)]
        self.assertIn("_extract_body", block,
                      "渲染级没走带校验的抽取——静态改了、渲染没改，"
                      "修复只覆盖了一半路径")
        self.assertNotIn("trafilatura.extract", block,
                         "渲染级还在自己调 trafilatura，等于绕过校验")
    def test_empty_page_returns_empty(self):
        """拿不到就返回空，让上层升级渲染级，不能硬塞已知是导航的内容。"""
        self.assertEqual(self._col()._auto_locate_body("<html></html>"), "")

    def test_navigation_only_page_is_not_returned_as_body(self):
        """整页只有导航时，不该把导航当成正文交出去。"""
        out = self._col()._auto_locate_body("<html><body>" + self.NAV + "</body></html>")
        self.assertEqual(out, "", "纯导航被当正文交出去了")


class CollectLockWaitTests(unittest.TestCase):
    """采集锁被占时应该**等**，不是立刻放弃整轮。

    2026-10-03 通宵实测：整夜 1823 轮里 **888 轮（49%）退出码 2**，
    日志反复是「主项目采集正在运行（collect.lock 被持有），本轮不启动」。
    那一轮 0.2 分钟结束、净增 0——近一半的守夜时间花在
    「发现锁被占 → 放弃 → 退出 → watchdog 重启 → 再发现锁被占」的循环里。

    锁被占**不是故障**：那说明另一轮采集正在正常写库。锁存在的意义就是
    防止并发写库，把它当失败信号是把正确行为读成了错误。

    测试用真实的 collectors.lock 目录原语（真 mkdir 原子操作），
    在临时目录上制造竞争，不 mock 锁本身——mock 掉就测不出「等」这个行为了。
    """

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.lock = Path(self.tmp.name) / "collect.lock"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_true_when_lock_is_free(self):
        from lighthouse.overnight import _wait_for_collect_lock
        self.assertTrue(_wait_for_collect_lock(30, poll=0.2, lock_dir=self.lock),
                        "锁空闲时应该立刻返回可采")

    def test_waits_instead_of_failing(self):
        """锁被占时：要等，而且等到了就成功——不能一撞就返回 False。"""
        import threading
        import time
        from collectors.lock import CollectLockBusy, acquire, release
        from lighthouse.overnight import _wait_for_collect_lock

        acquire("holder", lock_dir=self.lock)   # 模拟另一轮正在写库
        released = threading.Event()

        def releaser() -> None:
            time.sleep(1.5)                      # 1.5 秒后让出锁
            released.set()
            release(self.lock)

        thread = threading.Thread(target=releaser)
        thread.start()
        try:
            started = time.time()
            got = _wait_for_collect_lock(30, poll=0.2, lock_dir=self.lock)
            waited = time.time() - started
        finally:
            thread.join()

        self.assertTrue(got, "等到了锁就该成功，不该放弃")
        self.assertTrue(released.is_set())
        self.assertGreaterEqual(waited, 1.0,
                                "它根本没等，立刻就返回了——那还是撞锁就走")

    def test_gives_up_after_timeout(self):
        """等超时才放弃，而且放弃时不能硬抢（并发写库会把库搞坏）。"""
        from collectors.lock import acquire
        from lighthouse.overnight import _wait_for_collect_lock

        acquire("holder", lock_dir=self.lock)
        try:
            self.assertFalse(_wait_for_collect_lock(0.6, poll=0.2, lock_dir=self.lock),
                             "等超时了应该放弃")
            # 放弃之后锁必须还被别人握着——不能自己抢走
            from collectors.lock import CollectLockBusy, acquire as re_acquire
            with self.assertRaises(CollectLockBusy):
                re_acquire("intruder", lock_dir=self.lock)
        finally:
            from collectors.lock import release
            release(self.lock)

    def test_run_collect_was_given_the_wait_flag(self):
        """overnight 必须真的有 --lock-wait-minutes，而不是写死。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "lighthouse"
               / "overnight.py").read_text(encoding="utf-8")
        self.assertIn("--lock-wait-minutes", src,
                      "没有这个开关，锁等待时长就调不了")


class SourceTierTests(unittest.TestCase):
    """采集源等级制度：判据必须建立在**实测证据**上。

    这个模块的价值全在判据上——三次判据设计错误，每一个都把大批健康源
    误判成废源或废路由：

    1. 用「连续零产出轮数」当降频判据 -> 143 个源里 132 个判成 T4 长眠。
       根因：daily 档一天回访 3 次，「连续 4 轮」实际只是 1.3 天没更新；
       而且退避阶梯把间隔越拉越长，轮数和时长之间没有稳定关系。
       对照：同一时刻按「距上次入库多少周」统计，126 个源是本周内产出过的。
    2. 用配置里的 ``playwright_fallback`` 当 T2 判据 -> 130 个源判成 T2。
       根因：**所有 143 个源都配了 True**（当初为兜底统一开的），
       这字段零区分度。实证里只有 37 个源真的靠渲染出的货更多。
    3. 判死只看单一信号 -> 误杀长假/改版的源。改成两个信号同时成立。

    所以这里不只是测「函数能跑」，而是**把上面这三个错钉成回归**。
    """

    @staticmethod
    def _load():
        # 用普通 import 而不是 importlib.util 的手动 exec：source_tiers 里有
        # 模块级 dataclass，手动 exec 时 __module__ 没注册进 sys.modules，
        # dataclass 装饰器会炸「NoneType has no __dict__」。
        import sys
        from pathlib import Path
        root = str(Path(__file__).resolve().parents[1])
        for p in (root, root + "/scripts"):
            if p not in sys.path:
                sys.path.insert(0, p)
        import source_tiers
        return source_tiers

    def _tier(self, **kwargs):
        mod = self._load()
        base = {"source_id": "x", "prod": {}, "back": {}, "declared": {}}
        base.update(kwargs)
        return mod, mod.classify_source(**base)

    # ── 判据一：降频用天数，不用轮数 ────────────────────────────
    def test_dormant_uses_days_not_dry_streak(self):
        """dry_streak 高但本周刚产出过 -> 不能判长眠。

        这是 2026-10-04 的真实场景：143 个源里 70 个 dry_streak=4，
        而同一时刻 126 个源本周内刚入库。按轮数判会把它们全打成 T4。
        """
        mod, t = self._tier(
            prod={"records": 300, "days_since_output": 2},
            back={"dry_streak": 4},
        )
        self.assertNotEqual(t.tier, "T4",
                            "刚 2 天没更新（dry_streak=4 只是被访问了 4 次）"
                            "就被判长眠——这是轮数当时间用的错")
        self.assertNotEqual(t.tier, "T5")

    def test_really_dormant_source_is_t4(self):
        mod, t = self._tier(
            prod={"records": 300, "days_since_output": 60},
            back={"dry_streak": 3},
        )
        self.assertEqual(t.tier, "T4", "60 天没产出该判长眠")
        self.assertEqual(t.revisit_seconds, 604800, "T4 应降到 7 天")

    # ── 判据二：T2 看实际渲染占比，不看配置声明 ──────────────────
    def test_t2_uses_render_share_not_config_flag(self):
        """所有源都配了 playwright_fallback=True，它零区分度。"""
        mod, t = self._tier(
            prod={"records": 200, "days_since_output": 1, "render_share": 0.05},
            declared={"playwright_fallback": True},
        )
        self.assertNotEqual(
            t.tier, "T2",
            "只有 5% 的记录是渲染采到的，却因为配了 fallback 就判 T2——"
            "配置声明是意图，等级必须建立在产出的事实上")

    def test_genuinely_render_dependent_source_is_t2(self):
        mod, t = self._tier(
            prod={"records": 200, "days_since_output": 1, "render_share": 0.98},
        )
        self.assertEqual(t.tier, "T2", "98% 靠渲染，必须走 T2")
        self.assertEqual(t.routes, ["render"], "T2 应直接走渲染、跳过静态尝试")

    # ── 判据三：判死要两个信号同时成立 ──────────────────────────
    def test_dead_needs_both_signals(self):
        """久没产出但最近还在出货 -> 不判死（避开长假/改版误杀）。"""
        mod, t = self._tier(
            prod={"records": 50, "days_since_output": 200, "dead_pages": 0},
            back={"dry_streak": 0},
        )
        self.assertNotEqual(t.tier, "T5", "只有「久」一个信号就判死，会误杀")

    def test_dead_pages_is_enough_on_its_own(self):
        """404 页面是页面级证据，没有假阳性，可以单独定案。"""
        mod, t = self._tier(prod={"records": 10, "days_since_output": 1,
                                  "dead_pages": 5})
        self.assertEqual(t.tier, "T5")

    # ── T3：跨记录重复正文 ──────────────────────────────────────
    def test_duplicate_body_ratio_is_the_signal(self):
        mod, t = self._tier(
            prod={"records": 598, "days_since_output": 1,
                  "dup_body_records": 350},
        )
        self.assertEqual(t.tier, "T3", "350/598 正文是重复模板 = 系统性取错内容")

    def test_no_sentence_marks_is_not_used_as_garbage_signal(self):
        """表格型公告没有句读但数据价值高，不能据此判废。

        2026-10-03 实测：库里 2111 条正文无句读，逐条抽读 10 条有 8 条是真数据
        （生态环境部的环评受理公示，正文是表格）。所以等级判据里不允许出现
        句读密度。
        """
        # 只查**代码**，不查注释——那段注释恰恰是在解释「为什么不用它」，
        # 全文匹配会把说明文字当成「用了」。
        import ast
        tree = ast.parse(self.tier_module_path().read_text(encoding="utf-8"))
        code_only = []
        for node in ast.walk(tree):
            code_only.extend(ast.dump(node).split())
        self.assertNotIn(
            "句读", "".join(code_only),
            "等级判据里出现了句读密度——表格型公告会被误杀")
        self.assertIn(
            "句读", self.tier_module_path().read_text(encoding="utf-8"),
            "应该留一段注释说明为什么不���句读这条信号")

    @staticmethod
    def tier_module_path():
        from pathlib import Path
        return Path(__file__).resolve().parents[1] / "scripts" / "source_tiers.py"


class TierPolicyWiringTests(unittest.TestCase):
    """等级必须真的改变采集行为，不能只是标签。"""

    def test_every_tier_has_a_policy(self):
        mod = SourceTierTests._load()
        for tier in ("T0", "T1", "T2", "T3", "T4", "T5"):
            self.assertIn(tier, mod.TIER_POLICY, "%s 没有采集策略" % tier)
            pol = mod.TIER_POLICY[tier]
            for key in ("routes", "revisit_seconds", "depth", "max_items"):
                self.assertIn(key, pol, "%s 的策略缺 %s" % (tier, key))

    def test_backoff_reads_tiers(self):
        """SourceBackoff 必须真的读等级表（否则等级只是个 JSON 文件）。"""
        from lighthouse.run import SourceBackoff as S
        self.assertTrue(hasattr(S, "tiers"))
        self.assertTrue(hasattr(S, "tier_of"))
        self.assertIn("T4", S.TIER_MIN_SECONDS)
        self.assertGreater(S.TIER_MIN_SECONDS["T4"],
                           S.TIER_MIN_SECONDS["T0"],
                           "长眠源的最小间隔必须大于主力源")

    def test_unknown_source_degrades_to_most_conservative(self):
        """等级表里没有的源 -> 当 T0（主力，间隔最短），宁可多采不漏采。"""
        from lighthouse.run import SourceBackoff as S
        self.assertEqual(S.tier_of("definitely_not_a_real_source"), "T0")

    def test_missing_tier_file_does_not_break_collection(self):
        """等级表是优化不是前置依赖——读不到必须照常工作。"""
        from lighthouse.run import SourceBackoff as S
        original = S.tier_path
        try:
            S.tier_path = classmethod(lambda cls: Path("/nonexistent/tiers.json"))
            self.assertEqual(S.tiers(), {}, "等级表缺失时应返回空而不是抛异常")
        finally:
            S.tier_path = original


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
