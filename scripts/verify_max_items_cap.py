"""验证某个源到底是被 ``max_items`` 截断，还是站点真就那么多链接。

为什么要单独一个脚本
--------------------
``measure_pagination_headroom.py`` 一次要跑 276 个任务，串行几小时、并行也抢
Playwright。而 ``total == max_items`` 这个信号**看起来**很确定，实际上有两种
可能，处理方式完全相反：

  A. 站点还有更多，只是被我们截断 → 放开 ``max_items``（2026-10-05 实测
     js_gxt 800→999 URL / 未入库 316→515；sd_rst 800→1003 / 77→280）
  B. 站点就这么多了，800 恰好是它的总数 → 放开只会白花时间和带宽

所以必须**实测**：同一源跑两次（当前 max_items vs 5000），比 ``total`` 和
``fresh``。只涨 fresh 才值得改。

``--sources`` 可以一次验多个，但每个源都可能几分钟（archive_pages=30 + 渲染），
所以支持后台运行 + 增量落盘，中断也不丢已验的结果。

用法：
    python scripts/verify_max_items_cap.py --sources sc_nx_gov,sc_gxt --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from lighthouse.run import Ladder, build_collector  # noqa: E402

DB = ROOT / "data" / "beacon.db"
SOURCES = ROOT / "config" / "sources.yaml"
OUT = ROOT / "data" / "runtime" / "lighthouse" / "max_items_verify.json"
TRIAL_MAX = 5000


def _load_cfg() -> dict:
    path = ROOT / "lighthouse" / "config" / "web_collector_config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _sources() -> dict[str, dict]:
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8")) or {}
    return {str(s.get("id")): s for s in (data.get("sources") or []) if s.get("id")}


def _known() -> set[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return {str(r[0]) for r in con.execute("SELECT source_url FROM announcements")}
    finally:
        con.close()


def probe(src: dict, max_items: int, cfg: dict, known: set[str],
          archive_pages: int) -> dict:
    s = dict(src)
    s["max_items"] = max_items
    s["archive_pages"] = archive_pages
    ladder = Ladder(cfg, None)
    collector = build_collector(s, ladder.levels)
    ladder.collector = collector
    started = time.time()
    try:
        links = list(collector.fetch_list() or [])
    except Exception as exc:                                   # noqa: BLE001
        return {"max_items": max_items, "error": f"{type(exc).__name__}: {exc}"[:120]}
    finally:
        try:
            collector.close()
        except Exception:                                      # noqa: BLE001
            pass
    urls = {x if isinstance(x, str) else str(getattr(x, "url", x)) for x in links}
    return {"max_items": max_items, "total": len(urls),
            "fresh": len([u for u in urls if u not in known]),
            "seconds": round(time.time() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True)
    ap.add_argument("--archive-pages", type=int, default=30)
    ap.add_argument("--trial-max", type=int, default=TRIAL_MAX)
    ap.add_argument("--min-fresh-gain", type=int, default=10,
                    help="放开后至少多出这么多条未入库才值得改（省掉白花的时间）")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    cfg = _load_cfg()
    srcs = _sources()
    known = _known()
    ids = [s.strip() for s in args.sources.split(",") if s.strip()]

    results: dict[str, dict] = {}
    out_path = Path(args.out)
    if out_path.exists():
        try:
            results = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError:
            results = {}

    for sid in ids:
        src = srcs.get(sid)
        if not src:
            print("%s 不在 sources.yaml，跳过" % sid)
            continue
        cur = int(src.get("max_items") or 0)
        print("\n=== %s  (max_items=%s) ===" % (sid, cur), flush=True)
        row = results.setdefault(sid, {"archive_pages": args.archive_pages, "trials": {}})
        for mi in (cur, args.trial_max):
            if str(mi) in row["trials"]:
                print("   max_items=%-5d 已有结果，跳过" % mi, flush=True)
                continue
            r = probe(src, mi, cfg, known, args.archive_pages)
            row["trials"][str(mi)] = r
            out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                encoding="utf-8")   # 增量落盘，中断不丢
            if r.get("error"):
                print("   max_items=%-5d 失败：%s" % (mi, r["error"]), flush=True)
            else:
                print("   max_items=%-5d total=%-5d 未入库=%-5d %5.1fs"
                      % (mi, r["total"], r["fresh"], r["seconds"]), flush=True)

        base = row["trials"].get(str(cur)) or {}
        trial = row["trials"].get(str(args.trial_max)) or {}
        if base.get("error") or trial.get("error"):
            print("   结论：有一侧失败，不做判断")
            continue
        gain = int(trial["fresh"]) - int(base["fresh"])
        print("   结论：total %d -> %d，未入库 %d -> %d（%+d）"
              % (base["total"], trial["total"], base["fresh"], trial["fresh"], gain))
        if gain >= args.min_fresh_gain:
            row["verdict"] = "lift"
            row["recommended_max_items"] = args.trial_max
            row["fresh_gain"] = gain
            print("   → 值得放开到 %d" % args.trial_max)
        else:
            row["verdict"] = "keep"
            row["fresh_gain"] = gain
            print("   → 不改（站点可能真就这么多，省下的时间比这 %d 条值）" % gain)

    lift = {sid: r["recommended_max_items"] for sid, r in results.items()
            if r.get("verdict") == "lift" and sid in ids}
    print("\n建议放开：%s" % (lift or "无"))
    print("结果已写入 %s" % out_path)
    if lift:
        print("执行：python scripts/lift_max_items.py --sources %s --apply"
              % ",".join(sorted(lift)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())