"""熔断判据必须区分「没出货」和「**不知道有没有出货**」。

2026-10-05 实测踩过，代价极大。旧代码::

    last_produced = row[F_LAST_PRODUCED]
    if not last_produced:
        return True          # 访问过这么多次，一次都没出过货

``F_LAST_PRODUCED`` 只在 ``record(produced=True)`` 时写入，而**早期回填进来的
数据不经过它**。于是「计数器是 0」被当成「这个源从未出货」，143 个源里
**65 个被判永久熔断** —— 包括库里已有几百条、且实测还有几百条没采的源：

    js_gxt   库内 484 条   实测未入库 316
    cq_fgw   库内 432 条   实测未入库 203
    cq_ny    库内 327 条   实测未入库 170
    cq_kjt   库内 268 条   实测未入库 219
    moa_gov  库内 290 条   实测未入库 205

后果：10-04 入库 0 条、10-05 只有 5 条，日志只留一行「61 个源被熔断」，
看着像正常节流。真正的死源（3 天零出货）一个都没被误伤。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lighthouse.run import SourceBackoff as S  # noqa: E402


def _row(visits, zero_visits, last_epoch, last_produced):
    row = [0] * S.FIELDS
    row[S.F_VISITS] = visits
    row[S.F_ZERO_VISITS] = zero_visits
    row[S.F_LAST_EPOCH] = last_epoch
    row[S.F_LAST_PRODUCED] = last_produced
    return row


def test_unknown_last_produced_is_not_death():
    """计数器说「从未出货」+ 最近刚访问过 → 不得熔断。

    这是 65 个源被误杀的场景：F_LAST_PRODUCED=0（没经过计数器），
    但 F_LAST_EPOCH 是刚才（刚访问完）。
    """
    now = int(time.time())
    bo = S({"backfilled": _row(4, 4, now - 60, 0)})

    assert not bo.is_circuited("backfilled"), (
        "「计数器不知道」不是「从未出货」，刚访问过的源不该被熔断")


def test_unknown_last_produced_still_circuits_after_3_days():
    """真死源结论不变：3 天没访问也没出货，照样熔断。"""
    old = int(time.time()) - (4 * 24 * 3600)
    bo = S({"really_dead": _row(4, 4, old, 0)})

    assert bo.is_circuited("really_dead"), "4 天零产出仍应熔断"


def test_real_last_produced_used_when_present():
    """有真实出货时间时照旧用它判断，不被回退逻辑干扰。"""
    now = int(time.time())
    fresh = S({"ok": _row(9, 4, now - 86400, now - 3600)})
    stale = S({"stale": _row(9, 4, now - 86400, now - (5 * 24 * 3600))})

    assert not fresh.is_circuited("ok")
    assert stale.is_circuited("stale")


def test_no_evidence_at_all_is_not_death():
    """连访问时间都没有（手工塞进 backoff 表的条目）→ 无证据，不判死。"""
    bo = S({"manual": _row(4, 4, 0, 0)})

    assert not bo.is_circuited("manual")


def test_insufficient_visits_never_circuits():
    """访问次数不够的一律不熔断 —— 闸门本身没变。"""
    now = int(time.time())
    bo = S({"fresh": _row(3, 4, now - (30 * 24 * 3600), 0)})

    assert not bo.is_circuited("fresh"), "F_VISITS 不足不该熔断"


def test_real_backoff_table_is_not_mass_circuited():
    """回归防线：**因「计数器无数据」而熔断**的源必须是个位数。

    2026-10-05 修之前是 65/143。修之后降到 12~13，随后放开 ``max_items`` /
    ``archive_pages`` 又涨到 39 —— 但这 39 个的 ``F_LAST_PRODUCED`` **都有值**
    （真实 3 天以上零产出），属于正常熔断，不是误杀。

    所以这里**不能**断言总熔断率：放开采集能力之后源访问更频繁、真实产出间隔
    自然拉长，熔断数上升是预期的，盯一个固定百分比只会逼着人放宽阈值。

    真正要盯的是「没有出货证据」的那一类：它们的熔断完全来自
    ``F_LAST_EPOCH`` 回退，是缺失数据造成的误杀。判据：

        熔断源里，``F_LAST_PRODUCED == 0`` 的占比要低。

    这个比例只会在「又用缺失数据杀源」时上升。
    """
    path = S.path()
    if not path.exists():
        return
    bo = S.load()
    blown = [k for k in bo.data if bo.is_circuited(k)]
    if not blown:
        return
    unknown = [k for k in blown if not bo.data[k][S.F_LAST_PRODUCED]]
    assert len(unknown) <= max(3, len(bo.data) * 0.05), (
        "熔断源里 %d/%d 个是「无出货证据」被熔断的（占全库 %.0f%%），"
        "说明「计数器不知道 = 死源」这个混淆又回来了"
        % (len(unknown), len(blown), 100 * len(unknown) / max(1, len(bo.data))))
