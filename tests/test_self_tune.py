"""自迭代诊断的行为测试。

这些判定直接决定"要不要动配置"，所以每条规则都要有用例 —— 尤其是
**不该动手**的那些：门禁阈值经实测是对的，放宽它等于绕过反爬。
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighthouse import self_tune


def shadow(sources, **extra):
    data = {"sources": sources, "records": len(sources), "dropped": 0,
            "by_reason": {}, "buckets": {}}
    data.update(extra)
    return data


def src(sid, records=0, dropped=0, seconds=0.0, note=""):
    return {"source_id": sid, "records": records, "dropped": dropped,
            "seconds": seconds, "notes": [note] if note else ["-"]}


def kinds(report):
    return {f.kind for f in report.findings}


class SelfTuneTests(unittest.TestCase):
    def test_reports_early_stop_as_drained(self):
        rep = self_tune.analyse(shadow([
            src("a", note="early stop after 40 known urls"),
            src("b", note="early stop after 2 known urls"),
        ]))
        self.assertIn("drained_sources", kinds(rep))
        finding = next(f for f in rep.findings if f.kind == "drained_sources")
        self.assertEqual(set(finding.sources), {"a", "b"})
        # 采干是正常状态，不该触发改配置的动作
        self.assertTrue(finding.auto)
        self.assertEqual(finding.stage, "collect")

    def test_slow_zero_needs_enough_sources_before_suggesting(self):
        few = self_tune.analyse(shadow([
            src("a", seconds=500, note="source time budget 300s exhausted after 0 records"),
        ]))
        self.assertNotIn("time_budget_wasted", kinds(few),
                         "只有 1 个源就建议调预算，样本不足")

        many = self_tune.analyse(shadow([
            src("a", seconds=500, note="source time budget 300s exhausted after 0 records"),
            src("b", seconds=480, note="source time budget 300s exhausted after 0 records"),
            src("c", seconds=460, note="source time budget 300s exhausted after 0 records"),
        ]))
        self.assertIn("time_budget_wasted", kinds(many))

    def test_no_links_points_at_selector_repair(self):
        rep = self_tune.analyse(shadow([
            src("a", note="no links found on list page"),
        ]))
        finding = next(f for f in rep.findings if f.kind == "no_links")
        self.assertEqual(finding.stage, "extract")
        self.assertIn("repair.py", finding.action)

    def test_all_dropped_never_auto_relaxes_gates(self):
        rep = self_tune.analyse(shadow([
            src("a", dropped=30), src("b", dropped=25),
        ], by_reason={"markdown:link_flood": 55}))
        finding = next(f for f in rep.findings if f.kind == "all_dropped")
        self.assertEqual(finding.stage, "extract")
        self.assertFalse(finding.auto, "全被拦下也不能自动放宽门禁")
        self.assertGreaterEqual(finding.evidence["per_source"]["a"], self_tune.DROPPED_ONLY_MIN)

    def test_concentrated_gate_drops_advise_verify_not_relax(self):
        rep = self_tune.analyse(shadow(
            [src("a", dropped=30)],
            by_reason={"markdown:link_flood": 45, "markdown:chrome": 5}))
        self.assertIn("gate_drop_concentrated", kinds(rep))
        finding = next(f for f in rep.findings if f.kind == "gate_drop_concentrated")
        self.assertEqual(finding.stage, "analyze")
        self.assertFalse(finding.auto)
        self.assertIn("别动全局阈值", finding.action)

    def test_summary_gap_only_fires_above_threshold(self):
        low = self_tune.analyse(shadow([src("a", records=1)]),
                                collected=100, ingested=95, summary_gap=5)
        self.assertNotIn("summary_gap_high", kinds(low))

        high = self_tune.analyse(shadow([src("a", records=1)]),
                                 collected=100, ingested=60, summary_gap=40)
        finding = next(f for f in high.findings if f.kind == "summary_gap_high")
        self.assertEqual(finding.stage, "summary")
        self.assertIn("别当脏数据删", finding.action)
        # 正文为图片的真公告抽不出句子是能力边界，不能当脏数据删
        self.assertFalse(finding.auto)

    def test_all_four_stages_are_representable(self):
        rep = self_tune.analyse(shadow([
            src("a", note="early stop after 40 known urls"),
            src("b", note="no links found on list page"),
            src("c", dropped=20),
        ], records=100, by_reason={"markdown:link_flood": 30}),
            collected=100, ingested=50, summary_gap=50)
        self.assertEqual(
            {f.stage for f in rep.findings} & set(self_tune.ALL_STAGES),
            {"collect", "extract", "summary", "analyze"})

    def test_empty_report_is_not_an_error(self):
        rep = self_tune.analyse(shadow([]))
        self.assertIsInstance(rep.findings, list)
        self.assertIn("coverage", kinds(rep))


class BackoffCadenceTests(unittest.TestCase):
    """回访间隔闸：防止一轮 10~15 分钟时，一天敲同一批源 30 遍门。"""

    def _backoff(self, use_override: bool = True):
        """载入 run.py。

        `use_override=False` 时禁用退避阶梯的运行时 override，让测试看到的是
        **类里声明的基线 LADDER**，而不是当前生产值。

        实测踩过（2026-10-02）：阶梯等价性测试直接调 `backoff_interval()`，
        而那个方法读的是运行时 override。自主迭代的 `loosen_backoff` 一跑
        （daily 8/16/32/48/96 -> 8/12.25/24.5/36.75/73.5），「与旧倍率行为
        逐档等价」就红了——而那不是回归，是有人**合法地**调了阶梯。
        断言基线等价性的测试就不该看可变量，否则每跑一次自主动作就红一次。
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_lhrun", Path(__file__).resolve().parents[1] / "lighthouse" / "run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not use_override:
            # backoff_interval() 读的是 cls.LADDER，而 override 在**导入时**
            # 就把 LADDER 整个换掉了（不是每次调用现读文件）。所以要还原的
            # 是类常量本身，光把 _override_path 指到不存在的文件没用——
            # 那只挡新写的 override，挡不住导入时已经加载进来的。
            module.SourceBackoff.LADDER = dict(module.BACKOFF_LADDER)
        return module

    def test_just_visited_source_sleeps_regardless_of_streak(self):
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("a", produced=True)          # 有产出，streak=0，本不该退避
        self.assertTrue(b.is_sleeping_for("a", "daily"),
                        "刚访问过就必须等够间隔，哪怕它有产出")

    def test_daily_vs_weekly_uses_different_floor(self):
        """daily / weekly 必须用各自的门槛，而不是同一个。

        偏移量按 CADENCE_SECONDS 算，不写死小时数 —— daily 门槛从 2 小时提到
        8 小时（2026-09-30，为省掉每轮 46% 的零产出时间）之后，写死「3 小时前」
        的写法会在 daily 门槛变化时静默失效：3 < 8，两种 tier 都会被拦住，
        测试却断言 daily 应该放行。
        """
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("a", produced=True)

        # 挪到「刚过 daily 门槛、但远未到 weekly 门槛」的时刻
        daily = m.CADENCE_SECONDS["daily"]
        weekly = m.CADENCE_SECONDS["weekly"]
        offset = int(daily + (weekly - daily) / 2)
        b.data["a"][2] = int(time.time()) - offset

        self.assertFalse(b.is_sleeping_for("a", "daily"),
                         "过了 daily 门槛就该放行")
        self.assertTrue(b.is_sleeping_for("a", "weekly"),
                        "没过 weekly 门槛就该继续拦着")

    def test_due_at_never_under_reports_the_sleep_gate(self):
        """``due_at()`` 报的时间必须**不早于** ``is_sleeping_for()`` 放行的时刻。

        这是 2026-10-06 修的一个潜伏 bug 的钉子。``due_at`` 曾经只算退避间隔、
        漏了 ``TIER_MIN_SECONDS`` 的等级下限，于是健康源被少报：T5 报 8 小时而
        实际闸门是 720 小时（少报 29 天）。后果是调度器算出一个很近的下一次
        时间，准点醒来却发现所有源都被闸门挡着，一轮 0.2 分钟、零入库。

        断言写成「取交集」而不是逐档写死数值：只要两者对**任意**
        (档位, dry_streak, 时刻) 组合都一致，这个 bug 就不可能再回来，
        以后有人改 TIER_MIN_SECONDS 或 LADDER 也不会假红。

        两个函数读同一份数据（``b.data``），所以逐个组合穷举即可。

        ★ 档位必须显式 mock。踩过一次：直接用假 source_id（``"s"``）去穷举，
        ``tier_of`` 查不到就返回 T0，而 T0 的等级下限是 0 —— 于是这个测试在
        **有 bug 的代码上照样全绿**，等于什么都没钉。实测就是这么被骗过去的：
        还原成修复前的 due_at，这条测试通过，只有下面那条 T5 的红了。
        """
        m = self._backoff()
        b = m.SourceBackoff()
        orig = m.SourceBackoff.tier_of

        for tier in ("T0", "T1", "T2", "T3", "T4", "T5"):
            m.SourceBackoff.tier_of = classmethod(lambda cls, sid, _t=tier: _t)
            for dry in range(0, 6):
                for age in (0, 3600, 8 * 3600, 12 * 3600, 16 * 3600,
                            3 * 86400, 40 * 86400):
                    b.data.clear()
                    b.next_run()
                    b.record("s", produced=dry == 0)
                    # 把连续零产出调到目标档位
                    b.data["s"][m.SourceBackoff.F_DRY_STREAK] = dry
                    last = int(time.time()) - age
                    b.data["s"][m.SourceBackoff.F_LAST_EPOCH] = last

                    due = b.due_at("s", "daily")
                    sleeping = b.is_sleeping_for("s", "daily")

                    # 正在睡 ⟺ now < due。两者必须给出同一个答案。
                    self.assertEqual(
                        sleeping, due > time.time(),
                        msg=(f"档={tier} dry={dry} age={age}s："
                             f"is_sleeping_for={sleeping} 但 due_at={due - last}s 后。"
                             f"due_at 早于实际闸门 = 调度器会被骗。"))
        m.SourceBackoff.tier_of = orig

    def test_due_at_respects_tier_floor_for_healthy_source(self):
        """具体钉住最严重的那一档：T5（不可采，30 天下限）在 dry=0 时不能报 8 小时。

        上一条是全组合穷举，这条把 712 小时少报那个具体数字写死，
        免得有人只看穷举没看懂严重性。
        """
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("s", produced=True)          # dry=0，健康
        last = int(time.time()) - 8 * 3600    # 8 小时前访问过
        b.data["s"][m.SourceBackoff.F_LAST_EPOCH] = last

        # 强制成 T5：tier_of 读 tier 表，用 mock 替掉，不依赖生产数据
        orig = m.SourceBackoff.tier_of
        m.SourceBackoff.tier_of = classmethod(lambda cls, sid: "T5")
        try:
            due = b.due_at("s", "daily")
            floor = m.SourceBackoff.TIER_MIN_SECONDS["T5"]
            self.assertGreaterEqual(
                due, last + floor,
                f"T5 下限 {floor}s，due_at 报的间隔只有 {due - last}s —— 少报了等级下限")
            self.assertTrue(b.is_sleeping_for("s", "daily"),
                            "刚访问过的 T5 源必须继续被闸门拦着")
        finally:
            m.SourceBackoff.tier_of = orig

    def test_sleep_ladder_preserves_the_old_multiplier_behaviour(self):
        """绝对阶梯必须和旧的「倍率 × 墙钟下限」**逐档等价**。

        改判据之前先用这个测试钉住行为：不是「新写法看起来对」，而是
        「dry=0..9 每一档的间隔都和改之前一样」。差一档就是行为变更，
        得当成新功能评审，不能混在重构里悄悄改掉。

        旧值（2026-10-02 起生效）：daily floor=8h，SLEEP_ROUNDS=(2,4,6,12)
        => 8 / 16 / 32 / 48 / 96 小时。
        """
        m = self._backoff(use_override=False)   # 测基线，不测自主动作改过的值
        c = m.SourceBackoff
        floor = int(m.CADENCE_SECONDS["daily"])
        old_sleep = (2, 4, 6, 12)          # 旧实现的乘数，已删除
        limit = int(c.DRY_STREAK_LIMIT)

        def old_interval(dry):
            if dry < limit:
                return floor
            return old_sleep[min(dry - limit, len(old_sleep) - 1)] * floor

        for dry in range(10):
            self.assertEqual(
                c.backoff_interval(dry), old_interval(dry),
                "dry=%d 时新阶梯 %d 秒 != 旧行为 %d 秒"
                % (dry, c.backoff_interval(dry), old_interval(dry)))

    def test_backoff_ladder_is_monotonic_and_bounded(self):
        """阶梯必须递增，且封顶不超过一周（再长源就饿死了）。"""
        m = self._backoff(use_override=False)
        c = m.SourceBackoff
        values = [c.backoff_interval(d) for d in range(12)]
        self.assertEqual(values, sorted(values),
                         "阶梯必须随零产出递增：%s" % values)
        self.assertLessEqual(
            values[-1], 7 * 24 * 3600,
            "封顶 %d 秒太长，源会被饿死" % values[-1])
        self.assertGreaterEqual(
            values[0], 8 * 3600,
            "首档不得低于 daily 的礼貌下限 8 小时")

    def test_backoff_ladder_does_not_scale_with_cadence_floor(self):
        """★ 阶梯是**绝对值**，改 floor 不该再等比放大它。

        这是本次改动的全部意义。2026-10-01 整夜零入库的直接原因：
        同一个上午把 daily floor 从 2 小时提到 8 小时（礼貌），退避跟着
        从 6 小时变成 24 小时，34 个源整夜全被挡住，而系统一切正常。
        """
        m = self._backoff()
        c = m.SourceBackoff
        before = {d: c.backoff_interval(d) for d in range(8)}
        original = dict(m.CADENCE_SECONDS)
        try:
            m.CADENCE_SECONDS["daily"] = 2 * 3600      # 模拟「改回 2 小时」
            after = {d: c.backoff_interval(d) for d in range(8)}
            self.assertEqual(
                before, after,
                "改 CADENCE_SECONDS 就把退避等比放大了 —— 这正是 2026-10-01 "
                "整夜零入库的成因")
        finally:
            m.CADENCE_SECONDS.clear()
            m.CADENCE_SECONDS.update(original)

    def test_backoff_ladder_covers_every_tier(self):
        """每个 cadence_tier 都要有自己的阶梯，不能回落到 daily。

        weekly 源用 daily 的 8 小时门槛 = 每小时敲一次门，那不是「保守」，
        是把周更的站当小时更的站折腾。
        """
        m = self._backoff()
        c = m.SourceBackoff
        self.assertGreater(
            c.backoff_interval(0, "weekly"), c.backoff_interval(0, "daily"),
            "weekly 的首档必须比 daily 长")
        self.assertGreater(
            c.backoff_interval(3, "monthly"), c.backoff_interval(3, "weekly"),
            "monthly 的退避必须比 weekly 长")
        # 不认识的 tier 回落到 daily，而不是崩
        self.assertEqual(c.backoff_interval(0, "不存在的档"),
                         c.backoff_interval(0, "daily"))

    def test_due_at_matches_is_sleeping_for(self):
        """due_at 和 is_sleeping_for 必须用同一个间隔算。

        两个算得不一样时，调度器会按 due_at 等，而实际早就到点了 —— 于是
        空等；反过来则是刚到点就扑上去，永远跑不到点。
        """
        import time as _t

        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        for tier in ("daily", "weekly"):
            b.record("a", produced=True)
            due = b.due_at("a", tier)
            self.assertGreater(due, 0, "%s: due_at 应给出到期时刻" % tier)
            # 到期时刻 - 现在 应该正好等于那一档的间隔
            wait = due - _t.time()
            expected = m.SourceBackoff.backoff_interval(0, tier)
            self.assertAlmostEqual(
                wait, expected,
                msg="%s: due_at 说等 %.0f 秒，实际门槛是 %.0f 秒"
                    % (tier, wait, expected),
                delta=5)
            # 还没到期时 is_sleeping_for 必须为真
            self.assertTrue(b.is_sleeping_for("a", tier))

    def test_dry_streak_backoff_is_long_enough_for_weekly_ministry_sources(self):
        """采干退避的时长要对得起「周/月更新」这个事实，也不能长到饿死源。

        实测 2026-09-30 19:42 那轮：34 个源里 33 个「all N list urls already
        known」、0 入库、源耗时 2024 秒全是空转。这些部委源的公告更新周期是
        周/月计，确实需要退避。

        但这个判据原来写的是「首个退避档 ≥ 24 小时」，那是 2026-10-02 整夜
        零入库的直接原因：34 个源全被挡在闸内，12 小时只完成 1 轮，日志全是
        「正常完成（无源可采）」。**24 小时不是下限，是上限。**
        现在头档 = 2 × 8 小时 = 16 小时。

        两条约束一起才是对的：
          * 头档不短于「一天回访一次」——采干的源每天该被看一次，不是每三天
          * 封顶不超过一周——再长就等于把源饿死，真出故障也不会被发现

        判据用**换算成小时**，不写死轮数：daily 门槛从 2 小时提到 8 小时
        （2026-09-30）之后，写死「2 轮 = 1 天」这种换算会静默失效。
        """
        m = self._backoff()
        c = m.SourceBackoff
        daily_hours = m.CADENCE_SECONDS["daily"] / 3600.0
        hours = c.backoff_interval(c.DRY_STREAK_LIMIT) / 3600.0
        first = c.DRY_STREAK_LIMIT
        # 下限：一天一次。低于它，采干源会被每小时敲一次门（实测烧掉
        # 46% 的轮次预算且零产出）。
        self.assertGreaterEqual(
            hours, 12,
            "首个退避档只等 %.0f 小时（%d 轮 × %.0f 小时），"
            "采干的源会被反复回访" % (hours, first, daily_hours))
        # 上限：一天两次。超过就是饿死——2026-10-02 整夜 34 个源全被挡、
        # 零入库，就是这个上限被写成 24 小时造成的。
        self.assertLessEqual(
            hours, 24,
            "首个退避档等 %.0f 小时（%d 轮 × %.0f 小时）太长："
            "34 个源会整夜被挡，一夜只跑一轮" % (hours, first, daily_hours))
        # 递增且封顶合理：一周内必须回来看一次。
        ladder = [c.backoff_interval(d) for d in range(8)]
        self.assertEqual(ladder, sorted(ladder),
                         "退避必须随连续零产出递增：%s" % ladder)
        cap_hours = ladder[-1] / 3600.0
        self.assertLessEqual(cap_hours, 7 * 24,
                             "封顶 %.0f 小时太大会让真出故障的源迟迟不被回访"
                             % cap_hours)

    def test_terminal_excluded_from_next_due(self):
        """终态源不能参与 next_due 计算。

        due_at 对终态源返回 TERMINAL_NEVER（135 年后）。它一旦混进调度器的
        next_due，日志就会出现「约 22713866 分钟后可访问」这种天文数字
        （实测 2026-09-30 的 overnight.log 就出现过），而这个值会被
        overnight.py 的轮间节流当成「下一个源到期时间」——排一次队等 135 年。
        """
        import time

        m = self._backoff()
        b = m.SourceBackoff()
        now = int(time.time())
        b.data["cq_kjt"] = [1, 1, now, 1, 1, 0, 1]    # robots 终态
        b.data["ok_src"] = [1, 1, now, 1, 1, 0, 0]    # 普通采干源

        self.assertEqual(b.due_at("cq_kjt", "daily"), m.SourceBackoff.TERMINAL_NEVER)

        paused = ["cq_kjt", "ok_src"]
        due_times = [b.due_at(s, "daily") for s in paused
                     if not b.is_terminal_blocked(s)]
        due_times = [t for t in due_times if t]
        self.assertTrue(due_times, "至少应有一个非终态源算出到期时间")
        next_due = int(min(due_times))
        self.assertLess(next_due, time.time() + 24 * 3600,
                        "next_due 不该是天文数字，实际 {}".format(next_due))

    def test_all_sources_paused_round_advances_run_counter(self):
        """「全部源都在退避中」那轮也必须推进 runs 计数器。

        回归 2026-09-30：早退分支在 `backoff.next_run()` 之前就 return，
        于是 `runs` 永远停住，而各源的 `F_LAST_RUN` 停在最后一次真采集的轮次
        上 —— 轮次退避判据 `(runs - F_LAST_RUN) < sleep_for` 于是**永远成立**，
        回访闸把每个源永久挡住，除非时钟闸（8 小时）先放行。

        实测症状：13:23 那轮日志「跳过 34 个源」，而 mca_gov/mct_gov 的时钟
        早已到期（13.4 小时 > 8 小时门槛）却仍被挡；`runs` 停在 13 不再前进，
        watchdog 只能干等 pacing_wait 报的 187 分钟。
        """
        import time

        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("a", produced=False)
        runs_before = b.runs

        # 模拟：又过了一轮，但全被闸挡住 → 早退
        b.next_run()
        self.assertGreater(b.runs, runs_before,
                           "轮次计数器必须前进，否则轮次退避永久生效")
        # 早退推进后，该源的墙钟闸到期就该放行（不再被轮次闸永久压住）
        row = b.data["a"]
        row[b.F_LAST_EPOCH] = int(time.time()) - 24 * 3600   # 24 小时前
        row[b.F_DRY_STREAK] = m.SourceBackoff.DRY_STREAK_LIMIT
        b.next_run()
        self.assertFalse(b.is_sleeping_for("a", "daily"),
                         "时钟已到期 24 小时却仍被挡 —— runs 没推进导致的假性永久退避")

    def test_next_due_is_zero_when_some_source_is_runnable(self):
        """有源现在就能采时，next_due 必须是 0（别让调度器等）。

        回归 2026-09-30：next_due 只取「被闸挡住」那些源的最早到期时间。
        实测 mca_gov/mct_gov 时钟早已到期（5.5~5.7 小时）且 streak 未达退避
        门槛、**现在就能采**，而 nea_gov 要等 2.1 小时 —— 只算被挡源的话
        next_due=2.1 小时，让能采的源白等两小时。

        next_due 的语义是「**下一次值得醒来的时刻**」：
          * 有源可采 → 0（别等，接着采）
          * 全被挡住 → 被挡源里最早到期的那个
        """
        import time

        m = self._backoff()
        b = m.SourceBackoff()
        now = int(time.time())
        floor = int(m.CADENCE_SECONDS["daily"])

        # runnable：时钟早到期，streak 未达退避门槛
        b.data["runnable_src"] = [0, 1, now - floor - 600, 1, 0, now - floor - 600, 0]
        # paused：还要等 2 小时
        b.data["paused_src"] = [1, 1, now - 60, 1, 1, 0, 0]

        self.assertFalse(b.is_sleeping_for("runnable_src", "daily"),
                         "时钟早到期且 streak 未达门槛，应该能采")
        self.assertTrue(b.is_sleeping_for("paused_src", "daily"),
                        "刚访问过，应该被挡")

        sources = ["runnable_src", "paused_src"]
        sources_paused = ["paused_src"]
        runnable = [s for s in sources if not b.is_sleeping_for(s, "daily")]
        self.assertEqual(runnable, ["runnable_src"])

        # 复现 run.py 的算法
        if runnable:
            next_due = 0
        else:
            due_times = [b.due_at(s, "daily") for s in sources_paused]
            due_times = [t for t in due_times if t]
            next_due = int(min(due_times)) if due_times else 0
        self.assertEqual(next_due, 0,
                         "有源可采时 next_due 必须为 0，否则白等 %.1f 小时"
                         % ((min(b.due_at(s, "daily") for s in sources_paused)
                             - time.time()) / 3600))

    def test_prune_keeps_sources_paused_by_the_backoff_gate(self):
        """prune 必须收到**未过滤**的源全集，否则回访闸等于没装。

        回归 2026-09-30：调用方传的是被回访闸过滤后的 sources，于是形成死循环
        ——

            源被回访闸挡住 -> 不在 sources 里 -> 记录被 prune 掉
            -> 下轮 is_sleeping 查不到、判定「没访问过」-> 放行 -> 记录再被删

        实测退避表跑了几十轮只剩 2 条，而 82% 的时间花在「已采干」源上反复
        确认「还是那些」（1693 秒 / 30 个源 / 单轮 2058 秒）。

        prune 本身没坏，坏的是传参。这条测试锁住调用方要传的语义。
        """
        m = self._backoff()
        path = m.SourceBackoff.path()
        backup = path.read_text(encoding="utf-8") if path.exists() else None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            b = m.SourceBackoff()
            b.record("drained_a", produced=False)
            b.record("drained_b", produced=False)
            b.record("fresh_c", produced=True)
            all_ids = {"drained_a", "drained_b", "fresh_c"}

            # 这一轮只有 fresh_c 真跑了；drained_* 被回访闸挡住
            paused = {"drained_a", "drained_b"}
            b.prune(all_ids)                 # 正确：传全集
            self.assertEqual(set(b.data), all_ids,
                             "被回访闸挡住的源必须保留记录")

            # 连续零产出达到阈值后，闸应该开始挡它
            for _ in range(m.SourceBackoff.DRY_STREAK_LIMIT):
                b.next_run()
                b.record("drained_a", produced=False)
            b.prune(all_ids)
            self.assertTrue(b.is_sleeping_for("drained_a"),
                            "连续零产出后应被回访闸挡住")
            b.prune(all_ids)
            self.assertTrue(b.is_sleeping_for("drained_a"),
                            "再次 prune 后仍应被挡住（记录没被擦掉）")

            # 注册表里真删掉的源仍会被清掉
            b.prune({"fresh_c"})
            self.assertEqual(set(b.data), {"fresh_c"},
                             "注册表里已移除的源应被清掉")
        finally:
            if backup is not None:
                path.write_text(backup, encoding="utf-8")
            elif path.exists():
                path.unlink()

    def test_allowlist_denial_is_terminal_and_never_visited_again(self):
        """白名单拒绝 = 终态，永不再访问。

        回归 2026-09-30：一轮里 5 个重庆源全是 robots 拒绝，cq_kjt(144s) +
        cq_ny(168s) + cq_gov(76s) + cq_fgw(77s) 合计约 7 分钟，产出 0 条。

        注：那次是按「robots=终态」处理的。2026-09-30 晚些时候规则已改 ——
        robots 改为静态优先 + 渲染兜底（重庆要恢复数据），终态标记只由
        **本地白名单**拒绝触发。白名单绕它等于越权采集，所以仍是终态。

        这是效率问题：终态源不再排进队列，不再每轮烧时间。
        """
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()

        blocked = "cq_kjt"   # 白名单拒绝的源
        b.record(blocked, produced=False, terminal=True)
        assert b.is_terminal_blocked(blocked)
        assert b.is_sleeping_for(blocked), "robots 终态必须永远跳过"
        assert b.due_at(blocked) == m.SourceBackoff.TERMINAL_NEVER, \
            "due_at 应报「永不再访」，否则调度器会反复空转"

        # 跑很多轮也不能自己恢复
        for _ in range(20):
            b.next_run()
            assert b.is_sleeping_for(blocked), "robots 终态不得因轮次推进而恢复"

        # 对照：普通零产出源仍按 dry streak 退避，不是终态
        other = "cq_ny"
        b.record(other, produced=False)
        assert not b.is_terminal_blocked(other), "普通零产出不是终态"
        assert b.due_at(other) != m.SourceBackoff.TERMINAL_NEVER

    def test_terminal_flag_survives_row_length_upgrade(self):
        """退避表行从 6 字段升到 7 字段时，老数据不能被误判成终态。"""
        m = self._backoff()
        b = m.SourceBackoff({"old_src": [3, 1, 1234567890, 5, 4, 1700000000]})
        assert not b.is_terminal_blocked("old_src"), \
            "老行（6 字段，无终态标记）不该被判成终态"
        assert b.data["old_src"][m.SourceBackoff.F_TERMINAL] == 0

    def test_cadence_floors_are_ordered(self):
        m = self._backoff()
        c = m.CADENCE_SECONDS
        self.assertLess(c["daily"], c["weekly"])
        self.assertLess(c["weekly"], c["monthly"])
        self.assertLess(c["monthly"], c["never"])

    def test_dry_streak_still_lengthens_backoff(self):
        import time

        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("a", produced=False)
        b.next_run()
        b.record("a", produced=False)          # streak=2，达到退避门槛
        # 抹掉时间戳，只测「采干退避」那道闸的加长效果。
        #
        # 2026-09-30：判据从「轮数」改成「轮数折算成墙钟」后（见
        # is_sleeping_for 的注释 —— 轮次闸会压住时钟闸，把采干源永久锁死），
        # 光把 F_LAST_EPOCH 抹成 0 不够了：now - 0 是几十年前，墙钟闸必然放行。
        # 所以改成把时间戳设成「刚好在最小回访间隔之外」，让时钟闸放行、
        # 只有采干闸可能挡。
        b.data["a"][m.SourceBackoff.F_LAST_EPOCH] = int(
            time.time()) - int(m.CADENCE_SECONDS["daily"]) - 10
        self.assertTrue(b.is_sleeping_for("a", "daily"),
                        "连续零产出达到门槛后应被采干闸挡住")
        # 走满首个退避档的墙钟量就该放行（档位从 SLEEP_ROUNDS 读，不写死）
        first = m.SourceBackoff.backoff_interval(
            m.SourceBackoff.DRY_STREAK_LIMIT)
        b.data["a"][m.SourceBackoff.F_LAST_EPOCH] = int(
            time.time()) - int(m.CADENCE_SECONDS["daily"] * first) - 10
        self.assertFalse(b.is_sleeping_for("a", "daily"),
                         "退避期到了就该回访")

    def test_unvisited_source_is_never_sleeping(self):
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        self.assertFalse(b.is_sleeping_for("brand_new", "daily"))


    def test_dry_streak_sleep_not_starved(self):
        """采干退避不能拉长到把源饿死。

        一度设成 (4, 8, 12, 20)：配合 8 小时回访间隔 = **32 小时**才回访一次，
        采干源直接饿死。现在 (3, 6, 9, 12) = 24 小时起步，每天一次。
        """
        m = self._backoff()
        c = m.SourceBackoff
        daily_h = m.CADENCE_SECONDS["daily"] / 3600.0
        head_hours = c.backoff_interval(c.DRY_STREAK_LIMIT) / 3600.0
        self.assertLessEqual(
            head_hours, 48,
            "首个退避档 %.0f 小时太长了（采干源会被饿死）" % head_hours)
        self.assertGreaterEqual(head_hours, 12,
                                "太短则退避没意义，仍会反复空转")


