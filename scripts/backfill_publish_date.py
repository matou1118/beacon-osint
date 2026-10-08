"""回填 publish_date：重抓缺日期的记录，只补日期字段。

为什么需要（2026-10-07 实测）
------------------------------
库内 918 条（6.08%）``publish_date`` 为空。按源分布很散：sn_swt 131、
mca_gov 69、sc_gs_gov 55、mo_gov 53…… 看起来像「抽不出来」，实测不是：

* 抽取器 ``quality.extract_fields()`` 是对的——现场抓 sn_swt 的
  12680，HTML 19998 字，抽到 ``2026-09-21``；
* 而库里存的那条**正文只有 310 字**。日期在 HTML 的模板区，被正文抽取
  （只保留文章主体）裁掉了，于是入库时既没有日期、正文里也找不到日期。

所以恢复日期只有一条路：**重新抓那个页面**，从完整 HTML 里抽。

为什么不顺手把正文也换掉：正文已经被下游（摘要、去重、字段）消费过，
换掉会让 ``content_fingerprint`` 对不上、摘要过期。本脚本只写
``publish_date`` 一个字段——正文截断是另一件事，要改得走采集链路的
正文抽取，那里已经有 ``min_content_chars`` 一类门禁，不该在这里旁路改。

**政治成本**：这是逐条重抓政府站页面。所以默认 ``--limit 50``、带
``--sleep`` 节流、必须显式 ``--apply``。918 条要抓完得跑若干轮，
不该一口气打完。

用法：
    python scripts/backfill_publish_date.py --dry-run
    python scripts/backfill_publish_date.py --limit 50 --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lighthouse.quality import extract_fields, load_config  # noqa: E402

DB = ROOT / "data" / "beacon.db"
CFG = load_config()
# UA 必须**纯 ASCII**：HTTP 头按 latin-1 编码，中文会直接抛
# UnicodeEncodeError，于是每条都「抓取失败」。2026-10-07 实测踩过：
# UA 写成「BeaconOSINT/0.1 (政策公告归档; 回填发布日期)」，
# 50 条全部 unfetchable，而同一批 URL 用 ASCII UA 抓得好好的。
# 请求头越朴素越好，不给站点判断依据。
UA = "Mozilla/5.0 (compatible; BeaconOSINT/0.1)"


def _fetch(url: str, timeout: int) -> str | None:
    """抓页面正文 HTML。失败返回 None——不猜、不用占位日期。"""
    import urllib.request

    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 400:
                return None
            raw = response.read()
    except Exception:                                    # noqa: BLE001
        return None
    # 政府站编码混乱，先按声明的编码解，失败退回 utf-8 宽松解。
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=50,
                    help="本轮最多回填多少条（默认 50；918 条要分多轮）")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="每次抓取之间的秒数（默认 1.0，对政府站友好）")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="真的写库")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, source_id, source_url, title FROM announcements "
        "WHERE publish_date IS NULL OR publish_date='' "
        "ORDER BY id LIMIT ?", (args.limit,)).fetchall()
    total_missing = con.execute(
        "SELECT COUNT(*) FROM announcements "
        "WHERE publish_date IS NULL OR publish_date=''").fetchone()[0]
    con.close()

    print(json.dumps({
        "total_missing": total_missing,
        "this_round": len(rows),
        "limit": args.limit,
        "apply": args.apply,
    }, ensure_ascii=False, indent=2))
    if args.dry_run or not args.apply:
        print("\ndry run: nothing fetched. re-run with --apply")
        return 0

    db = sqlite3.connect(DB, timeout=60)
    db.execute("PRAGMA busy_timeout=60000")
    filled = unchanged = unfetchable = nodate = 0
    for n, row in enumerate(rows, 1):
        html = _fetch(row["source_url"], args.timeout)
        if html is None:
            unfetchable += 1
            time.sleep(args.sleep)
            continue
        candidate = extract_fields(html, CFG).get("publish_date")
        if not candidate:
            nodate += 1
            time.sleep(args.sleep)
            continue
        try:
            db.execute("UPDATE announcements SET publish_date=? WHERE id=?",
                       (str(candidate), row["id"]))
            filled += 1
        except sqlite3.OperationalError as exc:
            # 采集调度器也在写同一张表，锁了就跳过这一条，别中断整轮。
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
        time.sleep(args.sleep)
        if n % 20 == 0:
            db.commit()
            print("  %d/%d 已处理（已补 %d）" % (n, len(rows), filled))
    db.commit()
    db.close()

    check = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    still = check.execute(
        "SELECT COUNT(*) FROM announcements "
        "WHERE publish_date IS NULL OR publish_date=''").fetchone()[0]
    total = check.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    check.close()
    print(json.dumps({
        "filled": filled, "already_had_none_on_page": nodate,
        "unfetchable": unfetchable,
        "still_missing": still,
        "publish_date_rate": round((total - still) / total, 4),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())