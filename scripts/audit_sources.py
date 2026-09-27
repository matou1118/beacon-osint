"""体检未启用的源，给出可执行的原因分类。

为什么需要
  config/sources.yaml 里 145 个源有 57 个 blocked、36 个 pending_reprobe，
  合计 93 个（64%）完全没在用，而这正是"数据量不够"最大的可释放部分。
  但它们的状态是配置里写死的，`source_sync_state.last_error` 全是空——
  说明从未尝试过，也就没有任何依据判断哪些能救、哪些真的不行。

分类（按可执行性排序，不是按错误类型）
  reachable        能抓且抽得出记录 —— 建议直接启用
  list_empty       页面能开但没抽到链接 —— 多半是 list_selector 失效
  robots_denied    robots 明确拒绝 —— 不可启用，也不绕过
  robots_blocked   读不到 robots（WAF 拦截）—— 不可确认，保持禁用
  http_error       4xx/5xx —— 失效或需渲染
  needs_render     静态抓不到但渲染可能行 —— 建议加 render_mode
  exception        其他异常

只读探测：遵循 robots 与每源 3 秒限速，不重试、不绕过任何 WAF。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

from collectors.base import BaseCollector
from main import collector_for

ROOT = Path(__file__).resolve().parents[1]


def classify(source: dict) -> dict:
    """探测单个源，返回原因分类与证据。不写入任何数据。"""
    sid = source.get("id", "")
    out: dict = {"id": sid, "name": source.get("name", ""), "status": source.get("status"),
                 "region": source.get("region_province", ""), "reason": "unknown",
                 "detail": ""}
    collector = None
    try:
        # BaseCollector 是抽象类，要通过 collector_for 拿到按 access_method
        # 分派的真实实现（static / rss / render / js 等），否则探测的不是源
        # 实际会走的路径。
        collector = collector_for(dict(source, request_interval_seconds=3))
        if collector is None:
            out["reason"] = "exception"
            out["detail"] = "collector_for 返回 None（access_method 无法识别）"
            return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = "exception"
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        # 必须走 collect()，不能自己 fetch_list + fetch_detail。
        # collect() 内部会跳过栏目落地页（SectionLandingError 只 continue），
        # 直接 fetch_detail 拿到第一条栏目入口就判死，会把能正常采集的源
        # 误报成 detail_failed——早先 41 个源就是这样被误判的。
        records = collector.collect(limit=5)
        out["found"] = len(records)
        if not records:
            out["reason"] = "list_empty" if not collector.errors else "collect_empty"
            out["detail"] = (collector.errors[0].get("error", "") if collector.errors
                             else f"未抽到链接（list_selector={source.get('list_selector')}）")[:140]
            return out
        bodies = [len((r.get("content") or "").strip()) for r in records]
        good = [n for n in bodies if n >= 200]
        if not good:
            out["reason"] = "list_empty"
            out["detail"] = f"{len(records)} 条正文都 <200 字（{bodies[:5]}）"
            return out
        out["reason"] = "reachable"
        out["detail"] = f"{len(records)} 条记录，正文 {min(good)}~{max(bodies)} 字"
        if collector.errors:
            out["partial_errors"] = len(collector.errors)
        return out
    except PermissionError as exc:
        text = str(exc)
        out["reason"] = "robots_blocked" if "robots.txt" in text else "robots_denied"
        out["detail"] = text[:120]
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "HTTP 4" in text or "HTTP 5" in text:
            out["reason"] = "http_error"
        elif "Playwright Sync API" in text:
            out["reason"] = "playwright_in_async"
        out["detail"] = f"{type(exc).__name__}: {text[:110]}"
    finally:
        try:
            collector.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", default="blocked,pending_reprobe",
                        help="要体检的状态，逗号分隔；传 all 体检全部")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default="data/source_audit.json")
    args = parser.parse_args()

    import yaml

    data = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    sources = data["sources"] if isinstance(data, dict) else data
    if args.status != "all":
        wanted = {s.strip() for s in args.status.split(",") if s.strip()}
        sources = [s for s in sources if s.get("status") in wanted]
    if args.limit:
        sources = sources[:args.limit]

    print(f"体检 {len(sources)} 个未启用源（robots + 3s 限速，串行，需数分钟）\n", flush=True)
    results = []
    for index, source in enumerate(sources, 1):
        row = classify(source)
        results.append(row)
        print(f"[{index}/{len(sources)}] {row['id']:<20} {row['reason']:<15} {row['detail'][:70]}",
              flush=True)

    counts: dict[str, int] = {}
    for row in results:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    print("\n" + "=" * 60)
    for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<16} {count}")
    actionable = [r for r in results if r["reason"] == "reachable"]
    print(f"\n可直接启用: {len(actionable)} 个 —— {[r['id'] for r in actionable]}")
    (ROOT / args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(f"明细写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
