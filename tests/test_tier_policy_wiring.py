"""等级必须真的改变**采集方法**，而不只是降频。

背景：等级制度建起来之后只接了 ``revisit_seconds``（``is_sleeping_for``），
``routes`` / ``depth`` / ``max_items`` 三项都只是写在 ``TIER_POLICY`` 里没人读——
等于「给源贴了标签，但采集方式一点没变」。T3「链接形态可疑」会继续在一个
已知取错内容的链接形态上花整轮预算，这正是分级的反效果。

三条规则，每条都有代价，必须钉住：
1. 等级覆写在 ``with_defaults`` **之后** —— 默认地板是给「还不知道等级的源」
   准备的，等级是实测的，应当压过它。
2. ``depth`` / ``max_items`` 只往**保守**方向收窄，不因等级而加深。
3. ``routes`` 只做**跳过**不做新增；收窄后一层不剩就**放弃收窄**——
   宁可多花一次静态请求，也不能让源因为推断错误而彻底采不到数据。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lighthouse.run import apply_tier_policy, _restrict_ladder  # noqa: E402


TIERS = {
    "t3_src": {"tier": "T3", "routes": ["static"],
               "depth": 3, "max_items": 60, "revisit_seconds": 43200},
    "t2_src": {"tier": "T2", "routes": ["render"],
               "depth": 4, "max_items": 120, "revisit_seconds": 28800},
    "t0_src": {"tier": "T0", "routes": ["static"],
               "depth": 6, "max_items": 200, "revisit_seconds": 28800},
}


class _FakeLadder:
    def __init__(self, order, levels=None):
        self.order = list(order)
        self.levels = levels or {n: {"name": n} for n in self.order}


def test_tier_narrows_nothing_but_routes():
    """T3 只改路由顺序，配置里的深度/条数原样保留。"""
    src = {"id": "t3_src", "depth": 30, "max_items": 600}
    out, notes = apply_tier_policy(src, TIERS)

    assert out["depth"] == 30, "T3 不该改配置里的 depth"
    assert out["max_items"] == 600, "T3 不该改配置里的 max_items"
    assert out["tier_routes"] == ["static"]
    assert any("路由" in n for n in notes)
    assert src["depth"] == 30, "不该就地改调用方的 dict"


def test_depth_is_recorded_as_advisory_only():
    """tier 的 depth 写进 tier_depth_advisory，仅供诊断，不参与采集。

    采集器 ``_archive_pages()`` 读的是 ``archive_pages``，所以往 ``depth``
    写 tier 的值是空操作。留着它是为了让 source_tiers 的诊断输出不会
    让人误以为 tier 的 depth 真的在生效。
    """
    out, _ = apply_tier_policy({"id": "t3_src"}, TIERS)
    assert out["tier_depth_advisory"] == 3
    assert out.get("depth") is None, "不该凭空造一个 depth 键"


def test_tier_does_not_narrow_depth_or_items():
    """等级**不许**收窄 archive_pages / max_items。

    2026-10-05 实测推翻了原来的设计假设。``measure_pagination_headroom.py``
    量到的增量空间：

        moa_gov  库内 290 条
                 depth  3 →   59 URL，未入库   0
                 depth 12 →  235 URL，未入库   0
                 depth 30 →  495 URL，未入库 205
        mct_gov  库内  59 条
                 depth  3 →   93 URL，未入库  42
                 depth 12 →  120 URL，未入库  63

    当时 135/143 个源卡在退避、10-04 入库 0 条 —— 系统是**饿**的不是**撑爆**的。
    在这个状态下按等级收窄深度和条数，等于在最需要深挖的时候把深挖关掉。
    真要省预算该动 revisit_seconds（少回访），不是少翻页：
    少回访只省时间，翻页深度直接决定还有多少数据没被采走。
    """
    for src in ({"id": "t3_src", "archive_pages": 30, "max_items": 600},
                {"id": "t0_src", "archive_pages": 3, "max_items": 200}):
        out, _ = apply_tier_policy(src, TIERS)
        assert out.get("archive_pages") == src["archive_pages"], \
            "等级不该改 archive_pages：%s" % src["id"]
        assert out.get("max_items") == src["max_items"], \
            "等级不该改 max_items：%s" % src["id"]


def test_tier_routes_are_attached():
    out, notes = apply_tier_policy({"id": "t2_src"}, TIERS)
    assert out["tier_routes"] == ["render"]
    assert any("路由" in n for n in notes)


def test_unknown_source_gets_no_policy():
    """等级表里没有的源不能被改 —— 保守走默认。"""
    out, notes = apply_tier_policy({"id": "brand_new"}, TIERS)
    assert "tier_routes" not in out
    assert notes == []


def test_routes_reorder_never_exclude():
    """T2 要求 render：结果把 render **提前**，但 static/markdown 仍留作兜底。

    回归：2026-10-05 实际踩过。写成过滤（只保留 routes 里的层）后
    ``ndrc_gov`` 立刻变成 ``丢弃原因 {'render:too_short': 4}``，0 条入库——
    因为 T2 的 ``routes: ["render"]`` 语义是「静态拿不到，**优先**走渲染」，
    表达顺序不表达排除。过滤掉 static/markdown 后只剩渲染一条路，
    而渲染恰好拿不到正文，26 个 T2 源会被静默废掉。
    """
    ladder = _FakeLadder(["static", "render", "markdown"])
    changed = _restrict_ladder(ladder, ["render"])

    assert changed is True
    assert ladder.order[0] == "render", "render 必须排到最前，实际 %s" % ladder.order
    assert set(ladder.order) == {"static", "render", "markdown"}, \
        "其余层必须保留兜底，实际 %s" % ladder.order


def test_no_over_restriction_when_routes_disabled():
    """routes 为空（T5 熔断）时不改顺序，交给 terminal 机制处理。"""
    ladder = _FakeLadder(["static"])
    assert _restrict_ladder(ladder, []) is False
    assert _restrict_ladder(ladder, None) is False
    assert ladder.order == ["static"]


def test_no_change_when_all_levels_already_allowed():
    ladder = _FakeLadder(["static", "render"])
    assert _restrict_ladder(ladder, ["static", "render"]) is False
    assert ladder.order == ["static", "render"]


def test_route_not_in_config_is_ignored():
    """等级要求的层配置里没开 → 不报错、不改动，保留原顺序。"""
    ladder = _FakeLadder(["static"])
    assert _restrict_ladder(ladder, ["render"]) is False
    assert ladder.order == ["static"]
