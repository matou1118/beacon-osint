"""自我迭代闭环的端到端联动测试。

**不是单元测试** —— 是真跑一遍完整回路，验证每一步都咬合：

    1. 造一个真实失败（预算耗尽）
    2. _failure_types 把它翻译成原则表的 failure_type
    3. apply_principles 查到适用原则
    4. 动作真改配置（读文件确认，不看返回值）
    5. 下一轮效果被记录，success_count 从 0 动起来
    6. 限额生效（不会无限改）

改之前这套是死的：actions_total 恒为 0，success_count 恒为 0。
"""
from __future__ import annotations

import datetime
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lighthouse.run as run_mod  # noqa: E402
from lighthouse import principle_applier, principles
from lighthouse.run import _failure_types

RUNTIME = ROOT / "data" / "runtime" / "lighthouse"
CFG = ROOT / "lighthouse" / "config" / "web_collector_config.yaml"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print("  [%s] %-46s %s" % (PASS if ok else FAIL, name, detail[:60]))


def cfg_value(key: str):
    import yaml

    c = yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}
    def dig(d):
        for part in key.split("."):
            if not isinstance(d, dict):
                return None
            d = d.get(part)
        return d
    return dig(c)


def _set_cfg_value(key: str, value: int) -> None:
    import yaml

    data = yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    CFG.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")


def _bump_demo_records(db_path: Path) -> None:
    """给 demo_gov 加一条记录，模拟「动作之后指标变好」。

    效果回填的判据是「该源记录数是否增长」，所以要造出增长才能验证 success
    那一路。真跑一轮采集太慢，这里直接写库 —— 但写的是**临时库**，见 main。
    """
    con = sqlite3.connect(db_path)
    try:
        # 15 个 NOT NULL 且无默认值的字段必须都给值（清单来自
        # PRAGMA table_info(announcements)）—— 少一个就是 IntegrityError。
        # 全部用命名参数绑定，避免占位符与列顺序错位。
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.execute(
            """
            INSERT INTO announcements (
              source_id, source_url, title, content, content_hash,
              retention_class, focus_tracked, project_tracked, industry_focus,
              guidance_source, needs_review,
              fetched_at, last_seen_at, created_at, updated_at
            ) VALUES (
              :sid, :url, :title, :body, :h,
              'public', 0, 0, 0, 0, 0,
              :now, :now, :now, :now
            )
            """,
            {"sid": "demo_gov",
             "url": "https://example.gov.cn/loop-test-%d" % int(time.time()),
             "title": "联动测试记录",
             "body": "用于验证自我迭代闭环的效果回填。",
             "h": "loop-test-%d" % int(time.time() * 1000),
             "now": now})
        con.commit()
    finally:
        con.close()


