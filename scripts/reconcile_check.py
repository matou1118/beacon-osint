"""每轮核对：源列表页 vs 库内记录。

判据（三层，逐层收窄）：

  L1  列表页有 N 条合规链接，库里有 M 条 -> 若 M < N 且 N-M 里有「URL 不在库中
      但也不在 known-url 集合里」的，说明**抽取/入库丢了**。
  L2  源报「all N already known」但库里按 source_id 查不到那些 URL
      -> 入库时 source_id 写错了，或入库被静默丢弃。
  L3  抽取阶段 dropped 的条数里，有多少是「其实合格」的（误杀）。

这是纯读，不改任何东西、不碰 collect.lock —— 采集照常跑。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "beacon.db"
SRC = ROOT / "config" / "sources.yaml"
SHADOW = ROOT / "data" / "runtime" / "lighthouse"

CON = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
CON.row_factory = sqlite3.Row

HEAD_CACHE = 60          # 缓存里每个源留多少条列表链接（够判头部缺口）


def q(sql, *args):
    try:
        return CON.execute(sql, args).fetchone()[0]
    except sqlite3.Error:
        return None


def known_urls():
    # 列名是 source_url，不是 url。
    return {r[0] for r in CON.execute("SELECT source_url FROM announcements")}


def source_stats():
    out = {}
    for r in CON.execute(
            "SELECT source_id, COUNT(*) n, MAX(fetched_at) last "
            "FROM announcements GROUP BY source_id"):
        out[r["source_id"]] = {"n": r["n"], "last": r["last"]}
    return out


def url_host(u: str) -> str:
    return (urlparse(u).hostname or "").lower()


def _head_gap(source_id: str, head: int = 50,
              max_age_s: int = 6 * 3600) -> tuple[int, int, list[str]] | None:
    """列表页**前 head 条**里有多少条没入库。取不到返回 None。

    为什么不比总数：政府站列表页按时间倒序，最新的在**最前面**。采完头部之后，
    ``early stop after N known urls`` 会主动停在已入库的旧账上不再往下翻——
    这是省预算 + 少敲门的**设计**，不是丢数据。2026-10-06 实测：js_gxt 报
    「列表 999 条、库内 484 条，丢了 515 条」，实测位置 0..483 全部已入库，
    未入库的 515 条全在老尾里。一条都没丢，比总数却报成丢了 515 条。
    所以判据只能是**头部有没有新的没拿到**。

    顺带把那几条没入库的 URL 带出来：只报个数没法判断丢的是公告还是
    「搜索页 / #锚点 / 归档页」这种本来就不该入库的链接（实测 hb_ny 头部
    缺的 5 条全是 ``#test`` 锚点）。

    **必须带缓存** —— 核对每 20 分钟跑一次，每次都现抓 30+ 个政府站
    等于自己制造一轮采集：既慢又加重站点负担（本项目 §3.3 的频率红线）。

    缓存落 data/runtime/lighthouse/reconcile_cache.json，默认 6 小时过期。
    缓存里存**链接**而不是条数：缺口要对库现算，库里随时会涨。
    """
    import json as _json
    import time as _time

    cache_path = SHADOW / "reconcile_cache.json"
    try:
        cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    now = _time.time()
    hit = cache.get(source_id)
    links = None
    if hit and now - float(hit.get("ts", 0)) < max_age_s \
            and isinstance(hit.get("links"), list):
        links = list(hit["links"])
    if links is None:
        try:
            import yaml

            from lighthouse.routes import build_collector

            src = yaml.safe_load(SRC.read_text(encoding="utf-8")) or {}
            row = next((s for s in src.get("sources") or []
                        if str(s.get("id")) == source_id), None)
            if not row:
                return None
            cfg = dict(row)
            cfg["archive_pages"] = 1      # 只要头部，30 页太慢
            cfg["playwright_fallback"] = False
            c = build_collector(cfg)
            try:
                links = list(c.fetch_list() or [])
            finally:
                c.close()
        except Exception:                               # noqa: BLE001
            return None
        cache[source_id] = {"ts": now, "links": links[:HEAD_CACHE]}
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(_json.dumps(cache, ensure_ascii=False),
                                  encoding="utf-8")
        except OSError:
            pass

    head = list(links)[:head]
    if not head:
        return 0, 0, []
    have = set(known_urls())
    miss = [u for u in head if u not in have]
    return len(head), len(miss), miss[:3]


def main(as_json: bool = False):
    import yaml

    cfg = yaml.safe_load(SRC.read_text(encoding="utf-8")) or {}
    sources = [s for s in cfg.get("sources") or []
               if s.get("status") == "enabled" and s.get("enabled")]
    stats = source_stats()
    known = known_urls()

    # 库里有没有 source_id 缺失的（入库时没写来源）
    null_src = q("SELECT COUNT(*) FROM announcements WHERE source_id IS NULL "
                 "OR source_id=''")
    total = q("SELECT COUNT(*) FROM announcements")

    print("=" * 72)
    print(" 采集核对  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 72)
    print(" 库内 %s 条 | 有 source_id 的 %s 条 | source_id 为空 %s 条"
          % (total, total - (null_src or 0), null_src))
    print()

    # ── 逐源核对 ──────────────────────────────────────────────────────
    print("【逐源核对】%s" % ("%-16s %-6s %-8s %-8s %s"
                            % ("源", "库内", "最近入库", "状态", "备注")))
    print("  " + "-" * 68)
    problems = []
    for s in sources:
        sid = str(s.get("id") or "")
        st = stats.get(sid)
        n = st["n"] if st else 0
        last = (st["last"] or "")[:16] if st else "-"
        note = ""
        if st is None:
            note = "!! 库内一条都没有"
            problems.append((sid, "库内无任何记录"))
        else:
            age_h = _age_hours(st["last"])
            if age_h is not None and age_h > 48:
                # 零入库**不等于**故障。要区分两种情况：
                #   a) 列表页头部（最新的那批）全都拿到了 —— 采干/旧账，正常
                #   b) 头部有没入库的 —— 真丢数据，要报
                # 判据只看**头部**。比总数会把「故意不翻的旧账」算成丢失：
                # 2026-10-06 实测 js_gxt 被报「丢了 515 条」，实测那 515 条
                # 全在老尾，位置 0..483 一条不差地都在库里。
                gap = _head_gap(sid)
                if gap is None:
                    note = "%.0f 小时零入库（列表页取不到，无法判定）" % age_h
                else:
                    listed, missing, samples = gap
                    if missing == 0:
                        note = ("%.0f 小时零入库 —— 采干（头部 %d 条全在库，"
                                "库内共 %d 条）" % (age_h, listed, n))
                    else:
                        note = ("!! %.0f 小时零入库，列表页头部 %d 条有 %d 条"
                                "没入库：%s" % (age_h, listed, missing,
                                              " ".join(samples)))
                        problems.append(
                            (sid, "列表页头部 %d 条、%d 条没入库（%s）"
                             % (listed, missing, " ".join(samples))))
        print("  %-16s %-6s %-20s %-8s %s" % (sid, n, last,
                                             "采干" if "采干" in note else "", note))

    # ── L3：最近一轮的 dropped 明细 ───────────────────────────────────
    print()
    print("【最近各轮 dropped 明细抽查】")
    reports = sorted(SHADOW.glob("shadow-*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    for p in reports:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dd = d.get("dropped_detail") or []
        if not dd:
            continue
        by_reason = {}
        for x in dd:
            r = str(x.get("reason", "?"))[:34]
            by_reason[r] = by_reason.get(r, 0) + 1
        print("  %s  共 %d 条" % (p.stem[-6:], len(dd)))
        for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1])[:6]:
            print("      %-36s %d" % (r, n))

    # ── 结论 ─────────────────────────────────────────────────────────
    print()
    if problems:
        print("【发现 %d 个问题】" % len(problems))
        for sid, why in problems:
            print("  ! %-18s %s" % (sid, why))
    else:
        print("【核对通过】无源零入库，抽取/入库链路未见丢失。")
    if as_json:
        return {
            "total": total,
            "null_source": null_src,
            "sources": len(sources),
            "problems": [{"source_id": sid, "why": why} for sid, why in problems],
        }
    return 0


def _age_hours(ts: str) -> float | None:
    """时间戳距今多少小时。

    时间戳是 **UTC**（storage/models.py 的 ``utcnow()`` 写的），所以基准也必须
    是 UTC。2026-10-06 实测踩过：用 ``datetime.now()``（本地）减 UTC 戳，本机
    UTC+8 → 每个「零入库小时数」虚高 8 小时。后果是判据整体前移：真的刚采过
    0.1 小时的源被报成「157 小时零入库」，而 48 小时阈值实际 40 小时就触发 ——
    报告里全是假的「采干」和假的「丢数据」。
    """
    if not ts:
        return None
    import datetime as _dt

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            when = _dt.datetime.strptime(str(ts)[:26], fmt)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc)
                - when).total_seconds() / 3600.0
    return None


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(main(as_json=True), ensure_ascii=False))
    else:
        raise SystemExit(main())
