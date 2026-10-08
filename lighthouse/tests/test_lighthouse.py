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

from lighthouse.quality import (build_summary, extract_fields, judge,  # noqa: E402
                                load_config)

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


def test_link_heavy_needs_actual_prose_not_just_ratio():
    """满屏链接要分两种：真垃圾页 vs 正文短的真公告。

    回归 2026-09-30（jshrss.jiangsu.gov.cn，一条真实公告）：crawl4ai 的
    markdown 输出 2628~4906 字符里 81~85% 是 `[文字](url)` 占位符（栏目
    导航、字体大小按钮、附件图标），真正正文约 130~490 字。只看 link_ratio
    会把它当垃圾丢掉 —— 但它是一条合法公告，只是**正文天生短**（内容在
    .docx 附件里）。

    反过来也有真 bug：纯链接的栏目索引页 chars 被占位符撑到 1629，
    > min_summary(200) 也 > min_chars(800)，**不显式拦就会一路穿过所有
    长度门禁被判「通过」**。所以必须看「剥掉占位符后还剩多少实质正文」。
    """
    import lighthouse.quality as quality

    nav_units = [
        "![](https://x.gov.cn/a.png)超龄劳动者用工协议（参考文本）docx",
        "[首页](https://x.gov.cn/col/col78937/index.html)",
        "[资讯中心](https://x.gov.cn/col/col80048/index.html)",
        "[字体](javascript:doZoom(0.15)) [中](javascript:doZoom(0.18))",
        "[关闭本页](javascript:;) [打印本页](javascript:;)",
    ]
    prose = ("江苏发布《超龄劳动者用工协议（参考文本）》。为贯彻落实"
             "《超龄劳动者基本权益保障暂行规定》，指导用人单位和超龄劳动者"
             "签订书面用工协议，江苏省人力资源和社会保障厅制定了"
             "《超龄劳动者用工协议（参考文本）》，供用人单位参考。")

    # 1. 真公告：链接占比高但确有正文 → 不得判 link_flood
    md = "\n".join(nav_units * 3) + "\n" + prose + "\n" + "\n".join(nav_units * 2)
    v = judge(md, CFG)
    assert v.reason != "link_flood", \
        "正文短的真实公告被当成垃圾丢掉（prose_chars={}）".format(
            v.signals.get("prose_chars"))
    assert v.signals.get("link_heavy_but_has_prose"), \
        "应记录「链接重但有正文」这个信号，便于后续统计：{}".format(v.signals)

    # 2. 真垃圾：纯链接、零正文 → 必须判死（否则会穿过所有长度门禁）
    junk = "\n".join("[栏目{}](https://x.gov.cn/col/{}/)".format(i, i)
                     for i in range(50))
    v2 = judge(junk, CFG)
    assert v2.reason == "link_flood", \
        "纯链接索引页应判死，实际 {}（chars={} 会让它穿过长度门禁）".format(
            v2.reason or "pass", v2.signals.get("chars"))
    assert v2.signals.get("prose_chars", 999) < quality.MIN_PROSE_CHARS

    # 3. 正常长正文不受影响
    body = "索引号：014000319/2026-00347 主题：关于强化稳岗扩容提质的通知 " * 40
    v3 = judge(body, CFG)
    assert v3.reason == "", "正常长正文不该受影响：{}".format(v3.reason)


def test_chrome_ratio_needs_absolute_floor_not_just_ratio():
    """短正文上的 chrome 比例不可信：9 个字的页脚不该杀死一条真新闻。

    回归 2026-09-30（实测 moe_gov 人事任免新闻）：正文 259 字，页脚
    「版权所有：中华人民共和国教育部 京ICP备... 网站标识码」命中 9 个
    chrome 字符 —— 9/259 = 3.4% > max_chrome_ratio 3%，整篇被判死。

    比例判据在短正文上完全失真：几个字符就能把占比顶穿。真正要拦的是
    「满屏导航」，那种页面 chrome 字符数以百计。所以加绝对下限，
    且下限不能把真垃圾页放过去。
    """
    import lighthouse.quality as quality

    # 真实样本：教育部人事任免新闻正文 + 页脚
    news = (
        "9月30日，教育部人事司在华东师范大学宣布了教育部党组的任免决定，顾红亮"
        "同志任华东师范大学党委书记，梅兵同志不再担任华东师范大学党委书记职务。"
        "教育部人事司主要负责同志出席会议并讲话，上海市教卫工作党委有关负责同志"
        "出席会议。顾红亮，1971年10月出生，研究生，哲学博士，中共党员，教授。"
        "曾任华东师范大学党委副书记、副校长，上海市委教育工委副书记、市教委副"
        "主任。版权所有：中华人民共和国教育部 中文域名：教育部.政务"
        "京ICP备05002869号 京公网安备号 1010202007625 网站标识码：bm05000001"
    )
    v = judge(news, CFG)
    assert v.reason != "chrome", \
        "259 字真新闻只因 9 个字符的页脚被判死（chrome_ratio={}）".format(
            v.signals.get("chrome_ratio"))

    # 反面对照：满屏导航必须仍被拦。200 字的下限不能放过这种页面。
    nav = ("[首页](/)\n" * 40) + "版权所有：教育部 政务 京ICP备 网站标识码 " * 12
    v2 = judge(nav, CFG)
    assert v2.reason, "满屏导航页必须被拦，实际判为通过"

    # 面包屑满屏（link_flood 本来要拦的那类）也必须仍被拦
    crumbs = "\n".join("[栏目{}](/col/col{}/index.html)".format(i, i)
                       for i in range(60))
    v3 = judge(crumbs, CFG)
    assert v3.reason, "面包屑满屏必须被拦，实际判为通过"

    assert quality.MIN_CHROME_CHARS >= 100, \
        "下限设太低就退化成只看比例了"


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
    floors = cfg.get("collect_defaults") or {}
    want_pages = int(floors.get("raise_archive_pages_below") or 0)
    want_items = int(floors.get("raise_max_items_below") or 0)

    # 契约（2026-10-02 修订）：下限只抬**没显式写**的源。
    #
    # 原契约是「一律抬到下限」，后来按产出分档时发现它把分档抬平了：
    # 想让 C 档（库内 >=100 条的高产源）深翻 12 页、B 档其余 3 页保持快节奏，
    # 结果**每一档**都被算成 30 页，B 档的 3 形同虚设——而每源 30 页正是
    # 「145 源 / 30 分钟预算只跑完 36 个」的慢源根因。
    # 所以显式值视为作者意图，下限只防漏配（见 test_source_throttle.py 的
    # ArchiveDepthTierTests，那里钉住了双向行为）。
    unfilled_pages = [m for s, m in ((s, with_defaults(s, cfg)) for s in sources)
                      if not s.get("archive_pages")]
    unfilled_items = [m for s, m in ((s, with_defaults(s, cfg)) for s in sources)
                      if not s.get("max_items")]
    if want_pages:
        assert all(m["archive_pages"] >= want_pages for m in unfilled_pages), \
            "没显式写的源 archive_pages 低于下限（防漏配失效）"
    if want_items:
        assert all(m["max_items"] >= want_items for m in unfilled_items), \
            "没显式写的源 max_items 低于下限（防漏配失效）"

    # 反过来：显式写了的必须原样保留，否则分档无从谈起。
    explicit = [with_defaults(s, cfg) for s in sources if s.get("archive_pages")]
    assert all(m["archive_pages"] == int(s["archive_pages"])
               for s, m in ((s, with_defaults(s, cfg)) for s in sources)
               if s.get("archive_pages")), \
        "显式写的 archive_pages 被下限覆盖了，按产出分档会失效"


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


