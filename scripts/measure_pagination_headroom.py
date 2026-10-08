"""测量「深翻页能不能带来新数据」—— 不入库、不抢采集锁。

为什么需要这个
--------------
2026-10-05 实测：库 14110 条，10-04 入库 0 条，10-05 只有 16 条，
135/143 个源在退避中。``ndrc_gov`` 报 ``all 93 list urls already known``。

两种可能，处理方式完全相反：
  A. 列表页就那么多 URL（真的采干了）→ 只能加新源
  B. 列表页有分页，只是当前深度没翻到  → 深翻历史页就能补产出

靠猜会白干几小时。这个脚本直接数：同一个源在 depth=N 时能枚举出多少列表 URL，
其中多少条不在库里（N 条就是"翻这么深能多拿多少条"）。

只读，不写库，因此**不需要采集锁**，可以和通宵采集并行跑。
用法：
    python scripts/measure_pagination_headroom.py                    # 默认一批源
    python scripts/measure_pagination_headroom.py --sources a,b,c --depths 3,12,30
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

DB = ROOT / "data" / "beacon.db"
SOURCES = ROOT / "config" / "sources.yaml"


def _load_sources() -> dict[str, dict]:
    data = yaml.safe_load(SOURCES.read_text(encoding="utf-8")) or {}
    return {str(s.get("id")): s for s in (data.get("sources") or []) if s.get("id")}


def _known_urls() -> set[str]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        # 列名是 source_url 不是 url —— 实测踩过：
        # announcements 表 48 列里没有 url，写 url 会直接
        # OperationalError: no such column: url
        return {str(r[0]) for r in con.execute("SELECT source_url FROM announcements")}
    finally:
        con.close()


def _known_counts() -> dict[str, int]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return {str(s): int(n) for s, n in con.execute(
            "SELECT source_id, COUNT(*) FROM announcements GROUP BY source_id")}
    finally:
        con.close()


def probe(source: dict, depth: int, known: set[str]) -> dict:
    """在指定翻页深度下枚举列表 URL，返回总数与未入库数。"""
    from lighthouse.run import build_collector, Ladder, with_defaults

    cfg = yaml.safe_load(
        (ROOT / "lighthouse" / "config" / "web_collector_config.yaml")
        .read_text(encoding="utf-8")) or {}

    src = dict(source)
    src["archive_pages"] = depth            # 显式覆盖，绕开默认地板
    ladder = Ladder(cfg, None)
    src = with_defaults(src, cfg, depth)
    src["archive_pages"] = depth            # with_defaults 可能又被地板抬回去
    collector = build_collector(src, ladder.levels)
    ladder.collector = collector
    started = time.time()
    try:
        links = list(collector.fetch_list() or [])
    except Exception as exc:                                   # noqa: BLE001
        return {"depth": depth, "error": f"{type(exc).__name__}: {exc}"[:120]}
    finally:
        try:
            collector.close()
        except Exception:                                      # noqa: BLE001
            pass
    uniq = {str(getattr(x, "url", x)) if not isinstance(x, str) else x for x in links}
    fresh = [u for u in uniq if u not in known]
    return {"depth": depth, "total": len(uniq), "fresh": len(fresh),
            "seconds": round(time.time() - started, 1), "sample": fresh[:3]}


def _probe_star(args: tuple[str, dict, int, frozenset]) -> tuple[str, dict]:
    sid, src, depth, known = args
    return sid, probe(src, depth, set(known))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="",
                    help="逗号分隔的源 id；默认自动挑库里记录最多的几个")
    ap.add_argument("--depths", default="3,12,30")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--json-out", default="",
                    help="把测量结果写成 JSON（apply_depth_headroom.py 消费）")
    ap.add_argument("--workers", type=int, default=6,
                    help="并发数。串行实测 143 源 × 2 深度要几小时，"
                         "而每个源都是独立网络请求，没有共享状态 —— "
                         "唯一要小心的是 Playwright 渲染进程，"
                         "所以默认值和生产采集一致（6），别开更大")
    args = ap.parse_args()

    if not DB.exists():
        print("找不到 %s，先跑一轮采集" % DB)
        return 1

    sources = _load_sources()
    counts = _known_counts()
    if args.sources:
        ids = [s.strip() for s in args.sources.split(",") if s.strip()]
    else:
        ids = [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])
               if s in sources][:args.top]
    depths = [int(d) for d in args.depths.split(",") if d.strip()]

    known = _known_urls()
    print("库内 URL %d 个，公告 %d 条" % (len(known), sum(counts.values())))
    print("=" * 78)

    verdict: list[tuple[str, int, int]] = []
    per_source: dict[str, dict] = {}

    # 并发测量：每个 (源, 深度) 是独立网络请求，没有共享状态。
    #
    # 串行实测跑不动：143 源 × 2 深度，慢源单个 170 秒，总量以小时计。
    # 注意并发**不要超过 6** —— 部分源要起 Playwright 渲染，和生产采集
    # 抢同样的浏览器资源；测量本身只读、不抢采集锁，可以和生产并行。
    from concurrent.futures import ThreadPoolExecutor

    jobs: list[tuple[str, dict, int, frozenset]] = []
    for sid in ids:
        src = sources.get(sid)
        if not src:
            print("\n%s 不在 sources.yaml，跳过" % sid)
            continue
        for d in depths:
            jobs.append((sid, src, d, frozenset(known)))

    print("并发测量 %d 个任务（%d 源 × %d 深度，%d 并发）…"
          % (len(jobs), len(ids), len(depths), args.workers))
    results: dict[tuple[str, int], dict] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for sid, res in pool.map(_probe_star, jobs):
            results[(sid, res.get("depth", 0))] = res
            done += 1
            print("  [%d/%d] %s %s" % (done, len(jobs), sid,
                                       ("失败:" + res["error"]) if res.get("error")
                                       else ("%d URL / 新 %d" % (res["total"], res["fresh"]))))
            sys.stdout.flush()

    for sid in ids:
        src = sources.get(sid)
        if not src:
            continue
        print("\n%s（库内 %d 条，配置 archive_pages=%s）  %s"
              % (sid, counts.get(sid, 0), src.get("archive_pages"),
                 str(src.get("url"))[:46]))
        prev_total = None
        probes: list[dict] = []
        for d in depths:
            r = results.get((sid, d)) or {"error": "未执行"}
            if r.get("error"):
                print("   depth %-3d 失败：%s" % (d, r["error"]))
                probes.append({"depth": d, "error": r["error"]})
                continue
            delta = "" if prev_total is None else "  (+%d URL)" % (r["total"] - prev_total)
            print("   depth %-3d 列表 URL %4d | 未入库 %4d | 用时 %5.1fs%s"
                  % (d, r["total"], r["fresh"], r["seconds"], delta))
            if r["fresh"]:
                print("        例：%s" % r["sample"][0])
            prev_total = r["total"]
            verdict.append((sid, d, r["fresh"]))
            probes.append({"depth": d, "total": r["total"], "fresh": r["fresh"],
                           "seconds": r["seconds"], "sample": r["sample"]})
        per_source[sid] = {"current_archive_pages": src.get("archive_pages"),
                           "url": src.get("url"), "known_records": counts.get(sid, 0),
                           "probes": probes}

    print()
    print("=" * 78)
    has_room = [v for v in verdict if v[2] > 0]
    if has_room:
        print("★ 有增量空间（深翻页能拿到库外 URL）：")
        for sid, d, n in sorted(has_room, key=lambda x: -x[2])[:12]:
            print("   %-18s depth=%-3d 未入库 %d" % (sid, d, n))
    else:
        print("★ 全部为 0：列表页已采干，加深翻页没用 —— 要恢复产出只能加新源。")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"generated_at": datetime.now().isoformat(timespec="seconds"),
             "depths": depths,
             "known_urls": len(known),
             "known_records": sum(counts.values()),
             "sources": per_source},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n报告已写入 %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
