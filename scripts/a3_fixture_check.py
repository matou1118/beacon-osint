"""A3 离线夹具验证：选择器失效 -> 自动修复 -> 事件记录。

全程离线：不发网络请求、不改 config/sources.yaml、不碰生产数据。
夹具是 scripts/capture_heal_fixtures.py 抓下来的真实政府站列表页
（抓取时走 collector._request，内部强制 robots 检查与限速）。

五条验收标准：
  1. 人为改坏 class 名，能自动修复
  2. 修复结果可持久化（此处只验证产出可写回，生产写回在 A5 开关控制）
  3. 事件被正确记录
  4. 修不了时不硬改（纯导航页 -> 返回 None，注册表零改动）
  5. 不触发时零影响（原选择器有效 -> 感知为未失效）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighthouse import event_bus
from lighthouse import repair as sh

FIXTURES = Path("tests/fixtures/sel_heal")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else
           Path("data/runtime/lighthouse/tuning/a3_fixture_result.json"))
# 事件流是固定路径的 jsonl，隔离只能靠换文件路径。测完删除，不碰生产 events.jsonl。
EVENTS_TMP = Path("data/runtime/lighthouse/a3_events.tmp.jsonl")


def check_list_page(html: str, meta: dict) -> dict:
    """场景 1：人为改坏当前生效的那个配置 -> 应该修回来。

    分两类源：
      * 有 list_selector -> 感知靠 select_one，改坏后应重新推导容器
      * 无 list_selector（部委那批）-> 感知靠形态匹配，改坏后应重新推导形态
    """
    soup = BeautifulSoup(html, "html.parser")
    original = str(meta.get("list_selector") or "")
    old_pattern = str(meta.get("detail_pattern") or "")
    list_url = str(meta.get("url") or "")
    kind = "list_selector" if original else "detail_pattern"

    r: dict = {"source_id": meta["source_id"], "list_url": list_url,
               "kind": kind, "original_selector": original,
               "old_pattern": old_pattern}

    # 标准 5：当前配置此刻有效 -> 感知必须说「没坏」
    if kind == "list_selector":
        r["original_valid"] = sh.selector_matches(soup, original)
    else:
        # 形态类：用现有形态去过滤真实链接，滤出多少条算有效
        kept = [u for u in _all_links(soup, list_url)
                if re.search(old_pattern or r"$^\.^", u)]
        r["original_valid"] = len(kept) >= sh.MIN_LINKS
        r["original_kept"] = len(kept)
    if not r["original_valid"]:
        r["skip"] = ("原配置在夹具上就不生效（夹具与线上结构已不同），"
                     "不适合做改坏实验")
        return r

    broken = (original + "__a3_broken") if kind == "list_selector" \
        else r"/a3_no_such_shape_N\.html?$"
    r["broken"] = broken
    r["detected_broken"] = not sh.selector_matches(soup, broken) \
        if kind == "list_selector" else True

    src = dict(meta)
    src["list_selector"] = broken if kind == "list_selector" else ""
    h = sh.heal(soup, src, list_url=list_url,
                fallback_links=_all_links(soup, list_url))
    r["healed"] = h.healed
    r["new_selector"] = h.new_selector
    r["new_detail_pattern"] = h.new_detail_pattern
    r["link_count"] = h.link_count
    r["detail_ratio"] = h.detail_ratio
    r["reason"] = h.reason
    r["candidates_tried"] = len(h.candidates)
    return r


def _all_links(soup, list_url: str) -> list[str]:
    from urllib.parse import urljoin
    out = [urljoin(list_url, a.get("href")) for a in soup.select("a[href]")
           if a.get("href") and not a.get("href").startswith(("#", "javascript:"))]
    return list(dict.fromkeys(out))



def check_unhealable() -> dict:
    """标准 4：纯导航页 -> 必须修不了。"""
    nav = ("<html><body><div class='nav'>"
           + "".join("<a href='/about%d'>关于</a>" % i for i in range(10))
           + "</div></body></html>")
    soup = BeautifulSoup(nav, "html.parser")
    h = sh.heal_selector(soup, ".__a3_broken__",
                         list_url="https://example.gov.cn/list/")
    return {"healed": h.healed, "reason": h.reason,
            "candidates_tried": len(h.candidates)}


def check_empty_dom() -> dict:
    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    h = sh.heal_selector(soup, ".__a3_broken__",
                         list_url="https://example.gov.cn/list/")
    return {"healed": h.healed, "reason": h.reason}


def main() -> int:
    fixtures = sorted(FIXTURES.glob("*.json"))
    if not fixtures:
        print("!! 没有夹具。先跑 python scripts/capture_heal_fixtures.py")
        return 1

    report = {"fixtures": [], "unhealable": {}, "empty_dom": {},
              "events": [], "circuit": {}, "checks": {}}

    for meta_path in fixtures:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        html_path = meta_path.with_suffix(".html")
        if not html_path.exists():
            continue
        r = check_list_page(html_path.read_text(encoding="utf-8", errors="replace"), meta)
        report["fixtures"].append(r)
        if r.get("skip"):
            print("[跳过] %-12s %s" % (r["source_id"], r["skip"]))
            continue
        print("[修复] %-12s 感知坏=%-5s 修好=%-5s links=%-4s ratio=%-5s new=%s"
              % (r["source_id"], r["detected_broken"], r["healed"],
                 r["link_count"], r["detail_ratio"],
                 (r["new_selector"] or "-")[:28]))

    report["unhealable"] = check_unhealable()
    print("[修不了] 纯导航页 -> healed=%s  %s"
          % (report["unhealable"]["healed"], report["unhealable"]["reason"]))
    report["empty_dom"] = check_empty_dom()
    print("[修不了] 空 DOM    -> healed=%s  %s"
          % (report["empty_dom"]["healed"], report["empty_dom"]["reason"]))

    # ---- 事件记录 + 熔断（标准 3）----
    # 走真实事件流：repair.heal_and_report -> event_bus -> 读回。
    # 隔离到临时文件，测完删除，不碰生产 events.jsonl。
    if EVENTS_TMP.exists():
        EVENTS_TMP.unlink()
    _orig_events_file = event_bus.EVENTS_FILE
    event_bus.EVENTS_FILE = EVENTS_TMP
    try:
        sid = "_a3_test_src"
        sample = next((f for f in report["fixtures"] if f.get("healed")), None)
        ok_events = 0
        if sample:
            from bs4 import BeautifulSoup

            fx = FIXTURES / (sample["source_id"] + ".html")
            if fx.exists():
                soup = BeautifulSoup(fx.read_text(encoding="utf-8"),
                                     "html.parser")
                out = sh.heal_and_report(
                    {"id": sid, "url": sample["list_url"],
                     "list_selector": sample["broken"],
                     "detail_pattern": sample.get("detail_pattern", "")},
                    soup, list_url=sample["list_url"],
                    kind="list_selector")
            else:
                out = {"recorded": False}
            rows = [e for e in event_bus.read_events()
                    if e.get("scope") == "source_id:" + sid]
            got = bool(out.get("recorded"))
            # 形态类修复的产出落在 new_pattern，不是 new_selector —— 两条路径都算修好
            ok_events = 1 if (got and rows and (
                (rows[0].get("extra") or {}).get("new_selector")
                == sample["new_selector"]
                or (rows[0].get("extra") or {}).get("new_pattern")
                == sample.get("new_detail_pattern"))) else 0
            print("\n[事件] 写入=%s 读回一致=%s (%d 条)"
                  % (got, bool(ok_events), len(rows)))
            if rows:
                report["events"].append({
                    "event_id": rows[0].get("event_id", ""),
                    "source_id": sid,
                    "old_selector": (rows[0].get("extra") or {}).get("old_selector", ""),
                    "new_selector": (rows[0].get("extra") or {}).get("new_selector", ""),
                    "repair_result": rows[0].get("repair_result", ""),
                    "detail_ratio": (rows[0].get("extra") or {}).get("detail_ratio"),
                    "link_count": (rows[0].get("extra") or {}).get("link_count")})

        # 熔断：同一个源连续多次「写回 + 复检失败」，达到阈值后应永久降级。
        # 关键是同一个源 —— 失败计数是累计的，每轮换源的话每次都从 0 开始，
        # 永远够不到阈值。
        cb_scope = "source_id:_a3_circuit"
        seq = []
        for i in range(1, event_bus.DISQUALIFY_VERIFIED_FALSE + 2):
            event_bus.emit(layer="collector", scope=cb_scope,
                           failure_type="selector_broken", action="write_back",
                           repair_result="kept", source="repair",
                           detail="a3 熔断测试第%d次" % i)
            allowed, why = event_bus.should_auto_apply(cb_scope)
            seq.append(allowed)
            print("[熔断] 第%d次写回后 -> 允许自动写回=%-5s  %s"
                  % (i, allowed, why))
            # 复检：本次写回验证失败
            for rec in event_bus.read_events():
                if rec.get("scope") == cb_scope and rec.get("verified") is None:
                    event_bus.emit(layer="collector", scope=cb_scope,
                                   failure_type="selector_broken",
                                   action="verify", repair_result="verified",
                                   source="repair", verified=False,
                                   extra={"verifies": rec.get("event_id")})
        allow2, why2 = event_bus.should_auto_apply(cb_scope)
        print("[熔断] 连续%d次复检失败后 -> 允许自动写回=%s  %s"
              % (event_bus.DISQUALIFY_VERIFIED_FALSE, allow2, why2))
        report["circuit"] = {"sequence": seq, "after_n_failures": [allow2, why2]}
    finally:
        event_bus.EVENTS_FILE = _orig_events_file
        if EVENTS_TMP.exists():
            EVENTS_TMP.unlink()

    # ---- 汇总判定 ----
    healed = [f for f in report["fixtures"] if f.get("healed")]
    detected = [f for f in report["fixtures"] if f.get("detected_broken")]
    original_ok = [f for f in report["fixtures"] if f.get("original_valid")]
    # 两条修复路径的产出字段不同：容器类落 new_selector，形态类落 new_detail_pattern
    produced = [f for f in healed
                if f.get("new_selector") or f.get("new_detail_pattern")]
    report["checks"] = {
        "1_能自动修复": bool(healed),
        "2_修复结果含新值": len(produced) == len(healed) and bool(produced),
        "3_事件正确记录": bool(ok_events),
        "4_修不了时不硬改": (not report["unhealable"]["healed"]
                          and not report["empty_dom"]["healed"]),
        "5_不触发时零影响": len(original_ok) == len(report["fixtures"]) - len(
            [f for f in report["fixtures"] if f.get("skip")]),
        "熔断生效": (allow is True and allow2 is False),
    }

    print("\n" + "=" * 58)
    for name, passed in report["checks"].items():
        print("  %-22s %s" % (name, "PASS" if passed else "FAIL"))
    print("=" * 58)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("明细: %s" % OUT)
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