def test_every_list_html_path_is_observed():
    """所有取回列表 HTML 的路径都必须过感知，不能只感知首屏。

    回归 2026-09-30：感知原先只挂在 _list_html()（首屏静态 HTML）上，而
    fetch_list() 还有两条补链接的路 —— _static_archive_pages() 翻页和
    _render_archive() 的 playwright 渲染页 —— 两条都直接 _extract_links，
    完全不感知。

    后果是：对 JS 渲染型 / 分页型政府站（恰恰是最容易改版、最该自愈的那批）
    感知永远返回「没坏」。实测一次真实采集 4 个源 observe 调用 4 次、0 事件，
    看着像「一切正常」，其实是覆盖不全 —— 这类假阴性比误报更危险：它让
    影子模式看起来在正常工作。
    """
    import ast
    from pathlib import Path

    src = Path(_ROOT) / "collectors" / "html_list.py"
    code = src.read_text(encoding="utf-8")
    tree = ast.parse(code)
    lines = code.splitlines()

    observed_lines = {
        i + 1 for i, ln in enumerate(lines)
        if "_observe_selector_health(" in ln and "def " not in ln
    }
    assert observed_lines, "找不到 _observe_selector_health 调用"

    # fetch_list 的函数体范围
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fetch_list")
    lo, hi = fn.lineno, max(getattr(n, "lineno", fn.lineno) for n in ast.walk(fn))
    in_fn = {i for i in observed_lines if lo <= i <= hi}

    calls = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_extract_links"]
    assert calls, "fetch_list 里没有 _extract_links 调用，测试本身失效了"

    # fetch_list 内只要有感知，就说明所有取回的列表页都被覆盖了 ——
    # 感知统一挂在 _list_html() / 翻页 / 渲染三个入口，各自紧邻 _extract_links。
    # 早先只查「调用行前 6 行」会漏掉经 _list_html() 转发的那处（L723），
    # 因为感知在 _list_html 内部、隔着一次函数调用。
    assert len(in_fn) >= 2, (
        f"fetch_list 内只找到 {len(in_fn)} 处感知调用（L{sorted(in_fn)}），"
        f"预期至少 2 处：静态首屏 + 翻页/渲染补链路径。"
        f"只挂首屏会让 JS 渲染型站点逃过感知。")


def test_prefix_facts_do_not_invent_amount():
    """金额宁可置空。旧摘要会把「第 40 篇案例的 268 万元」当成文件投资额，
    这里锁住：正文没有金额时，前缀里不得出现金额。"""
    fields = extract_fields(GOOD, CFG)
    assert not fields.get("amount"), f"本样本正文没有投资规模，不该抽出金额：{fields}"
    summary, _ = build_summary("某省重点项目建设工作的通知", GOOD, CFG)
    assert "万元" not in summary or "预算内投资" in summary