class CircuitBreakerTests(unittest.TestCase):
    """熔断：自迭代第二个真能改变结果的动作（回访闸只是「等一等」，
    熔断是「别再来了」）。"""

    def _backoff(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_lhrun2", Path(__file__).resolve().parents[1] / "lighthouse" / "run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_never_produced_after_enough_visits_is_circuited(self):
        """访问够多次、连续零产出、且**已经过了熔断窗口** → 熔断。

        ★ 2026-10-05 语义变更：`F_LAST_PRODUCED == 0` 现在表示
        「**计数器不知道**」，而不是「这个源从未出货」。

        旧实现直接 `return True`，代价是 143 个源里 **65 个被永久熔断**，
        包括库内已有几百条、且实测还有几百条没采的源（js_gxt 484 条/未采 316、
        cq_fgw 432/203、moa_gov 290/205……）。根因是该字段只在
        `record(produced=True)` 时写入，早期回填的数据不经过它。

        新语义：没有出货证据时退回「上次被访问」起算 3 天。所以这里要显式把
        `F_LAST_EPOCH` 推到 4 天前 —— 不推的话刚访问完的源当然不该熔断，
        那正是这次修复的目的。
        """
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        for _ in range(m.SourceBackoff.CIRCUIT_VISITS + 2):
            b.record("dead", False)
        row = b.data["dead"]
        self.assertFalse(b.is_circuited("dead"),
                         "刚访问完的零产出源不该立刻熔断")
        row[m.SourceBackoff.F_LAST_EPOCH] = int(time.time()) - 4 * 24 * 3600
        self.assertTrue(b.is_circuited("dead"), "4 天零产出仍应熔断")


    def test_seasonal_source_is_not_circuited(self):
        """政府站有淡旺季。曾经出过货、只是这阵子没发文，不能熔断。"""
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("seasonal", True)
        for _ in range(m.SourceBackoff.CIRCUIT_VISITS + 2):
            b.record("seasonal", False)
        self.assertFalse(b.is_circuited("seasonal"))

    def test_long_idle_source_is_circuited(self):
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("slow", True)
        for _ in range(m.SourceBackoff.CIRCUIT_ZERO_STREAK):
            b.record("slow", False)
        row = b.data["slow"]
        row[m.SourceBackoff.F_LAST_PRODUCED] = int(time.time()) - 4 * 24 * 3600
        self.assertTrue(b.is_circuited("slow"))
        row[m.SourceBackoff.F_LAST_PRODUCED] = int(time.time()) - 3600
        self.assertFalse(b.is_circuited("slow"), "只 1 天没出货，未到门槛")

    def test_too_few_visits_is_not_circuited(self):
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("fresh", False)
        self.assertFalse(b.is_circuited("fresh"), "样本不足不能熔断")

    def test_producing_clears_the_zero_counter(self):
        """出过货之后：零产出计数清零、且立刻脱离熔断。

        先把源推入「3 天零产出」的熔断态，再来一次产出——必须能自愈。
        时间同样要显式推前，见 test_never_produced_after_enough_visits_is_circuited
        里的说明（语义变更：没有出货证据时从「上次被访问」起算 3 天）。
        """
        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        for _ in range(m.SourceBackoff.CIRCUIT_ZERO_STREAK + 2):
            b.record("flaky", False)
        b.data["flaky"][m.SourceBackoff.F_LAST_EPOCH] = int(time.time()) - 4 * 24 * 3600
        self.assertTrue(b.is_circuited("flaky"), "前置条件：应先处于熔断态")
        b.record("flaky", True)          # 出了一次货
        self.assertFalse(b.is_circuited("flaky"))
        row = b.data["flaky"]
        self.assertEqual(row[m.SourceBackoff.F_ZERO_VISITS], 0)
        self.assertGreater(row[m.SourceBackoff.F_LAST_PRODUCED], 0)


    def test_state_roundtrips_through_save_and_load(self):
        """字段布局换了位置（加了 visits / zero_visits / last_produced），
        旧文件必须能被读成同一语义，否则历史退避表一读就错位。"""
        import json
        import tempfile
        from pathlib import Path as P

        m = self._backoff()
        b = m.SourceBackoff()
        b.next_run()
        b.record("x", True)
        for _ in range(3):
            b.record("x", False)
        with tempfile.TemporaryDirectory() as tmp:
            saved = P(tmp) / "source_backoff.json"
            saved.write_text(json.dumps(b.data), encoding="utf-8")
            original_path = m.SourceBackoff.path
            try:
                m.SourceBackoff.path = classmethod(lambda cls: saved)
                loaded = m.SourceBackoff.load()
            finally:
                m.SourceBackoff.path = original_path
        row = loaded.data["x"]
        self.assertEqual(row[m.SourceBackoff.F_VISITS], 4)
        self.assertEqual(row[m.SourceBackoff.F_ZERO_VISITS], 3)
        self.assertEqual(row[m.SourceBackoff.F_DRY_STREAK], 3)
        self.assertGreater(row[m.SourceBackoff.F_LAST_PRODUCED], 0)
        # 旧格式（只有 2 个字段）也要能读，不能崩
        legacy = m.SourceBackoff({"old": [3, 7]})
        self.assertEqual(len(legacy.data["old"]), m.SourceBackoff.FIELDS)
        self.assertEqual(legacy.runs, 7)


class WatchdogRestartTests(unittest.TestCase):
    """watchdog 必须区分「调度器正常完成」和「调度器崩溃」。

    回归 2026-09-30 20:52~20:55：把退避阶梯拉长、退避闸把 34 个源
    全挡住之后，调度器每轮「无事可做」秒退（round_log 里 exit=0）。watchdog
    只看「子进程还在不在」，把这当成崩溃，20 秒一轮连打 199 次，撞满
    max_restarts=200 后直接弃守退出 —— 那一夜采集停摆 10 小时，日志里只有
    一行「重启次数用尽」，看不出真正原因。

    这类「静默停摆」比崩溃更难发现：崩溃会留 traceback，正常退出不会。
    """

    def _wd(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_wd",
            str(Path(__file__).resolve().parents[1] / "lighthouse" / "watchdog.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["_wd"] = module
        spec.loader.exec_module(module)
        return module

    def _status(self, rows):
        import tempfile

        f = Path(tempfile.gettempdir()) / "wd_test_status.json"
        f.write_text(json.dumps({"round_log": rows}), encoding="utf-8")
        self.addCleanup(lambda: f.exists() and f.unlink())
        return f

    def test_clean_exit_is_not_counted_as_restart(self):
        wd = self._wd()
        f = self._status([{"round": 1, "exit": 0}])
        self.assertTrue(wd.scheduler_exited_cleanly(f),
                        "exit=0 是正常完成（无源可采/轮数跑完），不该算重启")

    def test_nonzero_exit_is_a_crash(self):
        wd = self._wd()
        self.assertFalse(wd.scheduler_exited_cleanly(
            self._status([{"round": 1, "exit": 1}])), "exit 非 0 算崩溃")

    def test_error_field_counts_as_crash(self):
        wd = self._wd()
        self.assertFalse(wd.scheduler_exited_cleanly(
            self._status([{"round": 1, "exit": 0, "error": "boom"}])),
            "round_log 带 error 说明这轮出了问题")

    def test_missing_round_log_is_not_assumed_clean(self):
        """没有 round_log 时不能假设「正常」——宁可多重启一次。

        首轮刚起来、还没写 round_log 就退出的情况，假设正常会让 watchdog
        静默空转（不重启也不推进），那正是最难查的一种停摆。
        """
        wd = self._wd()
        self.assertFalse(wd.scheduler_exited_cleanly(self._status([])))

    def test_unreadable_status_is_not_assumed_clean(self):
        wd = self._wd()
        self.addCleanup(lambda: Path(wd.__file__).exists() and None)
        self.assertFalse(wd.scheduler_exited_cleanly(
            Path(__file__)))          # 一定不是合法 json

    def test_restart_budget_survives_a_night(self):
        """20 秒一轮的话，200 次只够撑约 1 小时。"""
        wd = self._wd()
        src = Path(wd.__file__).read_text(encoding="utf-8")
        self.assertIn('"--max-restarts", type=int, default=2000', src,
                      "重启上限应至少 2000：200 次 × 20 秒 ≈ 1 小时就弃守")

    def test_liveness_check_counts_pythonw_too(self):
        """进程存活判定必须把 pythonw.exe 数进去。

        无人值守链（watchdog / overnight / run.py）全由计划任务用 pythonw 拉起，
        按 ``name='python.exe'`` 查进程一个都看不见 —— 采集明明在跑，汇总却报
        「watchdog 进程不在」，``--heal`` 还会再拉一个 watchdog 出来抢锁。
        """
        root = Path(__file__).resolve().parents[1]
        src = (root / "scripts" / "status_summary.py").read_text(encoding="utf-8")
        self.assertIn("name like 'python%'", src,
                      "必须匹配 python*.exe（含 pythonw.exe）")
        self.assertNotIn("name='python.exe'", src,
                         "只查 python.exe 会漏掉计划任务拉起的 pythonw 链")

    def test_heal_launches_watchdog_without_a_console_window(self):
        """--heal 拉起 watchdog 必须是后台无窗口的。

        踩过的坑：窗口标志写成了 0x00000010 = CREATE_NEW_CONSOLE，**会弹出一个
        黑色控制台窗口**（通宵采集时它会一直杵在桌面上）。
        正确的是 DETACHED_PROCESS(0x08) | CREATE_NO_WINDOW(0x08000000)。
        """
        root = Path(__file__).resolve().parents[1]
        src = (root / "scripts" / "status_summary.py").read_text(encoding="utf-8")
        self.assertIn("0x00000008", src, "应使用 DETACHED_PROCESS")
        self.assertIn("0x08000000", src, "应使用 CREATE_NO_WINDOW")
        # 反例：CREATE_NEW_CONSOLE 会开窗口
        self.assertNotIn("flags = 0x00000010", src,
                         "CREATE_NEW_CONSOLE 会弹出控制台窗口")

    def test_watchdog_waits_for_next_due_instead_of_polling(self):
        """调度器「无事可做」正常退出时，watchdog 必须等到期再拉，不能固定轮询。

        固定睡 60 秒的话，一夜就是 1440 次「拉起→秒退→睡 60 秒」，什么也没干，
        还把日志刷满 —— 而日志里每一条都看着正常，正是最难查的那种浪费。
        """
        wd = self._wd()
        self.assertTrue(hasattr(wd, "seconds_until_next_due"),
                        "watchdog 应有 seconds_until_next_due()")
        src = Path(wd.__file__).read_text(encoding="utf-8")
        self.assertIn("scheduler_exited_cleanly()", src,
                      "正常完成要走 scheduler_exited_cleanly 分支，不能当崩溃")
        self.assertIn("seconds_until_next_due()", src,
                      "正常完成后应按 next_due 等待")

class DeadUrlTests(unittest.TestCase):
    """404/403 这类「不死」的 URL 必须被认出来，且不能误判正常页面。

    回归 2026-09-30（mct_gov）：第 1 页 URL 域名对（zwgk.mct.gov.cn），翻到
    第 2~6 页时站点返回**另一个旧站点**的链接（www.mct.gov.cn 的
    /ghjh/2020..、/tjxx/2025.. 等），全部 404。那 14+ 条 404 逐条走
    「静态 30s → 渲染 60s → markdown 60s」三级升级，一个源烧光 300 秒预算
    却产出 0 条。

    两个坑：
      * attempts 里**没有结构化 status 字段**，死状态码只出现在第一级
        static 的 reason 文本里（'error:RuntimeError:HTTP 404 for https://...'）。
      * 404 的 URL 在 render/markdown 级的 reason 也写着 'too_short'，跟一个
        正常但简短的页面**完全分不出来** —— 所以只能看第一级。
    """

    def _run(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_r2", Path(__file__).resolve().parents[1] / "lighthouse" / "run.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["_r2"] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _detail(attempts, ok=False):
        from types import SimpleNamespace

        return SimpleNamespace(attempts=attempts, ok=ok, dropped_reason="")

    def test_recognises_404_from_static_reason_text(self):
        m = self._run()
        d = self._detail([
            {"level": "static",
             "reason": "error:RuntimeError:HTTP 404 for "
                       "https://www.mct.gov.cn/ghjh/x.html"},
            {"level": "render", "reason": "too_short", "chars": "49"},
            {"level": "markdown", "reason": "too_short", "chars": "59"},
        ])
        self.assertEqual(m.dead_status(d), 404)
        self.assertTrue(m._is_dead_url(d))

    def test_short_but_alive_page_is_not_dead(self):
        """短正文不是死的 —— 这个误判会砍掉真实公告。"""
        m = self._run()
        d = self._detail([
            {"level": "static", "reason": "too_short", "chars": "265"},
            {"level": "markdown", "reason": "too_short", "chars": "120"},
        ])
        self.assertIsNone(m.dead_status(d),
                          "短正文被误判成 dead，真实公告会被整批丢掉")
        self.assertFalse(m._is_dead_url(d))

    def test_403_410_401_451_all_count(self):
        m = self._run()
        for code in (401, 403, 404, 410, 451):
            d = self._detail([
                {"level": "static", "reason": "error:HTTP %d for https://x" % code}])
            self.assertEqual(m.dead_status(d), code,
                             "HTTP %d 应被认成死链" % code)

    def test_dead_after_static_passed_is_not_counted(self):
        """static 过了就说明 URL 活着，后面的记录不算死链。"""
        m = self._run()
        d = self._detail([
            {"level": "static", "reason": "pass", "chars": "100"},
            {"level": "render", "reason": "error:HTTP 404"},
        ])
        self.assertIsNone(m.dead_status(d))

    def test_empty_attempts_is_not_dead(self):
        m = self._run()
        self.assertIsNone(m.dead_status(self._detail([])))
        self.assertIsNone(m.dead_status(self._detail(None)))


if __name__ == "__main__":
    unittest.main()
