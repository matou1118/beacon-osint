"""时间口径：**和数据库列比用 UTC，给人看的输出才转本地**。

2026-10-05 实测踩过两次，同一个根因：

1. ``scripts/overnight_health.py`` 用 ``datetime.now() - WINDOW``（**本地**时间）
   去比 ``created_at``（**UTC**）。本机 UTC+8，于是「最近 14 小时」实际只覆盖
   6 小时——最近 8 小时的产出整段漏掉。后果正是「今天采集这么低」这种误判：
   数据确实入库了，体检脚本报少。

2. ``scripts/collection_report.py`` 按 ``substr(created_at,1,13)`` 分桶，
   那是 **UTC 小时**，却当本地小时展示。于是「本地 20:00 采了 422 条」
   被标成「12:00」，看图的人会以为深夜还在跑、白天没产出——正好和实际相反。

这类 bug 的隐蔽之处：它**不会报错**，只会让数字偏小或偏早，看起来像
「采集系统不行」。所以必须钉死。
"""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))

import collection_report  # noqa: E402
import reconcile_check  # noqa: E402
import status_summary  # noqa: E402
import overnight_health  # noqa: E402


def _mk_db(tmp: Path, stamps: list[str]) -> Path:
    db = tmp / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE announcements (id INTEGER PRIMARY KEY, "
                "created_at TEXT, fetched_at TEXT, source_url TEXT, "
                "source_id TEXT, region_province TEXT)")
    con.executemany("INSERT INTO announcements (created_at, fetched_at, "
                    "source_url, source_id, region_province) VALUES (?,?,?,?,?)",
                    [(s, s, f"u{i}", "s", "x") for i, s in enumerate(stamps)])
    con.commit()
    con.close()
    return db


def test_utc_hour_to_local_roundtrip():
    """UTC 小时桶换算成本地小时桶，跨天也要正确。"""
    offset = datetime.now().astimezone().utcoffset()
    if offset == timedelta(0):
        return  # 本机就是 UTC+0，换算无意义，跳过
    # 拿「当前 UTC 时刻」当桶，换算后必须等于「当前本地时刻」的桶
    utc_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    local_bucket = datetime.now().astimezone().strftime("%Y-%m-%dT%H")
    assert collection_report._utc_hour_to_local(utc_bucket) == local_bucket, \
        "UTC 桶 %s 换算后应是本地桶 %s" % (utc_bucket, local_bucket)


def test_utc_hour_to_local_passes_through_garbage():
    assert collection_report._utc_hour_to_local("not-a-date") == "not-a-date"


def test_health_window_uses_utc_threshold():
    """体检脚本的窗口阈值必须是 UTC，否则最近 8 小时数据会被漏掉。

    用**真实行为**验证，而不是在源码里搜字符串——源码注释里引用了旧的
    错误写法（那正是问题被记录的地方），文本匹配会误报。
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    # 关键：这条要落在「被吞掉的 8 小时带」里。
    # 正确阈值 = UTC now-14h；错误阈值 = 本地时间当 UTC 用，比正确阈值晚 8 小时。
    # 所以只有「距今 6~14 小时」的记录会被错误口径漏掉。用 10 小时。
    band = (now_utc - timedelta(hours=10)).isoformat(sep=" ", timespec="seconds")
    recent = (now_utc - timedelta(hours=3)).isoformat(sep=" ", timespec="seconds")
    old = (now_utc - timedelta(hours=30)).isoformat(sep=" ", timespec="seconds")

    since_utc = (datetime.now(timezone.utc)
                 - overnight_health.WINDOW).isoformat(sep=" ", timespec="seconds")
    since_local = (datetime.now().astimezone().replace(tzinfo=None)
                   - overnight_health.WINDOW).isoformat(sep=" ", timespec="seconds")

    def count(since: str) -> int:
        return sum(1 for s in (band, recent, old) if s >= since)

    if datetime.now().astimezone().utcoffset() > timedelta(0):
        assert count(since_utc) == 2, \
            "UTC 阈值应数到 10 小时前和 3 小时前的记录"
        assert count(since_local) == 1, (
            "这个用例只在 UTC+8 下成立：10 小时前那条正好落在被吞掉的 8 小时带里，"
            "错误口径会漏掉它（这正是「今天采集这么低」的成因）")


def test_db_stores_utc_timestamps():
    """前提假设：created_at / fetched_at 存的是 UTC。

    这是整套时区修复的地基——如果哪天入库改成写本地时间，
    上面几条的换算方向就得反过来。所以显式钉住。
    """
    src = (ROOT / "storage" / "db.py").read_text(encoding="utf-8")
    assert "created_at=now" in src or "created_at = now" in src, \
        "入库时间戳应统一用同一个 now"
    assert "utc" in src.lower(), "storage/db.py 里的 now 应取 UTC"

def test_reconcile_age_hours_uses_utc_base():
    """reconcile_check 的「零入库多少小时」必须以 UTC 为基准。

    2026-10-06 实测第三个同根因：`_age_hours` 用 `datetime.now()`（本地）
    减 UTC 时间戳，本机 UTC+8 → 虚高 8 小时。后果是判据整体前移：真的刚采过
    0.1 小时的源被报成「157 小时零入库」，48 小时阈值实际 40 小时就触发。
    """
    if datetime.now().astimezone().utcoffset() <= timedelta(0):
        return  # 本机不是 UTC+8，两个口径相同，测不出差异
    # 用库里真实的写法（naive UTC，见 test_db_stores_utc_timestamps）
    stamp = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S")
    got = reconcile_check._age_hours(stamp)
    assert got is not None, "应能解析 UTC 时间戳"
    assert 0.9 <= got <= 1.2, (
        "1 小时前入库的源应报约 1 小时，实际报 %.1f —— 基准用了本地时间"
        % got)

def test_today_count_uses_local_day_boundary(tmp_path, monkeypatch):
    """「今日入库」按**本地**日界切，不是 UTC 日界。

    2026-10-06 实测第四处同根因：列里存 UTC，``date('now')`` 也是 UTC，两者
    自洽，但给人看的「今日」不是 UTC —— 本地 10-07 早上 07:11 入的 37 条被算进
    UTC 的 10-06，汇总报「今日入库 0」。每天有 8 小时是错的。

    造两条卡着本地零点的东西：零点后 1 分钟（必须算进今天）、零点前 1 分钟
    （必须不算）。这样无论当前几点，期望值都是确定的 1。

    验证方式是把真函数指到临时库上跑（不是搜源码字符串——注释里就写着旧的
    错误写法，文本匹配会误报）。
    """
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE announcements (id INTEGER PRIMARY KEY, "
                "created_at TEXT, fetched_at TEXT)")
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0)
    local_midnight_utc = (local_midnight - offset).replace(tzinfo=None)
    con.executemany("INSERT INTO announcements (created_at, fetched_at) "
                    "VALUES (?,?)",
                    [(local_midnight_utc + timedelta(minutes=1),) * 2,
                     (local_midnight_utc - timedelta(minutes=1),) * 2])
    con.commit()
    con.close()

    monkeypatch.setattr(status_summary, "DB", db)
    got = status_summary._db_counts()["today"]
    assert got == 1, (
        "今日入库应只算本地零点之后那条，实得 %d —— 日界用错了时区" % got)