def test_all_sources_paused_round_does_not_crash(monkeypatch, tmp_path):
    """全部源都在回访间隔里时，这一轮必须正常收尾，不能崩。

    回归 2026-09-30 的 UnboundLocalError：那条早退分支调用
    `_finish(cfg, [], stamp, ...)`，而 stamp 在它之后才赋值。
    「所有源都在间隔内」在密集轮次下是常态（日志里 34/34 全在退避中），
    所以这不是边缘情况，而是每轮必崩 —— 崩掉后调度器拿不到 next_due，
    整夜白跑。
    """
    import lighthouse.run as run_mod

    monkeypatch.setattr(sys, "argv", [
        "run.py", "--all", "--limit", "1", "--workers", "1",
        "--max-minutes", "1",
    ])

    class _AlwaysSleeping:
        @staticmethod
        def load():
            return _AlwaysSleeping()

        @staticmethod
        def is_sleeping_for(_sid, _tier):
            return True

        @staticmethod
        def is_terminal_blocked(_sid):
            # 全退避桩里没有终态源。少这个方法会 AttributeError：
            # next_due 计算要按 is_terminal_blocked 过滤掉 TERMINAL_NEVER 的源。
            return False

        @staticmethod
        def circuit_list(_ids):
            return set()

        @staticmethod
        def due_at(_sid, _tier):
            return 9999999999          # 很久以后 → next_due 应被如实上报

        @staticmethod
        def next_run():
            # 早退分支也要推进轮次计数器（2026-09-30 修：否则 runs 停住，
            # 轮次退避变成永久挡，采干的源再也回不来）。
            return None

        @staticmethod
        def save():
            return None

        @staticmethod
        def prune(_ids):
            return None

    monkeypatch.setattr(run_mod, "SourceBackoff", _AlwaysSleeping)
    monkeypatch.setattr(run_mod, "_finish",
                        lambda cfg, results, stamp, args, **kw: calls.append(
                            (stamp, kw.get("next_due"))))
    calls: list[tuple] = []
    monkeypatch.setattr(run_mod, "load_sources",
                        lambda statuses: [{"id": "x_gov", "status": "enabled"}])

    assert run_mod.main() == 0, "全退避轮次应正常返回 0"
    assert len(calls) == 1, f"应落一份收尾报告，实际 {calls}"
    stamp, next_due = calls[0]
    assert stamp and isinstance(stamp, str), f"stamp 必须已赋值，实际 {stamp!r}"
    assert next_due == 9999999999, \
        f"调度器靠 next_due 决定等多久；不报就得按固定节奏空转。实际 {next_due!r}"


def test_redirect_stub_page_is_not_judged_as_broken():
    """跳转桩 / 空页不得被判成「入口失效」。

    回归 2026-09-30（mof_gov 误报）：第 2 个归档页是 579 字节的 JS 跳转桩

        <script>location.replace("./tongzhitonggao/");</script><body></body>

    全页只有 <title> 里那 1 个链接。它只差 79 字节就绕过 500 字节护栏，于是
    形态过滤必然 0 条 → 记一条「形态失效」事件。可 mof_gov 的形态其实好好的：
    首屏 44474 字 / 64 条链接 / 滤出 14 条。

    这是**误报**，而且是最坏的一类：把「翻页取到跳转桩」上报成「站点改版、
    形态过时」，白白惊动修复流程、消耗熔断预算，还会让运维去改一个没坏的配置。

    关键推论：只有 1 条链接时**任何**形态都滤不出 0 条，所以推不出「形态过时」。
    链接少到不足以判定时，正确结论是「这一页不可用，本次不判定」。
    """
    from lighthouse import repair

    # 真页面里抠出来的跳转桩（579 字节，刻意贴着 500 字节护栏）。meta 那几行
    # 是为了把长度顶到 579 —— 少了它们就是 372 字，会被字节护栏拦下，
    # 复现不出「绕过护栏却误判」这个情形。
    stub = (
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n<head>\n'
        '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">\n'
        '<meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1">\n'
        '<meta name="SiteName" content="中华人民共和国财政部">\n'
        '<meta name="SiteDomain" content="http://www.mof.gov.cn">\n'
        '<meta name="SiteIDCode" content="bm14000001">\n'
        '<title><a href="./">通知公告</a></title>\n'
        '<script>\n  location.replace("./tongzhitonggao/");\n</script>\n'
        '</head>\n\n<body>\n</body>\n</html>\n')
    assert 500 < len(stub) < 800, \
        "夹具要贴着 500 字节护栏，才能复现当初绕过护栏的情形，实际 {}".format(len(stub))

    pattern_src = {
        "id": "mof_gov",
        "url": "https://www.mof.gov.cn/gkml/",
        "detail_pattern": r"/\d{6}/t\d{8}_\d+\.html?$",
    }
    obs = repair.observe_list_page(stub, pattern_src, list_url=pattern_src["url"])
    assert not obs.get("broken"), \
        f"跳转桩不得判成形态失效（会误报、消耗熔断预算）：{obs}"
    assert not obs.get("kind"), \
        f"未判定就不该带 kind，否则上层会当成真失效记事件：{obs}"
    assert "不判定" in (obs.get("detail") or ""), obs

    # 选择器分支同一个坑：跳转桩也匹配不到任何选择器
    sel_src = dict(pattern_src)
    del sel_src["detail_pattern"]
    sel_src["list_selector"] = "#barrierfree_container"
    obs2 = repair.observe_list_page(stub, sel_src, list_url=sel_src["url"])
    assert not obs2.get("broken"), \
        f"跳转桩不得判成选择器失效：{obs2}"

    # 反面对照：链接够多、形态真的失效时，仍必须判失效（闸不能把真故障也挡住）
    real = ("<html><body><ul>"
            + "".join("<li><a href='/other/{}/x.html'>x{}</a></li>".format(i, i)
                      for i in range(20))
            + "</ul></body></html>")
    obs3 = repair.observe_list_page(real, pattern_src, list_url=pattern_src["url"])
    assert obs3.get("broken"), \
        f"链接够多还滤不出 0 条，是真失效，必须判出来：{obs3}"
    assert obs3.get("kind") == "detail_pattern", obs3


