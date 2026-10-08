"""self_evolve 协调层测试：事件流 + 原则蒸馏 + 效用闭环。

测试策略：
1. 用临时 events.jsonl 隔离，不污染生产数据
2. 模拟事件序列，验证蒸馏和 apply_principles
3. 验证效用衰减和退休逻辑
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lighthouse import event_bus, principles


@pytest.fixture
def temp_events_file(tmp_path, monkeypatch):
    """把 events.jsonl 和 principles.json 重定向到临时目录。"""
    temp_events = tmp_path / "events.jsonl"
    temp_principles = tmp_path / "principles.json"
    monkeypatch.setattr(event_bus, "EVENTS_FILE", temp_events)
    monkeypatch.setattr(principles, "PRINCIPLES_FILE", temp_principles)
    # 重置序号
    event_bus._seq = 0
    return temp_events


class TestEventBus:
    """事件流基础功能。"""

    def test_emit_and_read(self, temp_events_file):
        """写一条事件，能读回来。"""
        eid = event_bus.emit(
            layer="collector", scope="source_id:src_test",
            failure_type="no_links", action="discover_fix",
            repair_result="candidate", source="repair",
            detail="测试事件")
        assert eid is not None
        events = event_bus.read_events()
        assert len(events) == 1
        assert events[0]["layer"] == "collector"
        assert events[0]["scope"] == "source_id:src_test"
        assert events[0]["failure_type"] == "no_links"

    def test_emit_multiple(self, temp_events_file):
        """写多条事件，序号递增。"""
        for i in range(5):
            event_bus.emit(
                layer="collector", scope=f"source_id:src_{i}",
                failure_type="no_links", action="discover_fix",
                repair_result="candidate", source="repair",
                detail=f"事件 {i}")
        events = event_bus.read_events()
        assert len(events) == 5
        # event_id 应该都不同
        ids = {e["event_id"] for e in events}
        assert len(ids) == 5

    def test_read_with_filter(self, temp_events_file):
        """按 source / layer / scope 过滤。"""
        event_bus.emit(layer="collector", scope="source_id:a",
                       failure_type="no_links", action="x",
                       repair_result="observed", source="repair")
        event_bus.emit(layer="summary", scope="metric:b",
                       failure_type="gap", action="y",
                       repair_result="kept", source="self_improve")
        event_bus.emit(layer="collector", scope="source_id:c",
                       failure_type="no_links", action="z",
                       repair_result="observed", source="repair")

        assert len(event_bus.read_events(source="repair")) == 2
        assert len(event_bus.read_events(source="self_improve")) == 1
        assert len(event_bus.read_events(layer="collector")) == 2
        assert len(event_bus.read_events(layer="summary")) == 1

    def test_stats(self, temp_events_file):
        """统计摘要。"""
        event_bus.emit(layer="collector", scope="s:a", failure_type="f1",
                       action="a1", repair_result="kept", source="repair")
        event_bus.emit(layer="summary", scope="m:b", failure_type="f2",
                       action="a2", repair_result="reverted", source="self_improve")
        s = event_bus.stats()
        assert s["total"] == 2
        assert s["by_source"]["repair"] == 1
        assert s["by_source"]["self_improve"] == 1
        assert s["by_result"]["kept"] == 1
        assert s["by_result"]["reverted"] == 1

    def test_best_effort_no_crash(self, temp_events_file):
        """写失败不抛异常，返回 None。"""
        # 把文件指向一个不存在的盘符（Windows）/ 只读路径
        with patch.object(event_bus, "EVENTS_FILE", Path("Z:/nonexist/events.jsonl")):
            eid = event_bus.emit(
                layer="x", scope="x", failure_type="x", action="x",
                repair_result="x", source="test")
            assert eid is None


class TestPrinciples:
    """原则蒸馏和效用闭环。"""

    def test_distill_basic(self, temp_events_file):
        """从事件蒸馏出原则。"""
        # 两条同类事件：no_links + discover_fix（真实动作：标记该源待人工修）
        event_bus.emit(layer="collector", scope="source_id:src_a",
                       failure_type="no_links", action="discover_fix",
                       repair_result="candidate", source="repair",
                       detail="修好选择器")
        event_bus.emit(layer="collector", scope="source_id:src_b",
                       failure_type="no_links", action="discover_fix",
                       repair_result="candidate", source="repair",
                       detail="修好选择器")

        plist = principles.distill()
        assert len(plist) >= 1
        # 找到 no_links 那条
        p = next(p for p in plist if p["failure_type"] == "no_links")
        assert p["best_action"] == "discover_fix"
        assert p["apply_count"] == 2

    def test_utility_score(self, temp_events_file):
        """kept+verified=true → +1.0, reverted → -0.5。"""
        # 一条成功（kept + verified=true）
        event_bus.emit(layer="collector", scope="source_id:src_a",
                       failure_type="no_links", action="discover_fix",
                       repair_result="kept", source="repair",
                       verified=True, utility=1.0)
        # 一条失败（reverted）
        event_bus.emit(layer="summary", scope="metric:median_content",
                       failure_type="gap", action="set_constant",
                       repair_result="reverted", source="self_improve",
                       utility=-0.5)

        plist = principles.distill()
        p1 = next(p for p in plist if p["failure_type"] == "no_links")
        assert p1["utility_score"] > 0  # 成功的正效用

        p2 = next(p for p in plist if p["failure_type"] == "gap")
        assert p2["utility_score"] < 0  # 失败的负效用

    def test_retirement(self, temp_events_file):
        """效用分低于阈值退休。"""
        # 写 5 条 reverted 事件，效用 = 5 × (-0.5) = -2.5 < -2.0
        for i in range(5):
            event_bus.emit(layer="summary", scope=f"metric:m{i}",
                           failure_type="bad_fix", action="useless_action",
                           repair_result="reverted", source="self_improve",
                           utility=-0.5)

        plist = principles.distill()
        p = next(p for p in plist if p["failure_type"] == "bad_fix")
        assert p["retired"] is True
        assert p["utility_score"] < principles.RETIRE_THRESHOLD

    def test_apply_principles(self, temp_events_file):
        """apply_principles 返回 Top-K。"""
        # 建立一条高效用原则
        for i in range(3):
            event_bus.emit(layer="collector", scope=f"source_id:src_{i}",
                           failure_type="no_links", action="discover_fix",
                           repair_result="kept", source="repair",
                           verified=True, utility=1.0)

        principles.distill()
        results = principles.apply_principles("source_id:xxx", "no_links")
        assert len(results) >= 1
        assert results[0]["best_action"] == "discover_fix"
        assert results[0]["utility_score"] > 0

    def test_apply_principles_excludes_retired(self, temp_events_file):
        """退休的原则不返回。"""
        for i in range(5):
            event_bus.emit(layer="x", scope=f"source_id:s{i}",
                           failure_type="bad", action="nope",
                           repair_result="reverted", source="test",
                           utility=-0.5)
        principles.distill()
        results = principles.apply_principles("source_id:xxx", "bad")
        assert len(results) == 0  # 退休了，不返回

    def test_decay(self, temp_events_file):
        """旧事件权重衰减。"""
        # 手写一条 30 天前的事件
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        old_event = {
            "event_id": f"{old_ts}#repair#source_id:old#1",
            "ts": old_ts,
            "layer": "collector",
            "scope": "source_id:old",
            "failure_type": "no_links",
            "action": "discover_fix",
            "repair_result": "kept",
            "verified": True,
            "utility": 1.0,
            "detail": "旧事件",
            "source": "repair",
        }
        temp_events_file.write_text(json.dumps(old_event, ensure_ascii=False) + "\n", encoding="utf-8")

        # 写一条今天的事件
        event_bus.emit(layer="collector", scope="source_id:new",
                       failure_type="no_links", action="discover_fix",
                       repair_result="kept", source="repair",
                       verified=True, utility=1.0)

        plist = principles.distill()
        p = next(p for p in plist if p["failure_type"] == "no_links")
        # 两条事件同一组，效用 = 1.0 (新) + 1.0 * 0.95^30 (旧) ≈ 1.0 + 0.21
        assert 1.0 < p["utility_score"] < 1.3


class TestSelfEvolveStep:
    """self_evolve.step() 协调层。"""

    def test_loads_self_improve_without_dataclass_crash(self, temp_events_file):
        """self_evolve 必须能真的把 self_improve 跑起来。

        回归 2026-09-30：step() 用 importlib 按路径加载 scripts/self_improve.py
        （scripts/ 没有 __init__.py，只能这么加载），但**没先注册进
        sys.modules**。self_improve.py 里有 @dataclass，Python 3.10 的
        dataclasses 处理字段时会做 sys.modules[cls.__module__].__dict__ 查找，
        模块没注册就抛

            AttributeError: 'NoneType' object has no attribute '__dict__'

        崩在 exec_module 里，被 step() 的 except 吞成 self_improve_error 字段 ——
        于是每轮日志都显示「跑过 self_improve」，实际一次都没跑成，只留下一行
        没人注意的 AttributeError。
        """
        from lighthouse import self_evolve

        result = self_evolve.step(round_no=999, findings=None,
                                  run_self_improve=True)

        assert "self_improve_error" not in result, \
            "self_improve 加载失败：{}".format(result.get("self_improve_error"))
        assert "_self_improve" in sys.modules, \
            "self_improve 应已注册进 sys.modules（dataclass 依赖它）"

    def test_repeated_findings_are_deduplicated(self, temp_events_file):
        """同一条 finding 连跑多轮，只该写一次事件。

        回归 2026-09-30：不去重时每轮把同样的 finding 原样重写一遍，实测半小时
        灌了 61 条 drained_sources、54 条 coverage，全是同内容复读。事件流的
        意义是记**状态变化**；复读会把 principles.distill() 和
        should_auto_apply() 的统计整个带偏。
        """
        from lighthouse import self_evolve
        from lighthouse.self_tune import Finding

        finding = Finding(stage="collect", kind="drained_sources",
                          sources=["src_a", "src_b"],
                          evidence={"count": 2},
                          action="退避", auto=True,
                          detail="2 个源报告 early stop（列表全是已知 URL）")

        # 第一轮是新状态，应该写（两个源各一条）
        first = self_evolve.step(round_no=1, findings=[finding],
                                 run_self_improve=False)
        assert first["events_new"] == 2, \
            "首轮应写入 2 条（两个源各一条）：{}".format(first)

        # 之后全是同内容复读，不该再写
        for i in range(2, 6):
            out = self_evolve.step(round_no=i, findings=[finding],
                                   run_self_improve=False)
            assert out["events_new"] == 0, \
                "第{}轮重复 finding 不该再写事件：{}".format(i, out)

        from lighthouse import event_bus

        mine = [e for e in event_bus.read_events()
                if e.get("failure_type") == "drained_sources"]
        assert len(mine) == 2, \
            "跑 5 轮应只有首轮的 2 条，实际 {}".format(len(mine))

    def test_changed_detail_is_still_recorded(self, temp_events_file):
        """detail 变了仍要写 —— 那说明问题变了，是真正的状态变化。"""
        from lighthouse import self_evolve
        from lighthouse.self_tune import Finding

        def f(detail):
            return Finding(stage="collect", kind="drained_sources",
                           sources=["src_a"], evidence={},
                           action="退避", auto=True, detail=detail)

        self_evolve.step(round_no=1, findings=[f("1 个源采干")],
                         run_self_improve=False)
        out = self_evolve.step(round_no=2, findings=[f("3 个源采干")],
                               run_self_improve=False)
        assert out["events_new"] == 1, "detail 变化应记一条新事件：{}".format(out)

    def test_step_with_no_findings(self, temp_events_file):
        """没有 findings 时 step 仍正常运行。"""
        from lighthouse import self_evolve
        result = self_evolve.step(round_no=1, findings=None, run_self_improve=False)
        assert result["round"] == 1
        assert "principles_distilled" in result

    def test_step_with_findings(self, temp_events_file):
        """findings 被转写成事件。"""
        from lighthouse import self_evolve
        from lighthouse.self_tune import Finding

        findings = [
            Finding(stage="collect", kind="no_links",
                    sources=["src_a", "src_b"],
                    detail="两个源取不到链接"),
            Finding(stage="summary", kind="summary_gap_high",
                    sources=[], detail="摘要空缺率高",
                    auto=False),
        ]
        result = self_evolve.step(round_no=1, findings=findings, run_self_improve=False)
        events = event_bus.read_events(source="self_tune")
        # 2 个源 + 1 个无源 = 3 条事件
        assert len(events) == 3
        assert result["events_emitted"] == 3

    def test_step_distills_principles(self, temp_events_file):
        """step 后原则表被蒸馏。"""
        from lighthouse import self_evolve
        from lighthouse.self_tune import Finding

        findings = [
            Finding(stage="collect", kind="no_links",
                    sources=["src_a"], detail="取不到链接"),
        ]
        result = self_evolve.step(round_no=1, findings=findings, run_self_improve=False)
        assert result["principles_distilled"] >= 1

    def test_report(self, temp_events_file):
        """report() 返回整体状态。"""
        from lighthouse import self_evolve
        event_bus.emit(layer="x", scope="s:a", failure_type="f", action="a",
                       repair_result="kept", source="test")
        r = self_evolve.report()
        assert "events" in r
        assert "principles" in r
        assert r["events"]["total"] == 1


class TestIntegration:
    """集成测试：验证两个系统的事件都进入统一流。"""

    def test_repair_and_self_improve_events_coexist(self, temp_events_file):
        """repair 和 self_improve 的事件在同一份 events.jsonl 里。"""
        # 模拟 repair 写事件
        event_bus.emit(layer="collector", scope="source_id:src_gov",
                       failure_type="selector_broken", action="discover_fix",
                       repair_result="candidate", source="repair",
                       detail="反推选择器")
        # 模拟 self_improve 写事件
        event_bus.emit(layer="summary", scope="metric:median_content_chars",
                       failure_type="content_too_short", action="set_constant",
                       repair_result="kept", source="self_improve",
                       utility=0.15, detail="调高最小长度")
        # 模拟 self_tune 写事件
        event_bus.emit(layer="analyze", scope="stage:analyze",
                       failure_type="gate_drop_concentrated", action="observe",
                       repair_result="observed", source="self_tune",
                       detail="门禁丢弃集中")

        events = event_bus.read_events()
        assert len(events) == 3
        sources = {e["source"] for e in events}
        assert sources == {"repair", "self_improve", "self_tune"}

        # 蒸馏后应该有 3 条原则（3 个不同的 scope_type + failure_type 组合）
        plist = principles.distill()
        assert len(plist) >= 3
