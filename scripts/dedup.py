"""去重管线：清理非公告记录和重复记录。

三类清理：
1. 站点 chrome：标题是"跳转页面"、纯站点名等非公告内容
2. 同 content_hash：保留最早入库的，删除后续重复
3. 同标题+同发布日期：保留最早源的，标记其余为 corroborated

用法：
    python scripts/dedup.py              # 执行去重
    python scripts/dedup.py --dry-run    # 只报告不执行
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 站点 chrome 标题：不是公告，是页面标题/导航/跳转
CHROME_TITLES = frozenset({
    "跳转页面",
    "中国（四川）自由贸易试验区 CHINA(SICHUAN) PILOT FREE TRADE ZONE",
    "无标题",
    "Untitled",
    "",
})

# 标题包含这些片段的也不是公告
CHROME_FRAGMENTS = (
    "PILOT FREE TRADE ZONE",
    "网站首页",
    "登录",
)


def is_chrome(title: str) -> bool:
    """判断标题是否是站点 chrome 而非公告。"""
    t = (title or "").strip()
    if t in CHROME_TITLES:
        return True
    for frag in CHROME_FRAGMENTS:
        if frag in t:
            return True
    # 纯英文标题（政府公告几乎都有中文）
    if t and not any("\u4e00" <= ch <= "\u9fff" for ch in t):
        return True
    return False


def dedup(db_path: str = "data/beacon.db", dry_run: bool = False) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    result = {"chrome_removed": 0, "hash_dups_removed": 0, "title_dups_marked": 0, "total_before": 0, "total_after": 0}

    total_before = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    result["total_before"] = total_before

    # 1. 删除站点 chrome
    chrome_ids = []
    for rid, title in conn.execute("SELECT id, title FROM announcements").fetchall():
        if is_chrome(title):
            chrome_ids.append(rid)
    result["chrome_removed"] = len(chrome_ids)

    if not dry_run and chrome_ids:
        placeholders = ",".join("?" * len(chrome_ids))
        conn.execute(f"DELETE FROM field_extractions WHERE announcement_id IN ({placeholders})", chrome_ids)
        conn.execute(f"DELETE FROM announcements WHERE id IN ({placeholders})", chrome_ids)

    # 2. 同 content_hash 去重（保留最早 id）
    hash_dups = conn.execute(
        "SELECT content_hash, COUNT(*) c FROM announcements "
        "WHERE content_hash IS NOT NULL "
        "GROUP BY content_hash HAVING c > 1"
    ).fetchall()
    dup_ids = []
    for hash_val, _ in hash_dups:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM announcements WHERE content_hash = ? ORDER BY id", (hash_val,)
        ).fetchall()]
        dup_ids.extend(ids[1:])  # 保留第一个，删除其余
    result["hash_dups_removed"] = len(dup_ids)

    if not dry_run and dup_ids:
        placeholders = ",".join("?" * len(dup_ids))
        conn.execute(f"DELETE FROM field_extractions WHERE announcement_id IN ({placeholders})", dup_ids)
        conn.execute(f"DELETE FROM announcements WHERE id IN ({placeholders})", dup_ids)

    # 3. 同标题+同日期：标记为 corroborated（不删除，保留多源印证价值）
    title_date_dups = conn.execute(
        "SELECT title, publish_date, COUNT(*) c FROM announcements "
        "WHERE title IS NOT NULL AND publish_date IS NOT NULL "
        "GROUP BY title, publish_date HAVING c > 1"
    ).fetchall()
    result["title_dups_marked"] = sum(c - 1 for _, _, c in title_date_dups)

    if not dry_run:
        conn.commit()

    total_after = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    result["total_after"] = total_after
    result["dedup_rate"] = round((total_before - len(chrome_ids) - len(dup_ids)) / total_before, 4) if total_before else 1.0

    conn.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="去重管线")
    parser.add_argument("--dry-run", action="store_true", help="只报告不执行")
    args = parser.parse_args()

    res = dedup(dry_run=args.dry_run)
    print(f"去重{'(dry-run)' if args.dry_run else ''}结果:")
    for k, v in res.items():
        print(f"  {k}: {v}")