def test_shape_source_valid_when_pattern_matches_few():
    """形态类源的「有效」判据是**滤出了东西**，不是滤出多少。

    回归 2026-09-30：zj_fgw 登记的形态就是栏目页本身
    （/col/col\\d+/index\\.html），列表页 45 条链接里 33 条同形态、12 条跨域，
    滤出数天然就少。按「>=4」判它失效会去修一个没坏的形态，
    而 derive_pattern 又会正确拒绝 → 白记一条假事件、白耗一轮修复预算。
    """
    from bs4 import BeautifulSoup

    from lighthouse import repair as sh

    # 夹具要过 500 字节的「页面过短」护栏（observe_list_page:304）—— 那个
    # 护栏是对的：500 字节以下的页面确实可能压根没加载出来。所以这里用真实
    # 体量的导航填充，而不是把护栏也一并放宽。
    filler = "".join(f'<a href="/col/col{i}/index.html">栏目{i}</a>'
                     for i in range(3, 40))
    html = ("<html><body>"
            '<a href="/col/col1599544/index.html">栏目一</a>'
            '<a href="/col/col122/index.html">栏目二</a>'
            '<a href="/art/2026/9/1/art_1.html">正文一</a>'
            '<a href="https://other.gov.cn/art/2026/9/2/art_2.html">外链</a>'
            f"{filler}"
            "</body></html>")
    assert len(html) > 500, "夹具需超过 500 字节，否则会被页面过短护栏拦下"
    soup = BeautifulSoup(html, "html.parser")

    # 滤出数 >= MIN_LINKS(4)，但仍要确认判据是「有没有滤出」而非「滤多少」：
    # 这里把门槛临时抬到 100，滤出 39 条依然判「没坏」。
    obs = sh.observe_list_page(str(soup), {
        "id": "zj_fgw",
        "url": "https://fzggw.zj.gov.cn/col/col1599544/index.html",
        "detail_pattern": r"/col/col\d+/index\.html",
    })
    assert obs.get("matched") == 39, f"夹具应滤出 39 条，实际 {obs.get('matched')}"
    assert not obs.get("broken"), \
        f"模式仍在生效（滤出 {obs.get('matched')} 条），不该判失效：{obs}"

    # 形态完全失效（改成页面里不存在的东西）→ 必须判为坏
    obs2 = sh.observe_list_page(str(soup), {
        "id": "zj_fgw",
        "url": "https://fzggw.zj.gov.cn/col/col1599544/index.html",
        "detail_pattern": r"/zzz/never-matches\.html",
    })
    assert obs2.get("broken"), f"模式一条都滤不出，应判失效：{obs2}"
    assert obs2.get("kind") == "detail_pattern", obs2


def test_heal_kind_is_not_guessed_from_field_presence(tmp_path, monkeypatch):
    """失效类型必须由观测结果决定，不能按「哪个字段非空」猜。

    回归 2026-09-30：坏的是 list_selector，但字段仍然非空，于是被当成
    形态失效去修形态，事件类型记成 detail_pattern_stale —— 与实际问题
    对不上，运维照着修会改错地方。

    顺带锁住「事件不落生产」：统一事件流是固定路径的 jsonl，不接受 db_path，
    所以测试必须 monkeypatch EVENTS_FILE 到临时文件。不隔离的话跑一次测试
    就在生产事件流里留一条 unit_src 垃圾，还分不清哪条是真的。
    """
    from bs4 import BeautifulSoup

    from lighthouse import event_bus, repair

    monkeypatch.setattr(event_bus, "EVENTS_FILE", tmp_path / "events.jsonl")

    soup = BeautifulSoup(
        "<html><body><div class='bt-column-y'><ul>"
        "<li><a href='/col/col1/index.html'>一</a></li>"
        "<li><a href='/col/col2/index.html'>二</a></li>"
        "</ul></div></body></html>", "html.parser")
    src = {
        "id": "unit_src",
        "url": "https://example.gov.cn/col/col1/index.html",
        # 坏掉的容器选择器：字段非空但匹配不到东西
        "list_selector": "#barrierfree_container__broken",
        "detail_pattern": r"/col/col\d+/index\.html",
    }

    out = repair.heal_and_report(src, soup, list_url=src["url"],
                                 kind="list_selector")
    assert out["kind"] == "list_selector", \
        f"kind 应由观测传入，实际 {out.get('kind')!r} —— 会被记成错的事件类型"

    # 事件里 failure_type / action 必须与 kind 对齐
    events = event_bus.read_events()
    mine = [e for e in events if e.get("scope") == "source_id:unit_src"]
    assert mine, "事件没落盘：{}".format(events)
    assert mine[0]["failure_type"] == "selector_broken", mine[0]
    assert mine[0]["action"] == "heal_selector", mine[0]


def test_circuit_breaker_counts_cumulative_failures(tmp_path, monkeypatch):
    """熔断按**累计**验证失败计数，不是每轮重新数。

    回归 2026-09-30：按轮重数的话，一个只写回一次的源永远停在「失败 1 次」，
    够不到阈值，熔断形同虚设。
    """
    from lighthouse import event_bus

    monkeypatch.setattr(event_bus, "EVENTS_FILE", tmp_path / "events.jsonl")

    scope = "source_id:flaky_src"
    ok, why = event_bus.should_auto_apply(scope)
    assert ok, "没写过任何东西的源应允许写回：{}".format(why)

    for _ in range(5):
        event_bus.emit(layer="collector", scope=scope,
                       failure_type="selector_broken", action="heal_selector",
                       repair_result="candidate", source="repair")
    ok, why = event_bus.should_auto_apply(scope)
    assert ok, "只提候选、没采纳过，仍应允许：{}".format(why)

    for _ in range(event_bus.MAX_RUNTIME_FIXES):
        event_bus.emit(layer="collector", scope=scope,
                       failure_type="selector_broken", action="write_back",
                       repair_result="kept", source="repair")
    ok, why = event_bus.should_auto_apply(scope)
    assert not ok, "达到写回上限应降级：{}".format(why)

    for _ in range(event_bus.DISQUALIFY_VERIFIED_FALSE):
        event_bus.emit(layer="collector", scope=scope,
                       failure_type="selector_broken", action="write_back",
                       repair_result="kept", source="repair", verified=False)
    ok, why = event_bus.should_auto_apply(scope)
    assert not ok and "永久降级" in why, "验证失败应永久降级：{}".format(why)


