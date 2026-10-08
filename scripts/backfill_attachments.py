"""补齐 attachments：把库内已存的 attachment_links 真正抓一遍。

为什么需要（2026-10-07 实测）
------------------------------
``attachments`` 表 264 行，**全部是 2026-09-26/27 旧链路（``main.py``）写进去的**。
而 ``lighthouse`` 链路是采集主力，它把附件链接存进
``extra["attachment_links"]``（``lighthouse/run.py``），却**从不下载**——
``lighthouse/postprocess.py`` 的 ``persist()`` 注释就写着「附件下载不在这里」。

结果：库内 15091 条里 1005+ 条带着附件链接，``attachments`` 表却一条新的没有。
``data_health.py`` 的 ``attachment_download_rate`` 因此是 0.0175，低于 0.03 的门槛。

这个脚本只做一件事：读库里的 ``attachment_links`` → 下载 → 抽文本 → 写
``attachments`` 表。复用 ``extractors.attachment_downloader``（下载+抽文本+
立即删临时文件）和 ``Database.save_attachments``（去重写入），不重写任何管线。

政治成本要算清楚：这是**逐个附件的 HTTP 请求**，对政府站是真实打扰。所以
  * 默认 ``--limit`` 保守，且只抓「还没有 attachments 行」的记录（不重复劳动）；
  * 有 ``--sleep`` 节流，默认 0.5 秒；
  * ``--dry-run`` 先看计划。

用法：
    python scripts/backfill_attachments.py --dry-run
    python scripts/backfill_attachments.py --limit 200 --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from extractors.attachment_downloader import (  # noqa: E402
    MAX_ATTACHMENTS_PER_RECORD,
    _url_hash,
    download_and_extract,
)
from storage.db import Database  # noqa: E402

DB = ROOT / "data" / "beacon.db"


def _pending(limit: int, only_missing: bool) -> list[sqlite3.Row]:
    """还没抓过附件的记录。

    **LIMIT 必须作用在「真的有附件链接」的那批上，不能作用在扫描上。**
    2026-10-07 实测踩过：先用 ``ORDER BY a.id LIMIT 100`` 取前 100 条，再在
    Python 里过滤 ``attachment_links`` 非空 —— 而 id 靠前的记录大多没有附件，
    于是 100 条里只剩 16 条能抓，报表显示「records_pending: 16」像是快干完了，
    实际还有 6000 多条没抓。

    所以改成先在 SQL 里用 ``json_extract`` 过滤，再 LIMIT。
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = ("SELECT a.id, a.source_id, a.source_url, a.extra "
           "FROM announcements a "
           "WHERE json_array_length(json_extract(a.extra, '$.attachment_links')) > 0")
    if only_missing:
        sql += (" AND NOT EXISTS (SELECT 1 FROM attachments t "
                "WHERE t.announcement_id = a.id)")
    sql += " ORDER BY a.id LIMIT ?"
    return con.execute(sql, (limit,)).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=100,
                    help="最多处理多少条记录（默认 100，逐附件发 HTTP 请求）")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="每个附件之间的秒数（默认 0.5，对政府站友好）")
    ap.add_argument("--dry-run", action="store_true", help="只看计划")
    ap.add_argument("--apply", action="store_true", help="真的写库")
    ap.add_argument("--include-done", action="store_true",
                    help="连已有 attachments 行的记录也重抓（默认跳过）")
    args = ap.parse_args()

    rows = _pending(args.limit, only_missing=not args.include_done)
    links = sum(min(MAX_ATTACHMENTS_PER_RECORD,
                    len(json.loads(r["extra"] or "{}")["attachment_links"]))
                for r in rows)
    print(json.dumps({
        "records_pending": len(rows),
        "attachments_to_fetch": links,
        "limit": args.limit,
        "apply": args.apply,
    }, ensure_ascii=False, indent=2))
    if args.dry_run or not args.apply:
        print("\ndry run: nothing fetched. re-run with --apply")
        return 0
    if not rows:
        print("没有待抓的附件。")
        return 0

    database = Database(str(DB))
    now = datetime.now(timezone.utc)
    fetched = extracted = failed = 0
    batch: list[dict] = []

    def flush(items: list[dict]) -> None:
        """写一批，遇到锁就退避重试。

        采集调度器和本脚本同时写同一个库。Database 开了 WAL + busy_timeout=30s，
        但 SQLAlchemy 的 autoflush 会在**查询过程中**触发 INSERT，那一刻可能
        正撞上调度器的事务 —— 2026-10-07 实测直接抛
        ``(sqlite3.OperationalError) database is locked``，而且是在
        ``Query-invoked autoflush`` 上，说明 busy_timeout 没能兜住。

        所以这里不指望一次成功：失败就等一下重试，重试到底仍然失败才放弃
        （并如实计数，不假装成功）。
        """
        for attempt in range(5):
            try:
                database.save_attachments(items)
                return
            except OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                wait = 2 ** attempt
                print("     库被占用，等 %ds 重试（%d/5）" % (wait, attempt + 1))
                time.sleep(wait)
        print("     %d 条放弃写入：库一直被占用" % len(items))

    for n, row in enumerate(rows, 1):
        urls = json.loads(row["extra"] or "{}")["attachment_links"]
        texts = []
        for url in urls[:MAX_ATTACHMENTS_PER_RECORD]:
            result = download_and_extract(url)
            fetched += 1
            if result is None:
                failed += 1
            else:
                file_type, text = result
                if text:
                    extracted += 1
                texts.append({"url": url, "url_hash": _url_hash(url),
                              "file_type": file_type, "text": text,
                              "text_length": len(text)})
            time.sleep(args.sleep)
        batch.append({"source_id": row["source_id"],
                      "source_url": row["source_url"],
                      "attachment_texts": texts})
        # 批次要小：每批的写事务和调度器重叠的时间越长，撞锁的概率越高。
        if len(batch) >= 5:
            flush(batch)
            batch = []
            print("  %d/%d 已写库" % (n, len(rows)))
    if batch:
        flush(batch)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    total = con.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    con.close()
    print(json.dumps({
        "fetched": fetched, "extracted": extracted, "failed": failed,
        "attachments_table_rows": total,
        "attachment_download_rate": round(total / 15091, 4),
        "at": now.isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())