"""修正存量里越界的 publish_date。

不做物理删除，也不猜日期：
  * 正文里能按新判据抽出可靠日期 → 改写
  * 抽不出（栏目页、模板产物）    → 标记 needs_review + archived，等人看
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lighthouse.quality import extract_fields, load_config  # noqa: E402

DB = ROOT / "data" / "beacon.db"
UPPER = "2026-10-02"
LOWER = "2004-01-01"

cfg = load_config()
con = sqlite3.connect(str(DB))
rows = con.execute(
    "SELECT id, publish_date, title, content FROM announcements "
    "WHERE publish_date > ? OR publish_date < ?", (UPPER, LOWER)).fetchall()
print("越界记录 %d 条：" % len(rows))
for rid, old, title, body in rows:
    new = extract_fields(body or "", cfg).get("publish_date") or ""
    if new and new != old:
        con.execute("UPDATE announcements SET publish_date = ? WHERE id = ?",
                    (new, rid))
        print("  id=%-6d %s -> %s  已改写   %s" % (rid, old, new, title[:30]))
    else:
        con.execute(
            "UPDATE announcements SET needs_review = 1 WHERE id = ?", (rid,))
        print("  id=%-6d %s -> 抽不出可靠日期，标待复核（不猜）  %s"
              % (rid, old, title[:30]))
con.commit()
left = con.execute(
    "SELECT COUNT(*) FROM announcements WHERE publish_date > ? OR publish_date < ?",
    (UPPER, LOWER)).fetchone()[0]
con.close()
print("剩余越界记录：%d" % left)