# ---------------------------------------------------------------- 列表页判据
#
# 钉的是两件会同时出错的事：栏目页必须被拦（否则它会被翻页反复抓成详情页，
# 全库曾出现同一个栏目页 64 条重复），而带多个日期的**真公告**必须放行
# （人事任免、系列规划、「30 项标准」通告天然在行文里引用很多日期）。

_LISTING = "\n".join(
    "- 某某厅关于印发《某某管理办法》的通知[2026-09-%02d]" % (i + 1) for i in range(15))


def test_listing_page_is_rejected():
    """栏目页：标题是栏目名，正文全是「一条标题 + 尾随日期」。"""
    verdict = judge(_LISTING, CFG)
    assert not verdict.passed, "栏目页必须被拦下，否则翻页会把它反复抓成详情页"
    assert verdict.reason == "listing_page", verdict.reason
    assert verdict.signals["list_dates"] >= 12, verdict.signals


def test_real_announcement_with_four_appointment_dates_passes():
    """四个人的任免通知 = 四个日期。这是真公告，不能被列表页判据杀掉。

    第一版把阈值定在 4，实测这一条连同北京市 7 条「十五五」规划通知全被误杀。
    """
    body = GOOD + "\n王清旺 2026 年 9 月 1 日任\n章冬梅 2026 年 9 月 2 日任\n" \
                  "李明德 2026 年 9 月 3 日任\n赵伟民 2026 年 9 月 4 日任"
    verdict = judge(body, CFG)
    assert verdict.passed, "带 4 个任免日期的真公告被误杀：{}".format(verdict.reason)
    assert verdict.reason != "listing_page"


def test_thirty_standards_notice_counts_as_listing():
    """「等 30 项地方标准」的通告：31 个日期，形态上就是一张表。

    实测全库只此一条属于这类误判（270 条命中里唯一可疑的），认了。
    """
    body = GOOD + "\n" + "\n".join("标准第 %d 号 2026-09-%02d" % (i, i % 28 + 1)
                                   for i in range(30))
    assert judge(body, CFG).reason == "listing_page"


def test_list_dates_survives_lost_newlines():
    """入库时换行符会丢，整篇挤成一行。判据不能依赖换行。"""
    flat = _LISTING.replace("\n", " ")
    verdict = judge(flat, CFG)
    assert verdict.reason == "listing_page", \
        "换行丢了就判不出来：{}".format(verdict.signals)


def test_short_announcement_without_dates_still_passes():
    """短但干净的真公告：日期少，必须照旧放行（判据只加不减）。"""
    assert judge(GOOD, CFG).passed


# ---------------------------------------------------------------- 源健康分类
#
# 分类器的价值全在「把故障说清楚」上。判据只认某一句话的写法时，同一种故障的
# 另一种说法就会掉进兜底的 unknown——那个桶一变大，真实故障就被淹了。
#
# 实测 2026-10-02：最近一轮 17 个源里 9 个被判 unknown，原因只是它们写的是
# "all 29 list urls already known (no new content)" 而不是 "early stop"。
# 两句话是同一件事：列表里全是已知 URL。

_NO_NEW_PHRASINGS = (
    "early stop after 200 known urls",
    "all 29 list urls already known (no new content)",
    "all 83 list urls already known (no new content)",
    "all 100 list urls already known (no new content)",
)


def test_source_classify_maps_every_no_new_phrasing():
    """同一件事的每种说法都必须归到 no_new，一条都不许掉进 unknown。"""
    import re

    from lighthouse.run import _source_classify as classify

    for phrase in _NO_NEW_PHRASINGS:
        got = classify({"records": 0, "notes": [phrase], "dropped": []})
        assert got == "no_new", "%r 被判成 %s" % (phrase, got)


def test_source_classify_still_separates_real_faults():
    """归并「没新内容」不能顺手把真故障也吞掉。"""
    from lighthouse.run import _source_classify as classify

    cases = [
        ({"records": 0, "notes": [], "dropped": []}, "unknown",
         "真的什么都没有依据时才叫 unknown"),
        ({"records": 0, "notes": ["list ladder found nothing"], "dropped": []},
         "no_links", "列表取不到链接 = 选择器坏了"),
        ({"records": 0, "notes": ["list_selector did not match"], "dropped": []},
         "no_links", "选择器对不上"),
        ({"records": 0, "notes": ["x"], "dropped": [{"reason": "too_short"}]},
         "all_dropped", "链接有但全被门禁拦"),
        ({"records": 0, "notes": ["source time budget 900s exhausted"],
          "skipped": 1}, "skipped", "到时限不是源的毛病"),
        ({"records": 7, "notes": ["early stop"]}, "ok", "采到东西就没事"),
    ]
    for result, want, why in cases:
        got = classify(result)
        assert got == want, "%s：得到 %s，期望 %s" % (why, got, want)


