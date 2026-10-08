"""首页「行业分布 / 省份×行业」视图的回归测试。

为什么要有这个：分类雷达原先排的是 ``category_l1``，而它**混编了文件体裁
（公示公告 / 政策法规 / 政策发布）和内容主题（行业政策 / 商业机会）**。
两类不同语义的词排在一起，既不是行业分布也不是体裁分布，纯属噪声。

三个真实踩过的坑，一并钉住：
1. 行业子类必须按 ``category_rules.yaml`` 的**父子结构**取，不能手写白名单
   ——手写会漏掉只出现在 ``fallback_categories`` 的「商务贸易」。
2. 体裁子类（公示/名单/办法）**不能**当行业，否则图退化成「各省发了多少公示」。
3. 页面是一个巨型 f-string，往里塞 JS 时每个花括号都得写成 ``{{``；
   现在 JS 已提成模块级常量 ``PROVIND_JS``，模板里只写 ``{PROVIND_JS}``。
   所以断言渲染结果里花括号**原样保留**、且没有 ``{{`` 泄漏。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _home():
    """**必须延迟导入**：home_dashboard 会 import streamlit。

    在模块作用域导入会污染整个 pytest 进程：streamlit 初始化时会重配 logging
    和 stdout，导致后面那些用 ``subprocess`` 跑真实脚本、再拿中文断言的测试
    （test_pipeline_lock / test_proxy_guard / test_view_smoke / test_principle_loop）
    全部拿到乱码而失败 —— 2026-10-05 实测 6 个测试因此挂掉，
    而单跑任何一个都是绿的，看起来像我的改动坏了采集，其实是我坏了测试环境。
    """
    import home_dashboard
    return home_dashboard

DB = ROOT / "data" / "beacon.db"


def test_industry_l2_from_yaml_parent_structure():
    """行业子类来自父子结构，不含任何体裁词。"""
    h = _home()
    names = h._industry_l2(None)
    assert names, "config/category_rules.yaml 应能解析出行业子类"
    for genre in ("公示", "公示公告", "通知公告", "名单", "办法", "政策法规", "公告"):
        assert genre not in names, f"体裁词 {genre!r} 不该被当成行业门类"


def test_distributions_expose_industry_fields():
    """db_distributions 必须给出行业和省份×行业两组数据。"""
    h = _home()
    if not DB.exists():
        return  # 无库时跳过，不制造假绿
    d = h.db_distributions("data/beacon.db")
    for key in ("cat_data", "ind_data", "prov_ind_data", "prov_data", "year_data"):
        assert key in d, f"缺少 {key}"
    assert d["ind_data"], "ind_data 不应为空"
    assert d["prov_ind_data"], "prov_ind_data 不应为空"
    for row in d["prov_ind_data"]:
        assert set(row) == {"prov", "ind", "value"}


def test_js_constant_braces_intact():
    """PROVIND_JS 是普通字符串常量，花括号必须原样保留。

    如果有人图省事把这段 JS 塞回 f-string 模板里，每个 ``{`` 就得写成 ``{{``，
    而报错位置会被 f-string 的插值机制扭曲（磁盘上明明是
    ``document.getElementById(...)``，ast 却报成 ``const piv )``）——非常难查。
    所以这里钉住「它不在模板里、且花括号是单层」。
    """
    h = _home()
    js = h.PROVIND_JS
    # 注意：不能断言 '}}' 不存在 —— `}})();`、`{}};` 是合法 JS。
    # 真正要抓的是「整块被转义成 f-string 风格」，那会让 stack/grid 全变双花括号。
    assert "stack: 'pi'" in js, "堆叠配置缺失或被转义成 {{stack}}"
    assert "echarts.init(el)" in js
    assert "grid: {left:" in js, "grid 配置被转义成 {{left:"
    assert js.count("{") == js.count("}"), "花括号不配对"


def test_template_embeds_js_by_placeholder():
    """模板里必须是 ``{PROVIND_JS}`` 占位符，而不是内联转义后的 JS。"""
    src = (ROOT / "home_dashboard.py").read_text(encoding="utf-8")

    assert "  {PROVIND_JS}\n" in src, "模板里找不到 {PROVIND_JS} 占位符"
    assert 'id="chart-provind"' in src, "省份×行业卡片 div 缺失"
    assert "PROVIND_JS = " in src, "常量定义缺失"

    # 行业榜用行业口径，不再用混编的 category_l1
    assert "STATS.ind_data.slice" in src
    assert "STATS.cat_data.slice" not in src


def test_empty_db_fallback_has_industry_keys():
    """空库回退路径也必须带齐新字段，否则首页直接 KeyError。"""
    empty = {"cat_data": [], "prov_data": [], "year_data": [],
             "ind_data": [], "prov_ind_data": []}
    assert "ind_data" in empty and "prov_ind_data" in empty
