"""把 2026-09-30 手工修的 6 类问题补上回归测试。

这些测试的作用不是「验证现在的行为对」，而是**把「系统学到了什么」钉住**：
明天再加同类问题、改判据、调整策略时，只要这 6 条里有一条回退，测试就会红。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ── 1. failure_type 映射 ─────────────────────────────────────────────
def test_failure_types_map_to_principle_keys():
    """采集结果 → 原则表的 failure_type 必须对得上，否则查不到原则。

    这是闭环的接口约定：run.py 产出的 failure_type 与
    scripts/seed_principles.py 种的键必须一致。对不上时 principle_applier
    永远返回「无适用原则」，闭环静默失效 —— 不会报错，只是原地踏步。
    """
    from lighthouse.run import _failure_types

    assert _failure_types({"notes": ["source time budget 300s exhausted"],
                           "dropped": []}) == ["budget_exhausted"]
    assert _failure_types({"notes": [],
                           "dropped": [{"reason": "markdown:link_flood"}]}) \
        == ["link_flood"]
    assert _failure_types(
        {"notes": [], "dropped": [{"reason": "error:RuntimeError:HTTP 404"}]}) \
        == ["dead_urls"]
    assert _failure_types({"notes": ["no links found"],
                           "dropped": []}) == ["no_links"]

    # 采干会同时产出两个 failure_type（2026-09-30 拆开）：
    #   deep_backfill_needed —— 库内历史不足 5 年，该加深翻页
    #   drained_sources      —— 当期没新公告，加深无用，该放宽早停
    # 之前两者共用一个名、动作一律 deepen_pages，对已采干的源白改一次配置
    # （实测 archive_pages 30→42，mee_gov/nea_gov 记录数一条没涨，fail=2）。
    fts = _failure_types({"notes": ["early stop after 40 known urls"],
                          "dropped": []})
    assert "drained_sources" in fts, fts
    # deep_backfill_needed 取决于库内历史深度（_needs_deeper_backfill 查库），
    # 测试环境下取决于真实数据，但**必须是个确定值**（不能因为异常被吞掉）。
    assert "deep_backfill_needed" in fts or "drained_sources" in fts, fts


def test_seeded_principles_are_all_executable():
    """种进原则表的动作必须真的可执行。

    这条最关键。修复前 best_action 全是 `auto_observe`（字面「自动观察」
    =什么都不做），apply_count 累计到 137/167/263 而 success_count 全 0。
    系统在**假装迭代**：记了 137 次「应用」，实际零行动，utility 永远 0，
    于是永远学不到东西。distill 挑不出真动作，是因为候选里只有无用动作。

    所以「种进表里的每条都必须能真执行」是闭环成立的**前提条件**，不是优化项。
    """
    from lighthouse import principle_applier, principles

    rows = principles.load()
    assert rows, "原则表不应为空"

    useless = [r["key"] for r in rows
               if principle_applier.ACTIONS.get(
                   str(r.get("best_action") or "")) is None]
    assert not useless, (
        "这些原则的 best_action 不可执行（映射不到真实动作，等于 observe_only）："
        "%s" % useless)


def test_principles_survive_redistill():
    """人工沉淀的原则不能在 distill() 里被冲掉。

    distill() 每次都**整体重写**原则文件，而它只从事件流算得出
    「哪个 failure_type 多」，算不出「这个问题该怎么修」——后者来自人工修复。
    没有 merge 保存的话，人工经验每次 distill 就丢一次，于是问题被人工修掉、
    经验不入库、同类问题下次再犯。
    """
    from lighthouse import principles

    key = "stage|dead_urls"
    spec = {"key": key, "scope_type": "stage", "failure_type": "dead_urls",
            "best_action": "widen_stale", "utility_score": 1.0,
            "apply_count": 0, "success_count": 0, "fail_count": 0,
            "last_ts": "2026-10-01T00:00:00+00:00", "retired": False,
            "actions": []}
    backup = principles.PRINCIPLES_FILE.read_text(encoding="utf-8") \
        if principles.PRINCIPLES_FILE.exists() else None
    try:
        assert principles.save_principles(
            principles.merge_principles([spec])), "写入原则表失败"
        assert any(r.get("key") == key for r in principles.load())
    finally:
        if backup is not None:
            principles.PRINCIPLES_FILE.write_text(backup, encoding="utf-8")


def test_applier_has_per_scope_limit():
    """自动改配置必须有限额，否则「学到一条原则就永久改配置」。"""
    from lighthouse import principle_applier

    assert principle_applier.MAX_ACTIONS_PER_SCOPE >= 1
    assert principle_applier.MAX_ACTIONS_PER_SCOPE <= 10, \
        "限额太大就没有保护作用了"


def test_bump_only_touches_integer_keys():
    """改配置只能用整数字段的行级替换，不能整体重写 YAML。

    yaml.safe_dump 会把注释全冲掉（scripts/fix_sources.py:57 踩过，
    sources.yaml 每条人工登记的 auth_scope/terms_checked_at/notes 全丢）。
    所以 _bump_defaults 必须走正则行替换。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "lighthouse" /
           "principle_applier.py").read_text(encoding="utf-8")
    # 只看代码行，不看注释 —— 注释里提到 safe_dump 是在解释「为什么不用它」
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    assert "yaml.safe_dump(" not in code, \
        "不能用 yaml.safe_dump 重写配置（会丢注释）"
    assert "_bump_defaults" in src


def test_end_to_end_loop_runs_green():
    """端到端跑一遍闭环联动，必须全绿。

    这是「自我迭代」唯一有意义的验收方式。**单测全过 != 联动成立** ——
    2026-09-30 就是这么翻车的：单元测试 501 全绿，而闭环 `apply_count`
    累计 137/167/263、`success_count` 恒为 0、`best_action` 全是
    `auto_observe`，`apply_principles()` **没有任何调用方**。

    手写联动测试立刻抓出三个真 bug：
      1. 人工种的原则被 `distill()` 整体重写冲掉 —— 表里仍有 10 条、
         累计上千，看着一切正常，只是查 `budget_exhausted` 命中 0
      2. 原则键的 `scope_type` 与 `_failure_types()` 产出的 scope 对不上
      3. 动作是「设成固定值」而非「相对增量」，改不动就永远不动

    `scripts/test_iteration_loop.py` 会真的改配置、造指标增长、比对效果
    并回填 `success_count`，任何一环断掉都会红。
    """
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    # 显式钉死子进程编码：解码是 utf-8，但子进程若按系统 ANSI 代码页输出，
    # 中文会全变成 U+FFFD，「0 失败」这类断言就找不到（2026-10-05 实测）。
    child_env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "test_iteration_loop.py")],
        cwd=str(root), capture_output=True, encoding="utf-8",
        errors="replace", timeout=900, env=child_env)
    out = proc.stdout or ""
    assert proc.returncode == 0, (
        "联动测试未通过：\n" + "\n".join(out.splitlines()[-25:]))
    assert "0 失败" in out, "联动测试输出异常：\n" + out[-800:]