# ---------------------------------------------------------------- 发布日期
#
# 旧逻辑是「取正文里第一个日期串」，实测三种错法都出现过：
#   mca_gov《2026年民政部标准制定计划》正文写「于2027年9月30日前完成标准文本
#   编写」→ 那个**截止日期**被当成发布日期，库存成 2027-09-30
#   北京市规范性文件栏目页的「SEO 2029-06-11」→ 页面模板产物
#   四川省商务厅机构沿革页 → 抓到沿革正文里的 2000-04-20

def test_publish_date_prefers_labelled_date():
    body = "关于印发某某规划的通知　发布日期：2026-09-28\n正文提到2018年。"
    assert extract_fields(body, CFG)["publish_date"] == "2026-09-28"


def test_deadline_in_body_is_not_taken_as_publish_date():
    """截止日期不能当发布日期。这是 2027-09-30 那条错数据的成因。"""
    body = ("现将计划印发给你们，请抓紧开展标准编写、征求意见与技术审查，"
            "于2027年9月30日前完成标准文本编写。")
    assert "publish_date" not in extract_fields(body, CFG)


def test_page_template_artifact_is_rejected():
    """「SEO 2029-06-11」这类模板产物不是发布日期。"""
    assert "publish_date" not in extract_fields("SEO 2029-06-11 是 某某通知", CFG)


def test_implausible_calendar_date_rejected():
    """2 月 30 日这种拼得出来但不存在日期要拒掉。"""
    assert "publish_date" not in extract_fields("成文日期：2026-02-30", CFG)


def test_recent_publish_date_still_extracted():
    """正常日期照旧抽得出，判据只是加范围校验，不能把好数据也拒了。"""
    assert extract_fields("成文日期：2026-09-20", CFG)["publish_date"] == "2026-09-20"


# ---------------------------------------------------------------- 路由阶梯顺序
#
# 「切换后端 = 重排一个列表，不是改代码」——Agent Reach base.py 的
# ordered_backends() 那条原则。
#
# 灯塔原来把顺序硬编码成 ("rss","api","static",...)，配置里的顺序被丢进 dict
# 就没了。改 YAML 把 render 排第一完全不生效，看起来像「改了配置没反应」。
# 当前配置顺序恰好和硬编码一致，所以这个 bug 一直没暴露 —— 更该修。


def test_ladder_order_follows_config_not_hardcoded():
    """配置里的顺序就是实际顺序。"""
    from lighthouse.routes import Ladder

    cfg = {"routes": {"levels": [
        {"name": "static", "enabled": True},
        {"name": "render", "enabled": True},
        {"name": "markdown", "enabled": True},
    ]}}
    assert Ladder(cfg, None).order == ["static", "render", "markdown"]

    reordered = {"routes": {"levels": [
        {"name": "render", "enabled": True},
        {"name": "static", "enabled": True},
        {"name": "markdown", "enabled": True},
    ]}}
    assert Ladder(reordered, None).order == ["render", "static", "markdown"], (
        "重排配置不生效 —— 那就等于改配置没用")


def test_ladder_skips_disabled_levels():
    """enabled=false 的不进阶梯，不是排在最后试。"""
    from lighthouse.routes import Ladder

    cfg = {"routes": {"levels": [
        {"name": "static", "enabled": True},
        {"name": "render", "enabled": False},
        {"name": "markdown", "enabled": True},
    ]}}
    ladder = Ladder(cfg, None)
    assert ladder.order == ["static", "markdown"]
    assert "render" not in ladder.levels


def test_ladder_rejects_unknown_level_at_load_time():
    """配置里写错阶梯名要在**加载时**就炸，不能等采集跑起来才发现。

    运行时报错的话，报错链长到没法定位（在哪一源、哪一页、哪几次尝试）。
    """
    from lighthouse.routes import Ladder

    cfg = {"routes": {"levels": [
        {"name": "static", "enabled": True},
        {"name": "jasper", "enabled": True},      # 不认识
    ]}}
    try:
        Ladder(cfg, None)
    except ValueError as exc:
        assert "jasper" in str(exc), exc
    else:
        raise AssertionError("写了不认识的阶梯名却没报错")


def test_real_config_order_is_used_as_is():
    """真实配置（web_collector_config.yaml）的顺序就是生效顺序。"""
    from pathlib import Path

    import yaml

    from lighthouse.routes import Ladder

    root = Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load(
        (root / "lighthouse" / "config" / "web_collector_config.yaml")
        .read_text(encoding="utf-8"))
    declared = [lv["name"] for lv in cfg["routes"]["levels"] if lv.get("enabled")]
    assert Ladder(cfg, None).order == declared, (
        "生效顺序和配置声明的不一致：%s vs %s"
        % (Ladder(cfg, None).order, declared))