def _probe_alarms(backoff: dict | None = None,
                  db: dict | None = None) -> list[str]:
    """问 status_summary「现在有没有告警」，真的去问，不造假。

    2026-10-01 整夜就是这个漏掉的：进程活着、日志正常、没有一行报错，
    但 34 个源全被退避闸挡住、一夜零入库。原来只看「watchdog 进程在不在」，
    于是那一夜看起来完全健康。

    ``backoff`` 可以注入，用来构造「全被挡住」这种场景——真去改
    source_backoff.json 没用，collect() 内部 exec 的是另一份模块实例。
    """
    import importlib.util

    path = ROOT / "scripts" / "status_summary.py"
    spec = importlib.util.spec_from_file_location("_ss_probe", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        data = module.collect()
        if backoff is not None:
            data["backoff"] = backoff
        if db is not None:
            data["db"] = db
        return list(module._alarms(data))
    except Exception as exc:                                  # noqa: BLE001
        return ["探针自身失败 %s: %s" % (type(exc).__name__, exc)]


def _age_pending(seconds: int) -> None:
    """把待验证记录的 ts 往前拨，绕过 verify_pending 的「至少隔一轮」门槛。

    生产环境那个 60 秒门槛是对的（配置立刻生效、指标要等采集），测试里
    不该真等 —— 拨时间戳验证的是同一套逻辑。
    """
    path = RUNTIME / "principle_actions.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for rows in state.values():
        for r in rows:
            if not r.get("verified"):
                r["ts"] = float(r.get("ts", time.time())) - seconds
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _scratch_db() -> Path:
    """建一个和生产库同构的临时库，并建出 demo_gov 需要的那些表。

    **不要在生产库上跑这个测试。** 2026-10-01 实测踩过两次：
      1. 原设计是「备份 145MB 生产库 → 改 → 还原」。可是 beacon.db 是 WAL 模式，
         还原只覆盖主文件，``beacon.db-wal`` 里那条 INSERT 还留着，下次打开
         SQLite 重放 WAL，记录又回来了 —— 生产库里堆了 16 条「联动测试记录」，
         而且是每小时 +3（联动测试和 pytest 各跑一遍）。
      2. 就算 WAL 也还原了，测试期间采集正常写入的数据会被一起回滚掉。

    临时库没这个问题：跑完整个目录删掉，不留痕。
    """
    scratch = Path(tempfile.mkdtemp(prefix="loop-test-")) / "beacon.db"
    src = sqlite3.connect(f"file:{ROOT / 'data' / 'beacon.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(scratch)
    try:
        for (sql,) in src.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
            dst.execute(sql)
    finally:
        src.close()
        dst.close()
    return scratch


def main() -> int:
    print("=" * 72)
    print(" 自我迭代闭环 · 端到端联动测试")
    print("=" * 72)
    scratch = _scratch_db()
    # 效果回填读的是这个模块级 DB 常量，指向临时库。
    _orig_db, principle_applier.DB = principle_applier.DB, scratch
    print(" 临时库: %s" % scratch)

    # 只备份文本配置。数据库不备份也不还原——根本不碰它。
    # 每个会被改的文件都要备份。**漏一个就等于让测试改生产状态。**
    #
    # 2026-10-02 踩过两次：
    #   1. ``source_backoff.json`` 没备份 —— 全量回填测试和采集抢库，调度器
    #      连续异常退出 62 次。
    #   2. ``events.jsonl`` 没备份 —— 联动测试往生产事件流里写。
    #   3. ``backoff_override.json`` 没备份 —— 联动测试第 9 步真的调用
    #      ``loosen_backoff``，它会落盘，于是测试跑完生产退避阶梯就变了
    #      （8/16/32/48/96 → 8/14/28/42/84 小时），等价性测试当场失败。
    #
    # 所以新增「会写盘的自迭代动作」时，这个清单必须同步加。
    text_backups = [CFG, principles.PRINCIPLES_FILE,
                    RUNTIME / "principle_actions.json",
                    RUNTIME / "events.jsonl",
                    run_mod.SourceBackoff._override_path()]

    backups: dict[Path, Any] = {}
    for p in text_backups:
        backups[p] = p.read_text(encoding="utf-8") if p.exists() else None

    try:
        # ── 1. 造真实失败 ────────────────────────────────────────────
        print("\n【1】造一个真实失败：预算耗尽")
        outcome = {"notes": ["source time budget 300s exhausted after 0 records"],
                   "dropped": [], "records": []}
        fts = _failure_types(outcome)
        check("失败被翻译成 failure_type", fts == ["budget_exhausted"],
              "得到 %s" % fts)

        # ── 2. 查到适用原则 ──────────────────────────────────────────
        print("\n【2】查原则表")
        matched = principles.apply_principles(
            "source_id:demo_gov", "budget_exhausted", top_k=5)
        check("查到适用原则", bool(matched),
              "命中 %d 条" % len(matched))
        if not matched:
            print("\n闭环断在第 2 步：查不到原则。后续步骤无法验证。")
            return 1
        best = matched[0]
        print("       最高效用: %s (score=%.2f, action=%s)"
              % (best.get("key"), best.get("utility_score", 0),
                 best.get("best_action")))
        check("该原则的动作可执行",
              principle_applier.ACTIONS.get(str(best.get("best_action"))) is not None,
              "action=%s" % best.get("best_action"))

        # ── 3. 动作真改配置 ──────────────────────────────────────────
        print("\n【3】执行动作 —— 读文件确认，不看返回值")
        # 先把生产值归零再测，否则这条断言测的不是动作，是生产配置当前长什么样。
        #
        # 实测踩过（2026-10-02）：source_budget_seconds 早已被自主迭代的
        # raise_budget 推到 _grow 的封顶 1800，于是 raise_budget 恒返回
        # applied=False，「配置文件真的变了」永远过不了。测试依赖生产配置的
        # 具体数值 = 测试会随生产漂移而失败，而失败原因和被测代码无关。
        # 归零后动作必然有空间可加，测完 main() 会把整个文件还原。
        _set_cfg_value("rate_limit.source_budget_seconds", 0)
        before = cfg_value("rate_limit.source_budget_seconds")
        print("       改前 source_budget_seconds = %s" % before)
        out = principle_applier.apply("demo_gov", "budget_exhausted")
        check("动作报告已执行", bool(out.get("applied")),
              "action=%s reason=%s" % (out.get("action"), out.get("reason")))
        after = cfg_value("rate_limit.source_budget_seconds")
        print("       改后 source_budget_seconds = %s" % after)
        check("配置文件真的变了", after != before,
              "%s -> %s" % (before, after))

        # ── 4. 事件留痕 ──────────────────────────────────────────────
        print("\n【4】事件流留痕")
        from lighthouse import event_bus

        acts = [e for e in event_bus.read_events()
                if e.get("source") == "principle_applier"]
        check("动作写进了事件流", bool(acts), "共 %d 条" % len(acts))
        if acts:
            print("       最新: %s / %s" % (acts[-1].get("action"),
                                          acts[-1].get("repair_result")))

        # ── 5. 限额生效 ──────────────────────────────────────────────
        print("\n【5】每源限额生效（防无限改配置）")
        outs = [principle_applier.apply("demo_gov", "budget_exhausted")
                for _ in range(principle_applier.MAX_ACTIONS_PER_SCOPE + 3)]
        applied_n = sum(1 for o in outs if o.get("applied"))
        check("超过限额后不再执行", applied_n <= principle_applier.MAX_ACTIONS_PER_SCOPE,
              "额外执行 %d 次（限额 %d）" % (applied_n,
                                            principle_applier.MAX_ACTIONS_PER_SCOPE))
        last = outs[-1]
        print("       最后一次: applied=%s reason=%s"
              % (last.get("applied"), last.get("reason")))

        # ── 6. 效果回填 ──────────────────────────────────────────────
        print("\n【6】下一轮回填效果 —— success_count 从 0 动起来")
        print("       这是闭环真的通了的标志。之前它恒为 0，系统永远学不到东西。")
        pend = [r for r in principles.load()
                if r.get("key") == "source_id|budget_exhausted"]
        check("动作已记下待验证状态",
              bool(json.loads((RUNTIME / "principle_actions.json")
                              .read_text(encoding="utf-8"))
                   .get("source_id:demo_gov")),
              "state 里有该源的执行记录")

        # 造「效果变好」：让 demo_gov 的记录数 +1
        _bump_demo_records(scratch)
        # verify_pending 有 60 秒的「至少隔一轮」门槛：配置改动立刻生效，
        # 但指标变化要等采集完成，刚执行就比对没有意义。
        # 这里不真的等 60 秒 —— 把 state 里 ts 往前拨，绕过等待直接验证逻辑。
        _age_pending(seconds=120)
        ver = principle_applier.verify_pending()
        print("       verify_pending -> %s" % json.dumps(
            {k: v for k, v in ver.items() if k != "keys"},
            ensure_ascii=False))
        check("效果被判定", ver.get("success", 0) + ver.get("failed", 0) > 0,
              "成功 %s / 失败 %s" % (ver.get("success"), ver.get("failed")))
        check("判定为成功（记录数增长）", ver.get("success", 0) >= 1,
              "成功 %s" % ver.get("success"))

        # ── 7. success_count / utility_score 真的动了 ────────────────
        print("\n【7】原则表被更新 —— success/utility 不再是 0")
        row = next((r for r in principles.load()
                    if r.get("key") == "source_id|budget_exhausted"), {})
        print("       success_count=%s fail_count=%s utility=%.4f"
              % (row.get("success_count"), row.get("fail_count"),
                 float(row.get("utility_score") or 0)))
        check("success_count > 0", int(row.get("success_count") or 0) > 0,
              "success_count=%s" % row.get("success_count"))
        # utility = (成功 + 先验 0.5) / (成功 + 失败 + 1)
        #
        # **按实际跑出来的成功/失败数算期望**，不要写死 3/0。原来写死
        # 「3 成功 → 0.875」，2026-10-01 给 _failure_types() 加了
        # deep_backfill_needed 分支后，动作可能落到不同的效果判据上，
        # 实跑变成 2 成功 1 失败，写死的期望立刻失配。
        #
        # 写死的数字有两个毛病：它在描述「当时的巧合」而不是「公式」，
        # 而且真出问题时只会告诉你「期望 0.875 实际 0.625」，不告诉你
        # 该期望多少。
        succ = int(row.get("success_count") or 0)
        fail = int(row.get("fail_count") or 0)
        want = (succ + 0.5) / (succ + fail + 1.0)
        got = float(row.get("utility_score") or 0)
        check("utility_score 按公式重算（不再是初值 1.0）",
              abs(got - want) < 0.01 and got != 1.0,
              "utility=%.4f（期望 (%.0f+0.5)/(%.0f+%.0f+1)=%.4f）"
              % (got, succ, succ, fail, want))

        st = principle_applier.stats()
        check("自动动作被计数", st["actions_total"] > 0,
              "actions_total=%d scopes=%d" % (st["actions_total"],
                                              st["scopes"]))

        # ── 8. ★ 静默停摆能被发现（2026-10-01 整夜零入库的直接对策）──
        #
        # 那一夜的特征：进程活着、退出码 0、日志全是「正常完成（无源可采）」、
        # 没有一行报错——但 34 个源全被退避闸挡住，一夜零入库。
        # 原来的联动测试完全测不到这一类，因为它只验「闭环通不通」。
        print("\n【8】静默停摆可被发现")
        # 注入一份「8 小时没入库」的 db 快照，而不是读生产库。
        #
        # 实测踩过（2026-10-02）：这条断言原先直接读生产 db.last_ingest。
        # 那天正好有一轮全量采集在跑、库 1 小时前刚入库，「静默停摆」
        # 当然不该报——于是测试红了，而被测代码完全正确。
        # 「静默停摆」要成立就必须库是静的，拿生产的活跃状态去断言它，
        # 等于断言「现在不该报警」。场景得自己造。
        stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=8)
        alarms = _probe_alarms(db={"last_ingest": stale.isoformat()})
        check("零入库超时会报警（不只看进程活不活）",
              any("静默停摆" in a for a in alarms),
              "alarms=%s" % (alarms or "无"))
        # 「可采源为 0」这条不能靠真实运行状态断言——测试跑的时候可能真有
        # 源可采（那就不该报警，报警反而是 bug）。所以**构造**这个场景：
        # 临时把退避状态改成全挡住，问告警会不会喊，问完还原。
        # 走真实的 _alarms()，但喂它一份「全被挡住」的 backoff 字典——
        # 不用改文件也不用 monkeypatch：collect() 内部会独立 exec 一份
        # run.py，patch 那边不生效（试过，白折腾一轮）。
        gated = _probe_alarms(backoff={"runs": 9, "tracked": 34, "sleeping": 34,
                                       "terminal_blocked": 0, "robots_ids": [],
                                       "top_dry": []})
        check("可采源为 0 会报警（退避把源饿死）",
              any("可采源为 0" in a for a in gated),
              "alarms=%s" % (gated or "无"))
        # 反向：有源可采时不该报这条（否则就是误报，一夜几十条变噪音）
        fine = _probe_alarms(backoff={"runs": 9, "tracked": 34, "sleeping": 5,
                                      "terminal_blocked": 0, "robots_ids": [],
                                      "top_dry": []})
        check("有源可采时不误报「可采源为 0」",
              not any("可采源为 0" in a for a in fine),
              "alarms=%s" % (fine or "无"))

        # ── 9. ★ 失败的动作会退役（不再反复执行已证明无效的动作）──
        #
        # 实测（2026-10-02）：`source_id|verify` 的 best_action=raise_budget
        # 失败了几十次仍在被执行，top_k 里的备选永远轮不到。没有退役机制时，
        # 系统在做已知的无效动作，而且因为每次都「执行成功」所以看不出异常。
        print("\n【9】失败动作退役 —— 不再反复做无效动作")
        _orig_ladder = {k: tuple(v) for k, v in
                        run_mod.SourceBackoff.LADDER.items()}
        try:
            # 阶梯已经压到首档（每一档都等于首档）时，动作应当报「压到底了」
            # 而不是假装成功。
            flat = (8 * 3600,) * 5
            run_mod.SourceBackoff.LADDER = dict(
                run_mod.SourceBackoff.LADDER, daily=flat)
            out = principle_applier.loosen_backoff("demo_gov", "source_id:demo_gov")
            check("退避阶梯已压到底时不再假装成功",
                  out.get("applied") is False and "首档" in str(out.get("reason")),
                  "applied=%s reason=%s" % (out.get("applied"), out.get("reason")))
            # 正常情况：阶梯确实被前移了
            steep = (8 * 3600, 16 * 3600, 32 * 3600, 48 * 3600, 96 * 3600)
            run_mod.SourceBackoff.LADDER = dict(
                run_mod.SourceBackoff.LADDER, daily=steep)
            out2 = principle_applier.loosen_backoff("demo_gov", "source_id:demo_gov")
            check("阶梯陡时真的会前移（不是 observe_only 假动作）",
                  out2.get("applied") is True
                  and out2["to_hours"][-1] < out2["from_hours"][-1],
                  "%s -> %s" % (out2.get("from_hours"), out2.get("to_hours")))
            check("退避前移不会动首档（礼貌下限是红线）",
                  out2["to_hours"][0] == out2["from_hours"][0] == 8,
                  "首档 %s -> %s" % (out2["from_hours"][0], out2["to_hours"][0]))
            # 退役判据：失败够多次的动作必须被跳过
            check("退役门槛已配置且为正",
                  principle_applier.RETIRE_AFTER_FAILS > 0,
                  "RETIRE_AFTER_FAILS=%d" % principle_applier.RETIRE_AFTER_FAILS)
        finally:
            run_mod.SourceBackoff.LADDER = _orig_ladder

        # ── 10. ★ 每个动作名都有真实实现（防止「应用成功但什么都没做」）──
        print("\n【10】动作表里没有假动作")
        missing = sorted({n for n in principle_applier.ACTIONS.values()
                          if n and n not in dir(principle_applier)})
        check("ACTIONS 里每个名字都有实现", not missing, "缺: %s" % missing)
        seeded = {p.get("best_action") for p in principles.load()}
        unimplemented = sorted(
            a for a in seeded
            if a not in principle_applier.ACTIONS
            and a not in ("auto_observe", "observe_only", "report_only"))
        check("原则表里没有指向未实现动作的 best_action",
              not unimplemented, "未实现: %s" % unimplemented)

    finally:
        for path, data in backups.items():
            try:
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(data, encoding="utf-8")
            except OSError:
                pass
        principle_applier.DB = _orig_db
        shutil.rmtree(scratch.parent, ignore_errors=True)

    # ── 汇总 ──────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    failed = [r for r in results if r[0] == FAIL]
    print(" 联动测试: %d 项，%d 通过，%d 失败"
          % (len(results), len(results) - len(failed), len(failed)))
    for _, name, detail in failed:
        print("   FAILED: %s  %s" % (name, detail))
    print(" 文件已全部还原")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())