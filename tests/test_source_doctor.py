"""源健康诊断的测试。

重点是三条容易悄悄坏掉的：

1. **一个源诊断出错不能带倒整份报告**（Agent Reach doctor.py 的核心）。
   145 个源里一个抽风，剩下 144 个的结论必须照样给出来。
2. **配置「看起来不全」不等于故障**。第一版把「没 selector」判成要修，
   结果 11 个实际采了 26~484 条的源被误报——把常态当成了病。
3. **集体停摆要看得见**。逐源全绿但一个都采不了，是 2026-10-01 整夜丢数据
   的那个形态，逐源诊断天生看不见。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.source_doctor import (BAD, ERROR, OK, WARN, check_all,  # noqa: E402
                                   diagnose)

NOW = 1_800_000_000.0          # 固定时间戳，别用 now()（否则测试随时间漂）
HOUR = 3600


def _src(**kw):
    base = {"id": "t_gov", "enabled": True, "cadence_tier": "daily",
            "list_selector": "#list", "detail_pattern": "/d/\d+"}
    base.update(kw)
    return base


def _backoff(last_epoch, dry=0):
    return {"t_gov": [dry, 1, last_epoch, 2, dry, 0, 0]}


# --------------------------------------------------------------- 正常路径


def _last(status, notes=(), records=0, dropped=0):
    """构造 ``last`` 参数：{source_id: {...}}。"""
    return {"t_gov": {"status": status, "notes": list(notes),
                      "records": records, "dropped": dropped,
                      "seconds": 12.0, "at": "20261002T120000"}}


def _yields(total=300, recent=5, last_ingest="2026-10-01T00:00:00+00:00"):
    """构造 ``yields`` 参数。"""
    return {"t_gov": {"total": total, "recent_30d": recent,
                      "last_ingest": last_ingest}}


def test_healthy_source_is_ok_and_collectable():
    d = diagnose(_src(), _backoff(NOW - 10 * HOUR),
                 _last("ok", records=12), _yields(), NOW)
    assert d.status == OK, d.reason
    assert d.tier == 0
    assert d.evidence["collectable"] is True


def test_disabled_source_is_not_a_fault():
    d = diagnose(_src(enabled=False, status="blocked"), {}, {}, {}, NOW)
    assert d.status == OK, "未启用不是故障"
    assert d.tier == 0
    assert "未启用" in d.reason


# --------------------------------------------------------------- 节流 vs 故障


def test_interval_not_reached_is_normal_throttling():
    """回访间隔未到 = 正常节流。绝不是故障——那是每天一次的设计。"""
    d = diagnose(_src(), _backoff(NOW - 2 * HOUR), {}, {}, NOW)
    assert d.status == WARN
    assert d.tier == 1
    assert d.evidence["collectable"] is False
    assert "不用管" in d.hint


def test_weekly_source_uses_its_own_7day_floor():
    """weekly 源的下限是 7 天。用 daily 的 8 小时算会小 21 倍。

    两天前访问过：weekly 源还没到点（warn），daily 源早该到点了。
    两个源的历史数据一样，只有 cadence_tier 不同——这样测出来的差异
    只可能来自下限本身。
    """
    row = _backoff(NOW - 2 * 24 * HOUR)
    hist_last = _last("ok", records=5)
    hist = _yields(total=100, recent=1)

    weekly = diagnose(_src(cadence_tier="weekly"), row, hist_last, hist, NOW)
    daily = diagnose(_src(cadence_tier="daily"), row, hist_last, hist, NOW)

    assert weekly.status == WARN, (
        "7 天门槛下，2 天前访问过仍不该到点，得到 %s" % weekly.status)
    assert "小时" in weekly.reason
    assert daily.status == OK, (
        "8 小时门槛下早该到点了，得到 %s" % daily.status)
    assert daily.evidence["collectable"] is True


def test_no_new_urls_is_healthy_not_a_fault():
    """「列表里全是已知 URL」= 正常增量空转。判成故障会天天误报。"""
    d = diagnose(_src(), _backoff(NOW - 10 * HOUR),
                 _last("no_new", ["all 29 list urls already known"]),
                 _yields(), NOW)
    assert d.status == OK, "no_new 是健康的：%s" % d.reason


def test_no_new_but_stale_for_a_month_escalates():
    """no_new 超过 30 天没更新就该提出来：可能站改版了。"""
    d = diagnose(_src(), _backoff(NOW - 10 * HOUR),
                 _last("no_new", ["early stop"]),
                 _yields(total=300, recent=0, last_ingest="2026-06-01T00:00:00+00:00"),
                 NOW)
    assert d.status == WARN, "长期 no_new 要提出来"
    assert "probe_sources" in d.hint, "要给出可执行的排查命令"


# --------------------------------------------------------------- 真故障


def test_selector_missing_but_producing_is_not_a_fault():
    """★ 配置没 selector 但实际采到了很多条 = 正常。

    第一版的 bug：34 个 enabled 源里 11 个没有 selector 字段（采集走
    「从列表页直接提链接」），被判成「要修」，而它们实际采了 26~484 条。
    把常态当成了病。判据必须是实测，不是配置字段猜测。
    """
    src = _src(list_selector=None, detail_pattern=None)
    d = diagnose(src, _backoff(NOW - 10 * HOUR),
                 _last("no_new", ["early stop"]),
                 _yields(total=327, recent=12), NOW)
    assert d.status != BAD, "没 selector 但采到 327 条，不该报「要修」"
    assert d.tier < 2


def test_enabled_due_but_zero_yield_and_no_selector_is_actionable():
    """开了、到期了、库里 0 条、又没有 selector —— 这才是真该修。"""
    src = _src(list_selector=None, detail_pattern=None)
    d = diagnose(src, _backoff(NOW - 10 * HOUR),
                 _last("no_new"), _yields(total=0, recent=0, last_ingest=None), NOW)
    assert d.status == BAD
    assert d.tier == 2
    assert d.hint, "要修的源必须给出怎么修"


def test_no_links_needs_action():
    d = diagnose(_src(), _backoff(NOW - 10 * HOUR),
                 _last("no_links", ["no links"]),
                 _yields(total=5, recent=0, last_ingest="2026-09-01T00:00:00+00:00"),
                 NOW)
    assert d.status == BAD
    assert "repair.py" in d.hint


def test_unclassifiable_notes_become_error_not_ok():
    """分类器认不出来的必须显式标 ERROR，不能默默归进「正常」。

    这条最要紧：2026-10-02 之前 unknown 桶里堆了 17 个源，看起来一切正常。
    """
    d = diagnose(_src(), _backoff(NOW - 10 * HOUR),
                 _last("unknown", ["某个没见过的说法"]),
                 _yields(total=5, recent=0, last_ingest=None), NOW)
    assert d.status == ERROR, "认不出来的必须报错而不是归为正常"
    assert d.tier == 3
    assert "_source_classify" in d.hint, "要指明去哪儿补判据"


# --------------------------------------------------------------- 单源失败不带走全盘


def test_one_broken_source_does_not_sink_the_report(monkeypatch):
    """★ 一个源诊断崩溃，其余源的结论照样要给出来。

    这是 Agent Reach doctor.py 里那句「A single misbehaving channel must
    never take the whole report down」的直译。145 个源里一个抽风，
    剩下 144 个的诊断就是零价值了。
    """
    import scripts.source_doctor as sd

    real = sd.diagnose

    def flaky(source, *a, **kw):
        if str(source.get("id")) == "bad_gov":
            raise RuntimeError("模拟诊断器崩溃")
        return real(source, *a, **kw)

    monkeypatch.setattr(sd, "diagnose", flaky)
    monkeypatch.setattr(sd, "load_sources",
                        lambda: [_src(id="good_gov"), _src(id="bad_gov")])
    monkeypatch.setattr(sd, "load_backoff", lambda: {})
    monkeypatch.setattr(sd, "load_last_results", lambda: {})
    monkeypatch.setattr(sd, "load_yield_stats", lambda: {})

    report = sd.check_all()

    ids = {r["source_id"]: r for r in report["results"]}
    assert len(ids) == 2, "两个源的结论都要在"
    assert ids["good_gov"]["status"] != ERROR
    assert ids["bad_gov"]["status"] == ERROR
    assert ids["bad_gov"]["tier"] == 3, "诊断器故障要归 tier3（最显眼）"
    assert "doctor 的 bug" in ids["bad_gov"]["hint"], (
        "要说明这是 doctor 的问题，不是源的故障")


# --------------------------------------------------------------- 集体停摆


def test_collective_verdict_when_nothing_is_collectable(monkeypatch):
    """★ 逐源全绿 + 可采源为 0 = 停摆。

    2026-10-01 整夜的形态：34 个源逐个都「回访间隔未到」，tier2=0、
    tier3=0，全绿——但一个都采不了，一夜零入库。逐源诊断天生看不见这个病，
    所以必须有集体判定。
    """
    import scripts.source_doctor as sd

    monkeypatch.setattr(sd, "load_sources", lambda: [_src(id="a_gov")])
    monkeypatch.setattr(
        sd, "load_backoff",
        lambda: {"a_gov": [2, 1, NOW - HOUR, 2, 2, 0, 0]})
    monkeypatch.setattr(sd, "load_last_results", lambda: {})
    monkeypatch.setattr(
        sd, "load_yield_stats",
        lambda: {"a_gov": {"total": 100, "recent_30d": 0,
                           "last_ingest": "2026-09-30T00:00:00+00:00"}})

    report = sd.check_all()

    assert report["by_tier"].get(2, 0) == 0, "逐源看确实没有要修的"
    assert report["by_tier"].get(3, 0) == 0, "逐源看确实没有报错的"
    col = report["collective"]
    assert col.get("verdict"), "但集体判定必须喊出来：%s" % col
    assert col["collectable_now"] == 0
    assert "BACKOFF_LADDER" in col["hint"], "要指出退避阶梯这个常见根因"


def test_collective_quiet_when_some_source_is_collectable(monkeypatch):
    """有源能采时不该喊「全体停摆」——否则变噪音。

    时间戳用**真实当前时间**：``check_all()`` 内部取的是
    ``datetime.now()``，测试自己造一个固定 ``NOW`` 就会和它差出几千万秒，
    判出来的wait 全是天文数字——那是测试的错，不是代码的错。
    """
    import time

    import scripts.source_doctor as sd

    now = time.time()
    monkeypatch.setattr(sd, "load_sources",
                        lambda: [_src(id="a_gov"), _src(id="b_gov")])
    monkeypatch.setattr(
        sd, "load_backoff",
        lambda: {"a_gov": [2, 1, now - HOUR, 2, 2, 0, 0],      # 被挡
                 "b_gov": [0, 1, now - 10 * HOUR, 1, 0, 0, 0]})  # 可采
    monkeypatch.setattr(
        sd, "load_last_results",
        lambda: {"b_gov": {"status": "ok", "notes": [], "records": 9,
                           "dropped": 0, "seconds": 10.0, "at": "x"}})
    monkeypatch.setattr(
        sd, "load_yield_stats",
        lambda: {"b_gov": {"total": 50, "recent_30d": 2,
                           "last_ingest": "2026-10-01T00:00:00+00:00"}})

    report = sd.check_all()
    assert "verdict" not in report["collective"], (
        "有源可采时不该报停摆：%s" % report["collective"])
    assert report["collective"]["collectable_now"] == 1