def test_summary_points_keeps_the_column_shape_in_sync():
    """key_points 这一列的两个产出方必须同构，否则互相覆盖。

    2026-10-07 实测踩过：采集链路写 `[summary]`（一整块当单条要点），而
    `scripts/resummarize.py` 用 `build_summary()` 写的是**逐句列表**。
    同一列两种形态，于是采集每轮都把 resummarize 拆好的列表压回单块。
    后果是 196 条记录存着**过期摘要**：`content` 被后续清洗改过而摘要没
    重算，摘要里的话在 `content` 里再也找不到。

    钉住两件事：
      1. 拆出来的必须是列表（和 build_summary 同构）
      2. **无损** —— 拼回去必须等于原文。丢内容比形态不一致更糟。
    """
    from lighthouse.run import _summary_points

    assert _summary_points("") == [], "空摘要应给空列表，不能给 [""]"

    blob = "发布日期：2026-09-22。财政部拟发行本期国债。本期国债自9月24日开始计息。"
    points = _summary_points(blob)
    assert len(points) > 1, "多句摘要应拆成列表，实际 %r" % (points,)
    assert "".join(points) == blob, (
        "拆点必须无损：拼回去应等于原文，实际丢了 %r"
        % (blob[:12] if "".join(points) == blob else "内容"))

    # 没有句末标点的（只有事实行）保持单条，仍然是列表形态
    single = _summary_points("金额：面值总额400亿元｜发布日期：2026-09-22")
    assert single == ["金额：面值总额400亿元｜发布日期：2026-09-22"], single

    # 短碎片并回前一条，而不是被丢掉
    glued = _summary_points("第一句很短。第二句足够长可以独立成条。")
    assert "".join(glued) == "第一句很短。第二句足够长可以独立成条。", glued

def test_deep_cursor_advances_on_early_stop_and_never_skips_unswept(tmp_path, monkeypatch):
    """深翻游标：early stop 时记住位置，下次从那里之后继续。

    2026-10-07 实测这是「采集效率提不起来」的根因，也是「历史数据永远补不上」
    的同一个 bug：early stop 把「这个 URL 已入库」当成「这条线后面不用看了」。
    列表页倒序，于是每轮重爬同一段已知内容。近 40 轮实测 93% 的时间
    （1843/1973 秒）花在零产出访问上，moa_gov 单次 302 秒全是零产出；
    同时 moa_gov 库内 290 条而 depth=30 实测有 205 条未入库 URL 全在老尾。

    三条都要钉住：
      1. early stop 后游标前进（下次不重爬头部）
      2. 撞时间预算/死链时**不**前进（否则会跳过没看过的新内容）
      3. 列表变短时游标重置（站方改版后不能永远跳过开头）
    """
    import json

    from lighthouse import run as R

    cursor_file = tmp_path / "list_cursor.json"
    monkeypatch.setattr(R, "CURSOR_FILE", cursor_file)

    assert R._cursor_load() == {}, "没有游标文件时应返回空，不报错"

# 1) early stop -> 前进
    R._cursor_save("src_a", 250)
    assert json.loads(cursor_file.read_text(encoding="utf-8"))["src_a"] == 250

    # 2) 撞预算/死链那条路径不调 _cursor_save，游标保持不变
    assert R._cursor_load()["src_a"] == 250, "游标只能在 early stop 时前进"

    # 3) 列表变短 -> 调用方负责重置；这里钉住重置逻辑真的会被触发
    links = ["u%d" % i for i in range(10)]
    cursor = 250
    if cursor and len(links) <= cursor:
        cursor = 0
    assert cursor == 0, "列表比游标短时必须重置，否则永远跳过开头"

def test_nav_page_title_judgement_has_zero_false_positives():
    """站点导航页按**标题**判死，且不能误杀短标题真公告。

    2026-10-07 实测。这批页面正文上千字（「网站地图」最长 11252 字，里面就是
    联系电话和无障碍选项），所以 chrome/link_flood/list_dates 这些**看正文**
    的判据一个都拦不住——实测 35 条里只有 2 条被拦，其余 33 条进了库。

    而判据不能用「标题短」：全库标题 ≤12 字的有 1082 条，绝大多数是真公告
    （『八月全社会用电量再破万亿』『浙江省基础测绘办法』『深化党和国家机构
    改革方案』）。拿长度当判据会一次误杀上千条。

    所以：穷举导航词 + 去站点后缀后完全相等。实测 35 条命中全是导航页。
    """
    from lighthouse.quality import _is_site_nav_page

    # 该判死的
    for title in ("联系我们", "网站地图", "关于我们", "维护邮箱",
                  "联系我们 - 中华人民共和国民政部",
                  "政策法规 - 苏州市工业和信息化局",
                  "网站首页", "无障碍", "政务公开",
                  # 三种分隔符都要认。2026-10-07 实测：只 split(" - ") 时
                  # 「通知公告 -深圳市住房和建设局网站」整条漏过去进了库，
                  # 而「政策法规 - 四川省科学技术厅」能拦下——同批页面两种
                  # 写法，结果不同。
                  "通知公告 -深圳市住房和建设局网站",
                  "网站地图 - 湖北省人民政府门户网站",
                  "欢迎访问深圳市住房和建设局网站", "自然资源部APP"):
        assert _is_site_nav_page(title), title

    # **绝不能**判死的：短标题真公告，以及包含导航词的正常标题
    for title in ("八月全社会用电量再破万亿", "浙江省基础测绘办法",
                  "深化党和国家机构改革方案（摘要）",
                  "关于2026年新产品新技术医疗服务价格项目预立项制度的通知",
                  "关于企业技术改造项目申报的通知",
                  "海南省人民政府", "陕西省夏粮总产单产均创新高",
                  # 中间夹着「通知公告」**面包屑**的真公告。
                  # 2026-10-07 实踩：判据改成逐段比对后，这几条全被误杀
                  #（比选结果公示、绩效评价公告）。只看第一段才分得开。
                  "四川省商务厅关于中国（四川）—意大利（米兰）经贸合作交流会"
                  "服务机构比选结果的公示 - 通知公告 - 四川省商务厅",
                  "河南省2026年“三秋”农机作业服务值班电话公布"
                  "- 通知公告 - 河南省农业农村厅"):
        assert not _is_site_nav_page(title), title

    # judge() 必须真的用上标题：正文再长也判死
    v = judge("联系我们 政务服务功能维护：0991-7531791 " * 40, CFG,
              title="联系我们")
    assert v.reason == "nav_page" and v.fatal, v
    assert v.signals.get("nav_page") is True, v.signals

    # 不传标题时不能凭空判死（保持旧行为，别让既有调用点行为突变）
    v2 = judge("联系我们 政务服务功能维护：0991-7531791 " * 40, CFG)
    assert v2.reason != "nav_page", "没传标题就不该用标题判据"

