"""源等级必须**只统计干净记录**。

背景：2026-10-05 实测发现 11 个 T3/T5 里 5 个是假警报——

    源          全部  needs_review  干净  干净中重复   修前  修后
    sd_rst      723  413           310   14 (4.5%)    T3    T2
    sc_nx_gov   401  17            384   12 (3.1%)    T5    T0
    sc_jx_gov   103  26             77    4 (5.2%)    T3    T2
    mct_gov      59  34             25    0           T3    T1
    tj_gov       32   8             24    0           T5    T1

根因不是阈值，是口径：``dup_body_records`` / ``dead_pages`` 是**累加量**，
而 needs_review 只加标记、不减计数。于是「把源修好了」永远不反映到等级上，
源会永久停在自己出问题那天的档位。

sc_nx_gov 最刺眼：401 条正常记录，只因历史上采到过 **1 条** 404 页面
就被判「不可采」，从此降级到 30 天探活一次。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import source_tiers as st  # noqa: E402


SCHEMA = """
CREATE TABLE announcements (
    id INTEGER PRIMARY KEY,
    source_id TEXT,
    title TEXT,
    content TEXT,
    publish_date TEXT,
    created_at TEXT,
    content_source TEXT,
    needs_review INTEGER DEFAULT 0
);
"""


def _make_db(tmp_path: Path, rows: list[tuple]) -> Path:
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO announcements (source_id, title, content, publish_date, "
        "created_at, content_source, needs_review) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return db


DECL = {"enabled": True, "playwright_fallback": False,
        "access_method": "", "level": "", "cadence_tier": "daily"}


def _classify(db: Path, sid: str, tmp_path: Path):
    st.DB = db
    try:
        prod = st._prod_signals()
        return st.classify_source(sid, prod.get(sid, {}), {}, DECL)
    finally:
        st.DB = ROOT / "data" / "beacon.db"


def test_historical_404_no_longer_condemns_source(tmp_path):
    """401 条正常记录 + 1 条已标记的历史 404 → 不能判 T5 不可采。"""
    rows = [("src", "公告 %d" % i, "正文内容足够长，不会被判重复 %d" % i,
             "2026-09-01", "2026-10-05 03:00:00", "static_html", 0)
            for i in range(40)]
    # 唯一一条 404，且**已经**被标记为需复核
    rows.append(("src", "找不到網頁", "Page Not Found", None,
                 "2026-10-01 03:00:00", "static_html", 1))
    t = _classify(_make_db(tmp_path, rows), "src", tmp_path)

    assert t.tier != "T5", "已标记的历史 404 不该再判不可采：%s" % t.reason


def test_historical_duplicates_decayed_after_marking(tmp_path):
    """723 条里 413 条已标记 → 干净记录的重复率才是判据。"""
    rows = []
    for i in range(30):          # 干净记录，正文各不相同
        rows.append(("src", "公告 %d" % i, "各不相同且足够长的正文 %d" % i,
                     "2026-09-01", "2026-10-05 03:00:00", "static_html", 0))
    for i in range(100):         # 历史栏目页垃圾，全部已标记
        rows.append(("src", "标题 %d" % i, "索引 服务 日一二三四五六最新发布",
                     None, "2026-10-01 03:00:00", "static_html", 1))
    t = _classify(_make_db(tmp_path, rows), "src", tmp_path)

    assert t.tier not in ("T3",), "脏记录已标记，不该再判形态可疑：%s" % t.reason


def test_all_output_is_garbage_is_still_flagged(tmp_path):
    """反向：干净 0 条、脏记录 10+ 条 → 仍要判 T3，而不是掉进 T1。"""
    rows = [("src", "标题 %d" % i, "栏目页", None,
             "2026-10-01 03:00:00", "static_html", 1) for i in range(15)]
    t = _classify(_make_db(tmp_path, rows), "src", tmp_path)

    assert t.tier == "T3", "只产垃圾的源该判 T3，实际 %s：%s" % (t.tier, t.reason)
    assert "需复核" in t.reason


def test_never_produced_is_not_garbage(tmp_path):
    """从没产出过 ≠ 产出了但全是垃圾。两者必须分开，不能都判 T3。"""
    t = _classify(_make_db(tmp_path, []), "src", tmp_path)

    assert t.tier != "T3", "没产出过的源不该判形态可疑：%s" % t.reason


@pytest.mark.parametrize("query_needs_review", [True])
def test_every_signal_query_filters_needs_review(query_needs_review):
    """回归防线：CLEAN 过滤器必须出现在每条信号查询上。

    漏掉任何一条都会复发——实测就是「改了前三个查询，忘了第四个 dead_pages」，
    sc_nx_gov 因此仍然是 T5。
    """
    assert "coalesce(needs_review,0)=0" in st.CLEAN
    src = (ROOT / "scripts" / "source_tiers.py").read_text(encoding="utf-8")
    body = src[src.index("def _prod_signals"):src.index("def _backoff_signals")]
    # 4 条查 announcements 的信号查询，每条都要带 CLEAN
    assert body.count("FROM announcements") >= 4
    assert body.count("+ CLEAN +") >= 4, (
        "信号查询里只有 %d 处用了 CLEAN，应为 4 处" % body.count("+ CLEAN +"))
