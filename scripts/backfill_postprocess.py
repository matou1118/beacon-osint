"""存量记录补跑后处理：交叉验证 → 热度聚簇 → 写库。

为什么需要它
------------
lighthouse 链路 2026-10-01 之前一直没跑后处理（链条本身在 ``main.py`` 里，
lighthouse 绕过了）。所以库里 9467 条记录里：

* ``corroboration_status`` 大部分是默认值 ``single_source``——从没算过
* ``media_cluster_id`` 全空
* ``heat_topics`` **0 行**

接上链条只对**新采的**记录生效。这 9467 条要补，就靠这个脚本。

分批的取数方式是有讲究的
------------------------
交叉验证的分组键是 ``(地区, category_l1, 发布月份, 主题词)``
（``rules/corroboration.py:51``）。所以不能随机取 500 条——随机取出来的 500 条
几乎不同组，交叉验证一条也配不上，等于白跑。这里按**月份**为批，
让同一批里的记录尽量落在同一个分组键空间里。

聚簇则受 ``cluster_window_hours``（默认 72 小时）约束，跨月的记录本来就不该
聚成一个事件。以 ``now`` 为准回算时，用每批**最新那条的发布日期**当参照，
比用"今天"更贴近那批数据当时的语境。

安全
----
* 默认 ``--dry-run``，只报不写
* 真写时逐批提交，中断不留半个事务
* 附件不重下：``process_attachments`` 会发网络请求，9467 条全量重下不现实，
  且附件表本身已有 264 行，是旧链路写的
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lighthouse.postprocess import analyze, load_rules, source_index  # noqa: E402
from lighthouse.run import load_sources  # noqa: E402

DB = _ROOT / "data" / "beacon.db"

# 采集互斥锁。回填会写 9467 条记录，不持锁就是在和采集抢同一个库。
#
# 代价是实测的：2026-10-01 傍晚没持锁跑全量回填，那 20 分钟里调度器连续异常
# 退出 62 次（watchdog 每 20 秒拉一次，每次都秒退）。SQLite 扛住了没坏数据，
# 但两个写者同时改 source_sync_state 会互相覆盖，属于那种不报错只出错的情况。
# ``collectors.lock`` 的说明里写明了这一类静默错误。
from collectors.lock import CollectLockBusy, collect_lock  # noqa: E402

# 与 rules.corroboration 用的字段一致。
# issuing_body / source_type / summary 不在列里——它们只存在 extra JSON 中，
# 下面读出来后再 setdefault 补回。
COLUMNS = (
    "id", "source_id", "source_url", "title", "content", "content_hash",
    "publish_date", "category_l1", "category_l2", "target_industry",
    "target_group", "region_province", "region_city", "gov_level",
    "needs_review", "media_type", "media_tags",
    "corroboration_status", "extra",
)


def log(message: str) -> None:
    print(message, flush=True)


def read_batch(con: sqlite3.Connection, since: str, until: str) -> list[dict[str, Any]]:
    """读一个月的记录，还原成 postprocess 认识的 record dict。"""
    rows = con.execute(
        f"SELECT {', '.join(COLUMNS)} FROM announcements "
        "WHERE publish_date >= ? AND publish_date < ? ORDER BY publish_date",
        (since, until),
    ).fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(zip(COLUMNS, row))
        try:
            extra = json.loads(record.pop("extra") or "{}") or {}
        except (TypeError, ValueError):
            extra = {}
        # extra 里存着采集期才有的字段（source_type / issuing_body / summary…）。
        # 不还原的话交叉验证拿不到摘要文本，摘要为空会让主题词匹配全落空。
        for key, value in extra.items():
            record.setdefault(key, value)
        records.append(record)
    return records


def months(con: sqlite3.Connection, limit: int | None) -> list[tuple[str, str]]:
    """列出有数据的月份区间，按新到旧。

    ``--limit-months N`` 取的是**距今最近的 N 个自然月**，不是字符串排序最大的 N 个。
    库里确实有 2027/2029 的未来日期记录（各 1 条，日期抽取错误），按字符串排
    DESC 会正好挑中它们——实测 ``--limit-months 2`` 只跑出 2 条，全是异常记录。
    """
    rows = con.execute(
        "SELECT substr(publish_date, 1, 7) AS ym, COUNT(*) AS n FROM announcements "
        "WHERE publish_date IS NOT NULL AND length(publish_date) >= 7 "
        "GROUP BY ym"
    ).fetchall()
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    spans: list[tuple[str, str]] = []
    for ym, _count in rows:
        year, month = int(ym[:4]), int(ym[5:7])
        nxt = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
        spans.append((f"{ym}-01", nxt.strftime("%Y-%m-%d"), ym))
    # 未来日期排最后，不占用 --limit-months 的名额，但仍会被全量跑覆盖。
    spans.sort(key=lambda item: (item[2] > this_month, item[2]), reverse=True)
    if limit:
        past = [item for item in spans if item[2] <= this_month][:limit]
        future = [item for item in spans if item[2] > this_month]
        spans = past + future
    return [(since, until) for since, until, _ym in spans]


def _anchor(records: list[dict[str, Any]]) -> datetime:
    """聚簇的时间参照：取这批最新一条的发布日期。

    用 datetime.now() 会让 2023 年的数据因为超出 72 小时窗口而一个事件都聚不出，
    于是「跑完了但 heat_topics 还是空的」。参照点应该跟着数据走。
    """
    stamps = sorted(str(r.get("publish_date") or "") for r in records
                    if str(r.get("publish_date") or ""))
    return datetime.fromisoformat(stamps[-1].replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc) if stamps else datetime.now(timezone.utc)


def run(limit_months: int | None, dry_run: bool, rebuild: bool) -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    total_rows = con.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    con.close()

    spans = months(sqlite3.connect(f"file:{DB}?mode=ro", uri=True), limit_months)
    log(f"库内 {total_rows} 条 | 待补跑 {len(spans)} 个月份"
        f"{'（--dry-run，不写库）' if dry_run else ''}")

    rules = load_rules()
    sources = load_sources(statuses=())
    index = source_index(sources)
    log(f"源注册表 {len(index)} 个；media_heat 配了 {len(rules.get('media_heat') or {})} 项")

    if rebuild:
        from storage.db import Database

        db = Database(str(DB))
        try:
            from graph_service import rebuild_graph

            log("重建图谱…")
            summary = rebuild_graph(db)
            log(f"  图谱 {summary}")
        finally:
            db.close()

    if dry_run:
        for since, until in spans:
            batch = read_batch(sqlite3.connect(f"file:{DB}?mode=ro", uri=True), since, until)
            outcome = analyze(batch, rules, now=_anchor(batch), sources=sources)
            cor = outcome["corroboration"]
            log(f"  {since[:7]}  {len(batch):5d} 条 -> 交叉 {cor['corroborated']:4d} "
                f"| 事件 {len(outcome['clusters']):3d}")
        return 0

    try:
        grand = _write_batches(spans, rules, sources)
    except CollectLockBusy as exc:
        # 拿不到锁就不排队。回填是维护动作，采集是主线，不该互相等。
        log(f"采集器正在运行，本次跳过（不打断采集）：{exc}")
        return 0

    log(f"完成 {grand['records']} 条 / {grand['months']} 个月 | "
        f"交叉验证 {grand['corroborated']} | 事件 {grand['clusters']}")
    return 0


def _write_batches(spans, rules, sources) -> dict[str, Any]:
    """持采集锁逐批回填并写库。"""
    from storage.db import Database

    grand = {"records": 0, "corroborated": 0, "clusters": 0, "months": 0}
    with collect_lock("backfill_postprocess"):
        db = Database(str(DB))
        started = time.time()
        try:
            for index_, (since, until) in enumerate(spans, 1):
                batch = read_batch(sqlite3.connect(f"file:{DB}?mode=ro", uri=True),
                                   since, until)
                if not batch:
                    continue
                outcome = analyze(batch, rules, now=_anchor(batch), sources=sources)
                cor = outcome["corroboration"]
                if outcome["clusters"]:
                    db.upsert_heat_topics(outcome["clusters"], since[:7])
                # 必须走 upsert_announcements 回写 corroboration_* / media_cluster_id。
                # save_field_extractions 只写 field_extractions 留痕表，不碰
                # announcements——只调它的话交叉验证算完只留在内存里，
                # 表里的 corroboration_status 一个字都不会变（实测跑完全量仍是 114）。
                db.upsert_announcements(outcome["records"])
                db.save_field_extractions(outcome["records"])
                grand["records"] += len(batch)
                grand["corroborated"] += cor["corroborated"]
                grand["clusters"] += len(outcome["clusters"])
                grand["months"] += 1
                log(f"  [{index_}/{len(spans)}] {since[:7]} {len(batch):5d} 条 -> "
                    f"交叉 {cor['corroborated']:4d} | 事件 {len(outcome['clusters']):3d} "
                    f"| {round(time.time() - started, 1)}s")
        finally:
            db.close()
    return grand


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="存量记录补跑交叉验证和热度聚簇")
    parser.add_argument("--limit-months", type=int, default=None,
                        help="只补最近 N 个月（默认全部）")
    parser.add_argument("--dry-run", action="store_true", help="只报不写")
    parser.add_argument("--rebuild-graph", action="store_true", help="先重建图谱")
    args = parser.parse_args(argv)
    return run(args.limit_months, args.dry_run, args.rebuild_graph)


if __name__ == "__main__":
    raise SystemExit(main())