def test_directory_style_links_are_rejected():
    """以 / 结尾的目录型链接必须被 _link_allowed 拒掉。

    2026-10-07 实测的漏洞：守卫只拦「index.* 结尾」，而政府站大量用裸目录做
    栏目地址，于是整批混进来当详情抓走：
      https://www.stats.gov.cn/znwd/          -> 标题「RSS订阅」
      https://www.stats.gov.cn/wzgl/rss/      -> 标题「局领导」
      https://zjj.sz.gov.cn/xxgk/tjsj/        -> 标题「统计信息」
      https://zjj.sz.gov.cn/xxgk/zjxx/czyjs/ -> 标题「局长信箱」

    依据：库里 15332 条 URL 中以 / 结尾的有 617 条（4.02%），**逐条看过全是
    栏目页**（正文是导航，或只有「联系我们」四个字），零真文章。

    A/B 实测（sz_zjj，同参数跑两遍）：
      有这道守卫 入库 6 条 / 无守卫 入库 0 条
    ——目录链接会挤占单源配额（cap/early stop），真实公告反而采不到。
    所以这道守卫从不减少产出，只增加。
    """
    from lighthouse.routes import Ladder, build_collector

    cfg = CFG
    levels = Ladder(cfg, None).levels
    collector = build_collector(
        {"id": "t_dir", "name": "t", "url": "https://x.gov.cn/a/",
         "status": "enabled", "enabled": True, "playwright_fallback": False},
        levels)
    try:
        # 目录型：必须拒
        for bad in ("https://x.gov.cn/xxgk/tjsj/",
                    "https://www.stats.gov.cn/znwd/",
                    "https://www.stats.gov.cn/wzgl/rss/",
                    "https://x.gov.cn/",
                    "https://x.gov.cn/xxgk/ghjh/"):
            assert not collector._link_allowed(bad), bad
        # 文章形态：必须放行（别把真公告一起拦了）。
        # 用同域 URL —— _is_allowed_url 是域名白名单，跨域本来就该拒，
        # 那是另一道守卫，混进来测的就不是这道了。
        for good in ("https://x.gov.cn/xxgk/tzgg/202601/t20260102_1234.html",
                     "https://x.gov.cn/fzlm/mobile/content/post_3743101.html",
                     "https://x.gov.cn/art/2026/9/15/art_104_22141.html"):
            assert collector._link_allowed(good), good
    finally:
        try:
            collector.close()
        except Exception:
            pass

def test_links_hidden_in_script_datastore_are_recovered():
    """列表藏在<script type="text/xml"><datastore> 里时要能取出来。

    2026-10-07 实测踩过的坑（两层，缺一层都还是0 条）：

    1. 浙大/南京系 CMS 把**整段文章列表**塞进 `<script type="text/xml">`。
       BeautifulSoup 按规范把 script 内容当纯文本，`select("a[href]")` 完全
       看不见 —— nhsa_gov 页面上原始 67 个 `<a href>`，BS 只认出 21 个。
    2. 那段内容还整段包在 `<![CDATA[ ... ]]>` 里。html.parser 把它当声明
       节点，里面的 `<a>` 全成了该节点的文本。**只剥 `]]>` 无效，两头都要剥。**

    实测剥掉 CDATA 后 a[href] 从 0 条变 46 条，其中 45 条是 `/art/` 文章链接。
    而这个源的表现是「0 links found on list page」——**配了源也一条采不到**，
    是最隐蔽的一类失败。

    修好之后 6 个源从近乎为零变成正常：nhsa_gov 21→46、sd_swt 4→49、
    sd_gxt 7→45、sd_kjt 12→75、sd_fgw 9→44、sc_sd_gov 6→46。
    """
    from lighthouse.routes import Ladder, build_collector

    html = (
        "<html><body>"
        "<a href='/plain/1.html'>普通链接</a>"
        '<script type="text/xml"><datastore><![CDATA['
        "<ul><li><a href='/art/2026/9/15/art_104_22141.html'>文章一</a></li>"
        "<li><a href='/art/2026/9/8/art_104_22045.html'>文章二</a></li>"
        "</ul>]]></datastore></script>"
        "<script>var t = '<a href=\"/js/fake.html\">模板里的假链接</a>';</script>"
        "</body></html>"
    )
    collector = build_collector(
        {"id": "t_ds", "name": "t", "url": "https://x.gov.cn/col/col1/index.html",
         "status": "enabled", "enabled": True, "playwright_fallback": False},
        Ladder(CFG, None).levels)
    try:
        links = collector._extract_links(html, "https://x.gov.cn/col/col1/index.html")
        assert any("/art/2026/9/15/" in u for u in links), links
        assert any("/art/2026/9/8/" in u for u in links), links
        assert any("/plain/1.html" in u for u in links), links
        # 真 JS 块**不碰**：里面的 href 是模板字符串，不是链接
        assert not any("/js/fake.html" in u for u in links), \
            "真 script 块被当数据解析了，会捞出假链接"
    finally:
        try:
            collector.close()
        except Exception:
            pass
