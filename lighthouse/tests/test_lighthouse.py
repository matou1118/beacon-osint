"""灯塔采集工具的最小可运行检查。

只钉三件「坏了就整条链路作废」的事：
  1. 脏正文 / 太短正文必须被门禁拦下（否则又走回 soup.get_text() 的老路）
  2. 干净正文必须通过
  3. 摘要最后一个字符必然是终止标点（26.4% 句中硬切的根因）

跑： python -m pytest lighthouse/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lighthouse.quality import build_summary, extract_fields, judge, load_config  # noqa: E402

CFG = load_config()

# 一段真实政务正文：结构 = 标题 + 主送机关 + 发文缘由 + 编号小节。
# 长度必须过 800 字门槛（quality.min_content_chars），否则门禁会先拦下来，
# 测的就不是摘要逻辑而是长度判据了。
GOOD = """某省发展和改革委员会关于做好2026年重点项目建设工作的通知

各地级以上市发展改革委：为深入贯彻落实党中央、国务院关于稳增长的决策部署，
加快构建现代化产业体系建设，推动重点项目早落地、早开工，现将有关事项通知如下：
一、支持方向。重点支持能源、装备制造、数字基础设施、人工智能算力四个领域。
二、申报条件。申报单位应具有独立法人资格，财务管理制度健全，三年内无重大失信记录。
三、申报方式。申报单位于2026年10月15日前通过政务服务平台提交申报材料，逾期不予受理。
四、审核流程。省发展改革委受理后组织专家评审，评审通过的予以公示，公示期七个工作日。
五、资金安排。省财政将通过竞争性方式择优安排省级预算内投资 supporting 相关项目。
六、监督考核。项目实施期间实行季度调度和年度考核，考核结果纳入投资统计。
七、其他事项。本通知自发布之日起施行，有效期三年，由省发展改革委负责解释。
各地各部门要认真组织实施，确保各项任务落到实处，推动经济社会高质量发展。
八、附则。本通知所称重点项目，是指纳入省级重点项目建设名单的总投资一亿元以上的
新建、扩建和技术改造项目。项目的可行性研究报告应当已经完成审批或者备案。
九、申报纪律。任何单位和个人不得虚报冒领、弄虚作假骗取财政资金，一经查实，
取消该项目申报资格，追回已拨付资金，并依法追究相关责任人的责任。
十、咨询服务。申报工作由省发展改革委投资处具体负责，联系电话在政务服务平台公布，
具体申报系统操作说明另行下发，各市县发展改革委可组织本地企业开展政策宣讲。
十一、政策衔接。本通知与本省此前发布的相关规定不一致的，以本通知为准。
十二、解释权。本通知由省发展改革委负责组织编制和解释，自印发之日起执行。
十三、效果评估。省发展改革委在每年年底对本年度重点项目申报和实施情况进行
统计汇总，评估政策执行效果，必要时对支持方向和申报条件作出调整优化。
各地要按照本通知要求，及时组织辖区内企业申报，合理确定项目盘子，
加大项目前期工作力度，为重点项目顺利实施奠定坚实基础。
"""

# 满屏面包屑，一个字正文都没有（id=1453 山西政府公报的真实形态）
CRUMB = ("_ [首页](https://www.shanxi.gov.cn/ \"首页\") > [政务公开](https://www.shanxi.gov.cn/zfxxgk/) "
         "> [政府出版物](https://www.shanxi.gov.cn/zfxxgk/zfcbw/) > [政府公报](https://x.gov.cn/a/) "
         "> [2026年政府公报](https://x.gov.cn/b/) ![](https://x.gov.cn/images/g.png) "
         "山西省人民政府公报 gazette of the People's Government of Shanxi Provincial")


def test_clean_body_passes_gate():
    verdict = judge(GOOD, CFG)
    assert verdict.passed, f"干净正文应通过，实际 {verdict.reason} {verdict.signals}"


def test_short_body_fails_gate():
    assert not judge("很短的一段话，不足以构成可用正文。", CFG).passed


def test_short_but_clean_is_accepted_without_escalating():
    """300 字的完整通告：内容是好的，只是短。

    这条不许判死，也**不许升级到渲染**——渲染要 30-50 倍成本，而且不会让
    一篇短通告变长。bench 实测全库时发现原来的 800 字门槛把 1375 条好数据
    误杀了，就是这个错。
    """
    body = ("某市市场监督管理局关于开展食品生产经营单位专项检查的通知\n"
            "各区市场监督管理局：为保障群众食品安全，根据工作安排，现就有关事项通知如下："
            "一、检查范围。本市所有食品生产企业、餐饮服务单位、学校食堂。"
            "二、检查内容。原料采购、仓储、加工制作、餐具清洗消毒等环节。"
            "三、时间安排。2026年10月8日至10月20日。\n"
            "四、工作要求。各单位要高度重视，按时完成检查并上报情况，"
            "检查结果作为食品安全生产信用评价的重要依据纳入年度考核。\n")
    verdict = judge(body, CFG)
    assert verdict.passed, f"短而干净的正文应被接受，实际 {verdict.reason}"
    assert not verdict.fatal, "短而干净不应触发升级渲染"
    summary, reason = build_summary("关于开展食品生产经营单位专项检查的通知", body, CFG)
    assert summary and not reason, f"短通告也该出摘要，实际 reason={reason}"
    assert summary[-1] in "。！？；", f"摘要未落在终止标点上：{summary[-20:]}"


def test_breadcrumb_page_fails_gate():
    """这条是 data_health.py 的 CHROME_RE 漏掉、只有本工具拦得住的形态。"""
    verdict = judge(CRUMB, CFG)
    assert not verdict.passed, "满屏面包屑必须被判不通过"
    assert verdict.reason in {"link_flood", "too_short", "toc_page"}


def test_chrome_words_in_a_long_body_are_tolerated():
    """长正文里偶然出现导航词不算污染——按比例判，不按次数判。

    实测全库时 max_chrome_hits: 0 误杀了 61 条超过 1000 字的正常文档。
    """
    long_body = GOOD + "　" * 200 + "\n网站地图　联系我们\n"
    verdict = judge(long_body, CFG)
    assert verdict.passed, f"长正文里的少量导航词不该判死：{verdict.reason} {verdict.signals}"


def test_chrome_flooded_body_fails_gate():
    """但满页导航必须判死。"""
    flooded = GOOD + "\n" + "　".join(["上一篇 下一篇 返回顶部 网站标识码 版权所有 联系我们 网站地图 友情链接"] * 40)
    verdict = judge(flooded, CFG)
    assert not verdict.passed and verdict.fatal
    assert verdict.reason == "chrome"


def test_summary_ends_at_terminator():
    """核心硬约束：绝不在字符数处硬切。"""
    for _ in range(1):
        summary, reason = build_summary("某省重点项目建设工作的通知", GOOD, CFG)
        assert summary, f"应有摘要，实际 reason={reason}"
        assert summary[-1] in "。！？；", f"摘要未落在终止标点上：…{summary[-25:]}"


def test_summary_never_exceeds_budget():
    summary, _ = build_summary("某省重点项目建设工作的通知", GOOD, CFG)
    assert len(summary) <= int((CFG.get("summary") or {}).get("max_chars", 300)) + 120, \
        "摘要超出预算（字段前缀不计入正文预算，但不该膨胀这么多）"


def test_run_deadline_skips_source_instantly():
    """整轮时限到点后，源要立刻返回而不是继续采——这是「一轮必须能跑完」的基础。

    109 源 / 6 并发 / 单源 300 秒最坏要 91 分钟。没有全局时限，
    一次运行能被无限拖长；实测跑满 50 分钟被外层 kill，产出全丢。
    """
    import time as _time

    from lighthouse.run import collect_source, load_sources

    sources = load_sources(("enabled",))
    assert sources, "sources.yaml 应有启用的源"
    started = _time.time()
    result = collect_source(sources[0], CFG, limit=5, known=set(), dry_run=True,
                            deadline=_time.time() - 1)      # 已经过期
    assert _time.time() - started < 2.0, "过期后应立刻返回"
    assert result.get("skipped") is True
    assert not result["records"]


def test_progress_renders_budget():
    """进度行必须能说清「什么时候停」，否则又是一块黑屏。"""
    import time as _time

    from lighthouse.run import Progress

    progress = Progress(10, interval=0.01, deadline=_time.time() + 120)
    line = progress._render()
    assert "10" in line and "后停" in line, line
    progress.deadline = _time.time() - 1
    assert "已到时限" in progress._render()


def test_org_not_duplicated_when_title_already_has_it():
    """标题里已含机构名，前缀就别再来一遍。

    实测出现过「四川省商务厅2025-07-30发布行政规范性文件 - 四川省商务厅。」，
    同一个机构名出现两次。
    """
    body = ("行政规范性文件 - 四川省商务厅\n"
            "为规范行政规范性文件制定管理工作，根据相关法规，现���有关事项通知如下："
            "一、总体要求。各地商务主管部门要提高认识，健全机制。\n"
            "二、起草程序。规范性文件应当经合法程序审查后公布。\n"
            "三、有效期。规范性文件一般有效期为五年，期满需要重新制定的应当重新履行程序。\n")
    summary, _ = build_summary("行政规范性文件 - 四川省商务厅", body, CFG,
                                {"issuing_body": "四川省商务厅"})
    assert summary.count("四川省商务厅") <= 1, f"机构名重复：{summary[:60]}"
    assert summary[-1] in "。！？；"


def test_regex_org_does_not_swallow_verbs():
    """正则兜底抽机构名时不能把动词/状语吃进去。

    实测原正则 `[一-龥]{2,20}?(?:厅|...)` 在
    「为了便于公民、法人和其他组织及时、准确获取四川省商务厅的政府信息」里
    抽出的是「准确获取四川省商务厅」——多了四个字的状语。
    """
    body = ("政府信息公开指南\n为了便于公民、法人和其他组织及时、准确获取四川省商务厅的"
            "政府信息，提高政府工作的透明度，现编制本指南。\n"
            "一、主动公开的情形。涉及公众利益调整的应当主动公开。\n"
            "二、依申请公开的情形。公民可以向行政机关提出申请。\n"
            "三、申请方式。申请应当采用书面形式并载明相关要求。\n"
            "四、答复时限。行政机关应当自收到申请之日起二十日内答复。\n")
    found = extract_fields(body, CFG).get("issuing_body", "")
    assert not found.startswith(("准确", "及时", "为了", "为便于")), found
    assert found in ("四川省商务厅", "") or "厅" in found, found


def test_nav_index_page_gets_no_summary():
    """纯导航索引页不能出一条 18 字的「发布XXX。」。

    那是 kjt.sc.gov.cn 的真实形态：211 字全是「无障碍/设为首页/打印/收藏」
    这种无终止标点的短条目，一个完整句都没有。返回空摘要 + 原因，
    标成 skipped，比假装有摘要强。
    """
    nav = ("您好！欢迎进入四川省科学技术厅，今天是2026年9月28日 星期一\n"
           "无障碍\n设为首页\n打印\n收藏\n繁體\n微信\n微博\n长者专区\n网站支持IPv6\n"
           "首页\n政府信息公开\n办事大厅\n政策法规\n统计信息\n党建工作\n"
           "业务综合服务平台\n四川科技\n四川省科技管理信息系统\n"
           "四川省科学技术奖励综合业务系统\n四川省科技报告呈交系统\n")
    summary, reason = build_summary("业务综合服务平台", nav, CFG,
                                    {"issuing_body": "四川省科学技术厅"})
    assert not summary, f"导航页不该出摘要，实际 {summary!r}"
    assert reason == "no_usable_sentence", reason


def test_chrome_vocabulary_catches_gov_nav_boilerplate():
    """政务站导航套话（无障碍/繁體/长者专区/网站支持IPv6）要能被抓到。

    原来这张表里一个都没有，kjt.sc.gov.cn 整页漏过门禁。
    """
    body = GOOD + "\n" + "\n".join(["无障碍", "设为首页", "繁體", "长者专区",
                                     "网站支持IPv6", "旧版回顾", "当前位置：首页"] * 30)
    verdict = judge(body, CFG)
    assert not verdict.passed and verdict.reason == "chrome", verdict.signals


def test_table_only_announcement_gets_key_value_summary():
    """整页表格的公告要能从表头取键值对出摘要，不能直接丢。

    招投标中标结果、环评受理清单这类内容没有一句散文，恰恰是「重大项目」
    最相关的。丢掉的代价太大。
    """
    table = ("| 序号 | 项目名称 | 建设单位 | 受理日期 |\n"
             "| 1 | 某800千伏特高压直流输电工程 | 国家电网有限公司 | 2026-9-23 |\n"
             "| 2 | 某220千伏输变电工程 | 某省电力公司 | 2026-9-24 |\n")
    summary, reason = build_summary("关于拟作出审批决定的公示", table, CFG)
    assert summary, f"表格公告应出摘要，实际 reason={reason}"
    assert "项目名称" in summary and "某800千伏特高压直流输电工程" in summary
    assert summary[-1] in "。！？；"


def test_colon_before_numbered_section_breaks_sentence():
    """公文的「现将有关事项通知如下：一、……」要在冒号处断句。

    只按句号切的话整段会变成 300+ 字的超长句，被 max_sentence_chars 丢掉。
    实测全库有 140 条卡在这里。
    """
    from lighthouse.quality import split_sentences

    text = "为做好相关工作，现将有关事项通知如下：一、支持方向。支持能源和装备制造。二、申报条件。"
    parts = split_sentences(text, "。！？；")
    assert len(parts) >= 4, f"应按冒号处断开，得到 {len(parts)} 段：{parts}"


def test_workbench_view_renders_both_states():
    """工作台视图在「运行中」和「已停止」两种状态下都要渲染无异常。

    用 streamlit 的 AppTest 真渲染一遍——只做 HTTP 200 的检查没有意义，
    Streamlit 是 websocket 渲染，视图里的异常只有真跑才看得见。
    app.py 是脚本式入口（import 即 main()），所以视图留了 __main__ 口子。
    """
    from streamlit.testing.v1 import AppTest

    view = _ROOT / "lighthouse_view.py"
    if not view.exists():
        return                                  # 视图还没上线，跳过

    at = AppTest.from_file(str(view), default_timeout=120)
    at.run()
    assert not at.exception, f"视图渲染异常：{[str(e.value) for e in at.exception]}"
    # 无论是启是停，总得有可交互的控件
    assert at.button, "视图没有任何按钮"

    # 强制切到「没有 status.json」的状态，验证启动表单也能渲染
    status = _ROOT / "data" / "runtime" / "lighthouse" / "status.json"
    backup = status.with_suffix(".json.bak")
    moved = False
    try:
        if status.exists():
            status.replace(backup)
            moved = True
        at2 = AppTest.from_file(str(view), default_timeout=120)
        at2.run()
        assert not at2.exception, \
            f"空状态渲染异常：{[str(e.value) for e in at2.exception]}"
        assert any("启动" in b.label for b in at2.button), \
            f"空状态应出现启动按钮，实际 {[b.label for b in at2.button]}"
    finally:
        if moved and backup.exists():
            backup.replace(status)


def test_nav_icon_contract():
    """加视图时最容易漏图标，而图标名不合法会让整页白屏（app.py:105 的硬闸）。"""
    import re

    app = _ROOT / "app.py"
    if not app.exists():
        return
    src = app.read_text(encoding="utf-8")
    nav = re.search(r"NAV_GROUPS = \[(.*?)\]\n", src, re.S)
    if not nav:
        return
    views = [name for group in re.findall(r'\[\s*((?:"[^"]+"\s*,?\s*)+)\]', nav.group(1))
             for name in re.findall(r'"([^"]+)"', group)]
    icons = dict(re.findall(r'"([^"]+)": "(:material/[^"]+:)"', src))
    dispatched = set(re.findall(r'active_view == "([^"]+)"', src))
    missing = [v for v in views if v not in icons or v not in dispatched]
    assert not missing, f"这些视图缺图标或没在 main() 里分发：{missing}"


def test_collect_defaults_apply_as_floors_not_just_blanks():
    """采集默认值必须能「抬低」已有的值，不能只填空。

    实测依据：archive_pages 6 -> 12，未采候选从 88 涨到 249（4 个源）。
    但 52 个源在 sources.yaml 里已经写了 6，只填空的话它们拿不到这个收益。
    """
    from lighthouse.quality import load_config
    from lighthouse.run import load_sources, with_defaults

    cfg = load_config()
    sources = load_sources(("enabled",))
    assert sources, "应有启用的源"
    merged = [with_defaults(s, cfg) for s in sources]
    floors = cfg.get("collect_defaults") or {}
    want_pages = int(floors.get("raise_archive_pages_below") or 0)
    want_items = int(floors.get("raise_max_items_below") or 0)
    if want_pages:
        assert all(m["archive_pages"] >= want_pages for m in merged), \
            "有源的 archive_pages 低于下限"
    if want_items:
        assert all(m["max_items"] >= want_items for m in merged), \
            "有源的 max_items 低于下限"


def test_summary_is_copied_not_generated():
    """摘要的**句子部分**必须逐字来自原文——这是「不编造」的可执行形式。

    开头那句「某省发展和改革委员会2026-10-15发布《…》。」是按模板拼的字段前缀，
    不属于抄录范围，所以从第一个句号之后开始校验。
    """
    from lighthouse.quality import strip_noise

    summary, _ = build_summary("某省重点项目建设工作的通知", GOOD, CFG)
    body = "".join(strip_noise(GOOD).split())
    for piece in "。".join(summary.split("。")[1:]).split("。"):
        piece = "".join(piece.split())
        if len(piece) < 8:
            continue
        assert piece in body, f"摘要里有原文不存在的内容：{piece!r}"


def test_prefix_facts_do_not_invent_amount():
    """金额宁可置空。旧摘要会把「第 40 篇案例的 268 万元」当成文件投资额，
    这里锁住：正文没有金额时，前缀里不得出现金额。"""
    fields = extract_fields(GOOD, CFG)
    assert not fields.get("amount"), f"本样本正文没有投资规模，不该抽出金额：{fields}"
    summary, _ = build_summary("某省重点项目建设工作的通知", GOOD, CFG)
    assert "万元" not in summary or "预算内投资" in summary
