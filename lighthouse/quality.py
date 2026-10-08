"""正文质量判据 + 字段拼装式摘要。

为什么这里不做「摘要算法」——实测依据
--------------------------------------
对全库 2492 条记录实测：

    摘要覆盖率            98%      不是「没有摘要」
    句中硬切              26.4%    在字符数处截断，不在句号停
    表格竖线倒灌           9.8%    整个表头行进了摘要
    正文原样开头           10.5%    等于没摘要
    公文结构句式覆盖       12.1%    「现将…通知如下」这类句式只占八分之一

病根是**选句**不是生成。id=2961 是《工作指引》+ 42 篇案例汇编共 71506 字，
摘要从第 40 篇里挑了「广外街道」「蓝梅采摘园 200 亩」，再把另一篇案例的
「268 万元」当成文件投资额拼在头部——**句子全部忠于原文，但拼起来答非所问**。

试过 TextRank 系（中心性）：在案例汇编里中心性最高的是每篇都重复的样板句
（「加强领导、健全机制」），不是「这份文件要做什么」的论断句。又试过 20 行
Lead-3 + 标题锚定，4 条样本里赢 2 平 1 输 1。**换算法不改变结论。**

所以这里把摘要重新定义为：**结构化字段 + 首段完整句的模板渲染**。
这样三条性质是构造上成立的，不依赖任何算法：

    1. 100% 在句号/分号处收尾   —— 不是字符数截断
    2. 100% 抄原文             —— 逐句复制，不生成
    3. 不含目录行/编号行/表格行 —— 构造时就被过滤

代价要认：它不是「读 200 字就懂这份 7 万字文件」。它是名片。
长文件靠 output/markdown.py 里的「关键要点」+ 完整原文承载。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping

# 采集器抓回来时可能带 Markdown 链接/图片占位符（crawl4ai fit_markdown 的输出形态）
_MD_LINK = re.compile(r"!?\[[^\]]{0,60}\]\([^)]*\)")
# 面包屑：>_ [首页](url) > [政务公开](url) > ...
_CRUMB_LINE = re.compile(r"^[\s>_*]*\[[^\]]{1,20}\]\([^)]*\)[\s>_*]*$")
# 首行 "标题 | 列1 | 列2 |" 这类表头残留
_TABLE_ROW = re.compile(r"^[^|]{0,40}\|.*\|.*\|")

# strip_noise 用的 chrome 词表，由 set_chrome_patterns() 在加载配置后注入。
# 空列表时 strip_noise 退化成原来的行为（只滤 markdown 面包屑），
# 这样单测不依赖配置文件也能跑。
_CHROME_TEXT: list[re.Pattern[str]] = []


def set_chrome_patterns(patterns: list[str]) -> None:
    """把配置里的 chrome_patterns 交给 strip_noise 用。

    判定（judge）和清洗（strip_noise）必须看同一批词。分成两套时，清洗永远
    追不上判定——2026-10-02 实测：词表里早就有「网站标识码」「版权所有」，
    摘要里照样漏出去，因为 strip_noise 只认 markdown 链接格式。
    """
    global _CHROME_TEXT
    _CHROME_TEXT = [re.compile(p) for p in (patterns or [])]
_WS = re.compile(r"[ \t　]+")
# 列表行：一行就是一条目录项，行尾带日期。
#
# 覆盖两种政府站常见的目录写法：
#   「- 商务厅召开党组（扩大）会议总结全厅...[2026-09-11]」
#   「商务部关于印发某某通知的通知 2026-09-11」
_LIST_ITEM_DATE = re.compile(r"[\[\(（]?\s*(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2})\s*[\]\)）]?\s*$")

# 比例判据的绝对数量下限。低于这个数就按比例判没有意义 —— 短正文上几个字符
# 就能把占比顶穿。实测 2026-09-30：moe_gov 一条 265 字的人事任免新闻，页脚
# 命中 9 个 chrome 字符，9/265 = 3.4% > 3% 阈值，整篇被当成框架页丢掉。
# 真要拦的「满屏导航」chrome 字符数以百计，设 200 不会放过那类页面。
MIN_CHROME_CHARS = 200
MIN_LINK_CHARS = 200
# 剥掉 markdown 占位符后，至少要剩这么多**实质正文**才算「不是垃圾页」。
#
# 实测 2026-09-30（jshrss.jiangsu.gov.cn 一条真实公告）：crawl4ai 的 markdown
# 输出 4906 字符里 82% 是 `[文字](url)` 占位符，正文约 130 字。link_ratio 判它
# 满屏链接完全正确，但它是一条**合法公告**，只是正文天生短（内容在 .docx 附件
# 里）——光凭链接占比判死就是误杀。
#
# 100 是「有一段话」的最低门槛：低于它才是真的从头到尾没正文。
MIN_PROSE_CHARS = 100

# 列表页判据：正文里有多少个**目录式日期标记** —— 方括号包裹的
# `[2026-09-11]`，或者裸日期落在行尾（`……的通知 2026-09-11`）。
#
# 为什么要单独一道，而不是靠 link_flood：link_flood 看的是 markdown 链接占位符
# `[文字](url)` 的字符占比。可栏目页被抽取成纯文本之后链接早就变成普通文字，
# link_ratio 归零 —— 实测「党建工作」「通知公告」「视频新闻-四川省教育厅」
# 这类栏目页 link_ratio 全是 0.00，判据完全失效，一路穿过所有门禁被判「通过」。
#
# 后果不是单条脏数据：同一个栏目页会在翻页时被反复当成详情页抓。全库按正文
# 指纹去重认出 544 条重复，最大一组 64 条全是同一个「山东省人社厅」栏目页。
#
# 为什么不直接数所有日期：那会误杀。实测全库日期数分布 0-4 个的有 8420 条，
# 5-9 个的有 511 条——而 5-9 里面混着真公告（比亚迪召回公告 8 个、导游考试
# 通知 12 个，它们在行文里引用多个日期）。**只数目录式的**（方括号包裹或行尾）。
#
# 阈值 12 是全库实测定的，不是拍的。9473 条按此信号计数：0 个的 9021 条，
# 4-5 个的 115 条，>=12 的 241 条。
#
# 4-5 那档是真公告，杀不得——「北京市人民政府关于王清旺、章冬梅同志职务任免
# 的通知」有 4 个日期（四个人的任免日期），「十五五」系列规划通知同理。
# 第一版把阈值定在 4，实测误杀了北京市 7 条「十五五」规划通知和 2 条职务任免
# 通知，改成 12 之后清零。
#
# >=12 的样本清一色是栏目页：文件库_广东省政府(114)、专技人才_重庆人社局(91)、
# 政府数据_内蒙古自治区政府(70)、民生服务 民生聚焦|便民通告(40)。
#
# 方括号形态单独看更干净（9449 条为 0，只有 24 条 >=5），但覆盖面小——
# 很多站的换行符在入库时丢了，日期既不成行尾也不带括号。两种形态取大值。
MIN_LIST_DATES = 12
_BRACKET_DATE = re.compile(
    r"[\[(（]\s*20\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?\s*[\])）]")
# 行尾日期：re.M 让 $ 匹配每行末尾。没有 re.M 的话整篇只会算最后一个。
_LINE_END_DATE = re.compile(
    r"20\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?\s*[\])）]?\s*$", re.M)


# 标题命中站点通用导航词 —— 抓的是「**整页就是导航**」而不是「正文短」。
#
# 为什么不用「标题短」当判据：实测全库标题 ≤12 字的有 1082 条，其中绝大多数
# 是真公告（『八月全社会用电量再破万亿』『浙江省基础测绘办法』『深化党和国家
# 机构改革方案』）。拿长度当判据会一次误杀上千条。2026-10-07 实测。
#
# 为什么不用现有的 chrome/link_flood/list_dates：这些都看**正文**。而导航页
# 的正文长得跟正常页面一模一样（『联系我们』有 941 字、11252 字的都有，
# 里面就是电话和地址），比例判据全失效。实测这 35 条里只有 2 条被现有判据
# 拦住，其余 33 条一路穿过所有门禁进了库。
#
# 判据必须窄。实测 35 条命中记录**全部**是导航页（逐条看过：联系电话、
# 无障碍选项、机构简介），零误杀，所以用穷举词表而不是启发式。
#
# 匹配方式：标题去掉「 - 站点名」后缀后**完全相等**。不用包含匹配——
# 「关于XX补贴的通知」这类真公告会被包含匹配误杀。
_SITE_NAV_TITLES = frozenset({
    "维护邮箱", "手机移动版", "微博", "微信", "联系我们", "网站首页",
    "无障碍", "网站地图", "公众参与", "专题专栏", "信息公开", "政务公开",
    "机构", "职能", "关于我们", "邮箱", "投稿", "订阅", "打印", "关闭",
    "返回", "更多", "首页", "English", "简体", "繁体", "长者模式",
    "设为首页", "加入收藏", "登录", "注册", "导航",
    "新媒体", "公众服务智能平台", "服务大厅", "办事大厅",
    # 栏目名。实测库里有「政策法规 - 四川省科学技术厅」（正文 492 字，纯栏目
    # 列表）。这些词单独成标题时只可能是栏目页——真公告不会只写「政策法规」
    # 四个字就说完了。
    "政策法规", "工作动态", "图片新闻", "视频新闻", "通知公告",
    "公告公示", "规划计划", "政策解读", "行政审批",
})


# 标题里的站点名后缀分隔符。政府站三种写法都见过：全角破折号两侧空格、
# 只有左边空格、只有右边空格。2026-10-07 实测踩过：判据只 split(" - ") 时，
# 「通知公告 -深圳市住房和建设局网站」整条漏过去进了库——而
# 「政策法规 - 四川省科学技术厅」能拦下。同一批页面，两种写法结果不同。
_TITLE_SUFFIX_SPLIT = re.compile(r"\s*[-\u2014\u2013\u2500]+\s*")

# 形态类导航标题。都是 2026-10-07 实采时**实际观察到的**，不是想象出来的：
#   「欢迎访问XX局网站」「自然资源部APP」「公众服务智能平台」「新媒体」
# 这些是站点的下载/入口链接，不是公告。逐条看正文确认过（正文就是
# 「扫描二维码下载APP」之类）。
_NAV_TITLE_PATTERNS = (
    re.compile(r"^欢迎访问"),            # 门户落地页标题
    re.compile(r"(APP|客户端|公众号)$", re.I),   # 下载入口
)


def _is_site_nav_page(title: str) -> bool:
    """标题是不是站点通用导航页的标题。

    只比对**第一段**（剥掉「- 站点名」后缀之后的部分），不是「任何一段」。

    2026-10-07 实测踩过这个坑：标题经常是
    「四川省商务厅关于…比选结果的公示 - 通知公告 - 四川省商务厅」——
    中间那个「通知公告」是**面包屑**不是后缀。逐段比对会把它当栏目页，
    一次误杀 5 条真公告（含比选结果公示、绩效评价公告）。只看第一段就分得开：
      「通知公告 -深圳市住房和建设局网站」  第一段=通知公告 → 判死
      「四川省商务厅关于…公示 - 通知公告 - …」 第一段=真标题 → 放行
    """
    text = str(title or "").strip()
    if not text:
        return False
    if text in _SITE_NAV_TITLES:
        return True
    if any(p.search(text) for p in _NAV_TITLE_PATTERNS):
        return True
    head = _TITLE_SUFFIX_SPLIT.split(text)[0].strip()
    return head in _SITE_NAV_TITLES


def _count_list_dates(text: str) -> int:
    """数目录式日期标记。取两种形态的较大值，不相加——
    同一个日期可能同时匹配两种，加起来会重复计数。"""
    return max(len(_BRACKET_DATE.findall(text)),
               len(_LINE_END_DATE.findall(text)))


def load_config(path: str | None = None) -> dict[str, Any]:
    """读 YAML 配置。所有可调项都在那里，代码里不写死。"""
    import pathlib
    import yaml

    if path is None:
        path = str(pathlib.Path(__file__).resolve().parent / "config" / "web_collector_config.yaml")
    with open(path, "r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}
    # 读配置就把 chrome 词表交给 strip_noise，这样任何调 load_config() 的人
    # 自动拿到清洗能力，不需要各自记得再调一次 set_chrome_patterns()。
    # 之前摘要漏「网站标识码/版权所有」就是因为没有一个统一入口。
    set_chrome_patterns((cfg.get("quality") or {}).get("chrome_patterns") or [])
    return cfg


# ------------------------------------------------------------------ 质量判据

@dataclass
class Verdict:
    """一条正文通过 / 不通过质量门禁的判定结果。

    区分两件本来被混在一起的事——这是 bench 实测全库 2492 条后改的：

    * ``fatal`` —— 内容是垃圾或根本没内容。这才值得升级到更贵的渲染级
      （chrome / link_flood / toc_page / 短到连一个完整句都没有）。
    * ``fatal=False`` —— 内容是好的，只是短。**不要升级**：渲染要花 10-50 倍
      成本，而且不会让一篇 300 字的完整通告变长。直接接受并出摘要。
    """

    passed: bool
    reason: str = ""            # 不通过时的机器可读原因
    fatal: bool = False         # True=升级/丢弃；False=内容可用，只是短
    score: float = 0.0          # 0..1，仅用于报告和排序，不参与门禁
    signals: dict[str, Any] = field(default_factory=dict)


def _patterns(cfg: Mapping[str, Any], section: str, key: str) -> list[re.Pattern[str]]:
    raw = ((cfg.get("quality") or {}).get(key) or [])
    return [re.compile(p) for p in raw]


def judge(content: str, cfg: Mapping[str, Any],
          title: str = "") -> Verdict:
    """判断一条正文能不能用。

    降级阶梯靠这个函数决定要不要升级到下一级渲染方式，所以判据必须是
    **正文质量**而不是「HTTP 返回 200」——政府栏目页返回 200 但正文是
    导航文字，这是最常见的假成功。
    """
    text = content or ""
    chars = len(text)
    limits = cfg.get("quality") or {}
    min_chars = int(limits.get("min_content_chars", 800))
    min_summary = int(limits.get("min_summary_chars", 200))
    max_chrome = float(limits.get("max_chrome_ratio", 0.03))
    max_link = float(limits.get("max_link_ratio", 0.25))
    max_toc = int(limits.get("max_toc_run", 8))

    chrome_res = _patterns(cfg, "chrome", "chrome_patterns")
    toc_res = _patterns(cfg, "chrome", "toc_patterns")

    # 站点框架文字**字符占比**。按比例不按次数：按次数（原来 max_chrome_hits: 0）
    # 会把 5833 字正文里偶然出现一个「网站地图」的正常文档也判死——实测全库时
    # 误杀了 61 条。真正要拦的是「满页都是导航」，那种占比在 20% 以上。
    chrome_chars = sum(len(m.group(0)) for r in chrome_res for m in r.finditer(text))
    chrome_hits = sum(1 for r in chrome_res if r.search(text))
    chrome_ratio = round(chrome_chars / chars, 4) if chars else 1.0

    # markdown 链接/图片占位符占掉的字符比例。
    # 这条专治「满屏面包屑」：data_health.py 的 CHROME_RE 看不见这种页面，
    # 但它从头到尾没有一个字是正文。
    link_chars = sum(len(m.group(0)) for m in _MD_LINK.finditer(text))
    link_ratio = round(link_chars / chars, 4) if chars else 1.0

    # **实质正文长度**：把 markdown 链接/图片占位符整段剥掉之后还剩多少字。
    #
    # 为什么光看 link_ratio 不够（实测 2026-09-30，jshrss.jiangsu.gov.cn）：
    # 一条真实公告「江苏发布《超龄劳动者用工协议（参考文本）》」，crawl4ai 的
    # markdown 输出 4906 字符里 82% 是 `[文字](url)` 占位符（栏目导航、字体
    # 大小按钮、附件图标），真正正文只有约 130 字 —— 判 link_flood 完全正确，
    # 页面从头到尾确实「满屏链接」。
    #
    # 但它不是垃圾页：它是一条合法公告，只是**正文天生短**（内容在 .docx
    # 附件里）。所以光凭 link_ratio 判死会误杀这类「短但真实」的公告。
    # 加上实质正文长度：剥掉占位符后还剩得够多 → 判为短正文（可用）而不是垃圾。
    prose = _MD_LINK.sub("", text)
    prose_chars = len(prose.strip())
    # 图片占位符（crawl4ai 会把 <img> 写成 ![](url)，上面的 _MD_LINK 已覆盖
    # 带 alt 文字的；裸的 <img ...> 标签再剥一次）
    prose_chars = len(re.sub(r"<img[^>]*>", "", prose).strip())
    prose_ratio = round(prose_chars / chars, 4) if chars else 0.0

    # 目录行连续长度
    toc_run = _longest_run(text.splitlines(), toc_res)

    # 列表页（栏目索引）：正文里有大量目录式日期标记，每条目录项一个。
    # 单篇公告的日期在句子里，是行文的一部分，量级差两个数量级。
    list_dates = _count_list_dates(text)

    # 标题层判据：站点通用导航页。看标题而不是正文（见 _SITE_NAV_TITLES
    # 的注释：这类页面正文有几百到上万字，比例判据全失效）。它对 fatal
    # 判定是决定性的——渲染再多次也变不成公告，升级只是烧钱。
    nav_page = _is_site_nav_page(title)

    signals = {
        "chars": chars,
        "chrome_hits": chrome_hits,
        "chrome_ratio": chrome_ratio,
        "link_ratio": link_ratio,
        "prose_chars": prose_chars,
        "prose_ratio": prose_ratio,
        "toc_run": toc_run,
        "list_dates": list_dates,
        "nav_page": nav_page,
    }

    # 垃圾内容：升级渲染有意义（DOM 里可能真有正文），全级失败就丢
    #
    # chrome 加一道**绝对数量**下限：比例在短正文上完全失真。实测 2026-09-30
    # moe_gov 的人事任免新闻（正文 265 字，页脚「版权所有：中华人民共和国
    # 教育部」等）只命中 9 个 chrome 字符 —— 9/265 = 3.4% > 3% 阈值，整篇被
    # 判死。9 个字的页脚不该杀死一条真新闻：真要拦的是「满屏导航」，那种页面
    # chrome 字符数以百计。低于下限时按比例判没有意义，直接放过。
    #
    # link_flood 同理多一道判据：剥掉 markdown 占位符后还剩多少**实质正文**。
    # 见上面 prose_chars 的注释（江苏人社厅那条 130 字真实公告）。低于下限
    # 时它不是「满屏链接的垃圾页」，只是「正文短」——判 short_but_clean，
    # 交给 min_summary_chars 那道关。
    if chrome_ratio > max_chrome and chrome_chars >= MIN_CHROME_CHARS:
        return Verdict(False, "chrome", True, 0.0, signals)
    if nav_page:
        # 判死且**不要**升级渲染：「联系我们」「网站地图」渲染一万次还是那些
        # 电话和地址。这条必须放在 chrome/link_flood 之前——它们全是正文字数
        # 上千字的页面，比例判据看不见，只有标题能认出。
        return Verdict(False, "nav_page", True, 0.0, signals)
    if link_ratio > max_link and link_chars >= MIN_LINK_CHARS:
        if prose_chars < MIN_PROSE_CHARS:
            # 满屏链接 + 剥掉后几乎没有实质正文 = 真的垃圾页（栏目索引、
            # 标签列表）。**必须在这里判死**：下面的长度门禁按 chars（原始
            # 长度）算，而满屏链接会把 chars 撑得很大 —— 实测一个 50 行的
            # 纯链接页 chars=1629，> min_summary(200) 也 > min_chars(800)，
            # 不在这里拦就会一路穿过所有门禁被判「通过」。
            signals["prose_too_thin"] = True
            return Verdict(False, "link_flood", True, 0.0, signals)
        # 链接占比高但**确有实质正文**（实测江苏人社厅那条 130~395 字的真实
        # 公告，crawl4ai 把导航也写成了 markdown 链接）：它不是垃圾页，只是
        # 正文天生短。放行给下面的长度门禁按「短正文」处理。
        signals["link_heavy_but_has_prose"] = True
    if toc_run >= max_toc:
        return Verdict(False, "toc_page", True, 0.0, signals)
    # 列表页（栏目索引）。放在 toc_page 之后、长度门禁之前：这类页面通常
    # chars 很大（几十条标题堆起来），只看长度一定会放过去。
    if list_dates >= MIN_LIST_DATES:
        return Verdict(False, "listing_page", True, 0.0, signals)

    # 短到连一个完整句都取不到：渲染也救不了，丢弃
    if chars < min_summary:
        return Verdict(False, "too_short", True, 0.0, signals)

    # 短但干净：内容可用，只是不到「值得花 50 倍成本升级渲染」的门槛。
    # passed=True / fatal=False —— 路由阶梯据此直接接受，不升级。
    if chars < min_chars:
        signals["below_escalate_bar"] = True
        return Verdict(True, "short_but_clean", False, 0.4, signals)

    # 通过。分数只用于报告里排序，不参与门禁
    score = min(1.0, round(min(chars / 2000.0, 1.0) * (1.0 - link_ratio), 4))
    return Verdict(True, "", False, score, signals)


def _longest_run(lines: list[str], res: list[re.Pattern[str]]) -> int:
    best = run = 0
    for line in lines:
        stripped = line.strip()
        if stripped and any(r.search(stripped) for r in res):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


# ------------------------------------------------------------------ 正文清洗

def strip_noise(text: str) -> str:
    """去掉采集器带回来的框架残留：markdown 链接、图片占位符、面包屑行。"""
    # 页脚/框架行：复用 judge() 用的同一份 chrome_patterns，不另立一套。
    #
    # 实测 2026-10-02：摘要里出现「网站标识码：3500000032 / 版权所有：©
    # 福建省科学技术厅 / 扫一扫在手机上查看当前页面」，因为 _CRUMB_LINE 只认
    # markdown 链接形式的���包屑（[x](url)），而这些页脚是**纯文本行**，
    # 一个都匹配不上。词表里本来就有「网站标识码」「版权所有」「扫一扫在手机
    # 打开当前页」——只是 strip_noise 从来没读过它。
    #
    # 一份词表两处用：判定和清洗必须看同一批词，否则清洗永远追不上判定。
    lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _CRUMB_LINE.match(stripped):
            continue
        if _TABLE_ROW.match(stripped) and stripped.count("|") >= 2:
            continue
        if _CHROME_TEXT and any(r.search(stripped) for r in _CHROME_TEXT):
            continue
        cleaned = _MD_LINK.sub(" ", stripped)
        cleaned = _WS.sub(" ", cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def split_sentences(text: str, terminators: str = "。！？；") -> list[str]:
    """按终止标点切句，**保留终止符**。

    这是「绝不在字符数处硬切」的基础：拼摘要时整句取用，
    摘要的最后一个字符必然是标点。

    公文还有一种断句方式：冒号引出编号小节，
    「现将有关事项通知如下：一、支持方向。二、申报条件。」
    只按句号切的话这一整段会变成一个 300+ 字的「超长句」而被 max_sentence_chars
    丢掉——实测全库有 140 条卡在这里。所以冒号后紧跟编号标记时也在那里断。
    """
    out: list[str] = []
    buf = ""
    for index, ch in enumerate(text or ""):
        buf += ch
        if ch in terminators or (ch == "：" and _STARTS_SECTION(buf, (text or "")[index + 1:])):
            sentence = buf.strip()
            if sentence:
                out.append(sentence)
            buf = ""
    tail = buf.strip()
    if tail:
        out.append(tail)          # 末尾没有标点的残句也返回，由调用方决定要不要用
    return out


# 冒号后面紧跟这些就算「引出编号小节」，在那里断句
_SECTION_MARK = re.compile(r"^\s*(?:[一二三四五六七八九十]{1,3}[、．.]|"
                           r"[（(][一二三四五六七八九十]{1,3}[)）]|"
                           r"\d{1,2}[、．.]|第[一二三四五六七八九十]{1,3}[条章节部分])")


def _STARTS_SECTION(buffer: str, rest: str) -> bool:
    """buffer 末尾是冒号，且**紧跟其后**的是编号小节标记。

    必须看 rest（冒号后面的原文）而不是 buffer 内部：逐字符扫到冒号时，
    「一、」还没读进来，只看 buffer 尾部永远匹配不上。
    """
    head, sep, _tail = buffer.rpartition("：")
    if not sep or len(head) < 8:
        # 太短的片段不按冒号断，否则「时间：2026年」这种会被切碎
        return False
    return bool(_SECTION_MARK.match(rest))


# ------------------------------------------------------------------ 字段抽取

def _norm_date(year: str, month: str, day: str) -> str:
    """拼成 YYYY-MM-DD。月份/日期越界（2 月 30 日）返回空串。"""
    try:
        y, m, d = int(year), int(month), int(day)
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


# 发布日期的合理范围。
#
# 未来的上限是 60 天，不是「取第一个日期就行」。实测 mca_gov 一条 2026 年的
# 《民政部标准制定计划》通知，正文写「于2027年9月30日前完成标准文本编写」，
# 旧逻辑把那个**截止日期**当成了发布日期，库���存成 2027-09-30。宽到 370 天
# 仍然漏（2026-10-01 + 370 天 = 2027-10-06，恰好盖不住临界），收到 60 天。
# 政务文件提前两个月以上发布没有先例，真有预发布也是月初的事。
_EARLIEST_PLAUSIBLE = 1990
_LATEST_MARGIN_DAYS = 60


def _date_plausible(value: str) -> bool:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return False
    if parsed.year < _EARLIEST_PLAUSIBLE:
        return False
    return parsed <= date.today() + timedelta(days=_LATEST_MARGIN_DAYS)


def extract_fields(content: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """从正文里抽四个字段：发布机构、日期、金额、截止。

    金额宁可置空也不猜——实测旧摘要把「第 40 篇案例的 268 万元」当成整个
    文件的投资额，这种错误比缺字段有害得多。

    发布机构的正则只作**兜底**。权威值来自 ``config/sources.yaml`` 的
    ``name``（那是逐源人工登记过的），由调用方通过 extra_facts 传进来。
    正则抽机构名不可靠：``[一-龥]{2,20}?(?:厅|局|委|部)`` 在
    「为了便于公民、法人及时、准确获取四川省商务厅的政府信息」这种句子里
    会抽出「准确获取四川省商务厅」——把动词和状语一起吃进来了。
    """
    text = content or ""
    junk_res = [re.compile(p) for p in
                ((cfg.get("summary") or {}).get("amount_junk_patterns") or [])]

    out: dict[str, Any] = {}

    # 发布机构兜底正则。锚到句首或标点后，避免从句子中间起匹配把动词吃进来。
    org = re.search(r"(?:^|[。；，、（(])([一-龥]{2,10}?(?:人民政府|厅|局|委|部|办公室|中心|集团|署))"
                    r"(?=发布|印发|关于|按照|根据|将|$|[，,])", text)
    if org:
        out["issuing_body"] = org.group(1)

    # 发布日期。两步：先找带标签的（权威），再退回裸日期（并做范围校验）。
    #
    # 为什么不能直接取「正文里第一个日期」——实测这么干会出三种错：
    #   id=435  民政部标准制定计划，正文写「于2027年9月30日前完成标准文本编写」，
    #           抓到了**截止日期**当发布日期
    #   id=1486 北京市规范性文件栏目页，「SEO 2029-06-11」是页面模板产物
    #   id=3439 四川省商务厅机构沿革页，抓到沿革正文里的 2000-04-20
    #
    # 「发布日期/公开日期/发文日期」是站方写在页面上的权威值，优先用它。
    labeled = re.search(
        r"(?:发布日期|公开日期|发文日期|成文日期|发布时间)\s*[:：]?\s*"
        r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})", text)
    if labeled:
        candidate = _norm_date(labeled.group(1), labeled.group(2), labeled.group(3))
        # 空串说明日期本身不存在（2 月 30 日）。别把空值塞进字典——下游
        # `record["publish_date"] or fields.get("publish_date") or ""` 看着能兜，
        # 但任何 `if "publish_date" in fields` 的判断都会被空串骗过去。
        if candidate:
            out["publish_date"] = candidate
    else:
        bare = re.search(r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})", text)
        if bare:
            candidate = _norm_date(bare.group(1), bare.group(2), bare.group(3))
            # 超出合理范围就宁可不给。政务文件确实会写「2027 年前完成」这类
            # 目标日期，但那是正文里的内容，不是这条文件的发布日期。
            if candidate and _date_plausible(candidate):
                out["publish_date"] = candidate

    # 金额：必须带货币单位或「万元/亿元」这类量词，纯数字不算
    amount = re.search(r"(\d[\d,，.]*\s*(?:亿元|万元|万美元|美元|元))", text)
    if amount:
        candidate = amount.group(1).strip()
        bare = re.sub(r"[^\d.]", "", candidate)
        if bare and not any(r.search(bare) for r in junk_res):
            out["amount"] = candidate

    # 截止时间
    deadline = re.search(r"((?:截止|截至|于)\s*20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
    if deadline:
        out["deadline"] = deadline.group(1)

    return out


# ------------------------------------------------------------------ 摘要

def _prefix(title: str, facts: Mapping[str, Any]) -> str:
    """摘要前缀：〔机构〕〔日期〕发布〔标题〕。都是原文字段，不生成。"""
    title_text = _clean(title)
    if not title_text:
        return ""
    bits: list[str] = []
    org = str(facts.get("issuing_body") or "").strip()
    # 标题里已经带了机构名就别再前缀一遍。实测出现过
    # 「四川省商务厅2025-07-30发布行政规范性文件 - 四川省商务厅。」这种
    # 同一个机构名出现两次的摘要。
    if org and org not in title_text:
        bits.append(org)
    if facts.get("publish_date"):
        bits.append(str(facts["publish_date"]))
    lead = "".join(bits)
    return f"{lead}发布{title_text}。" if lead else f"发布{title_text}。"


def summarize_table(content: str, cfg: Mapping[str, Any],
                    max_pairs: int = 6) -> str:
    """从管道分隔的表格里取「键：值。」摘要。

    政务公告里有一大类根本没有散文——整页是表格：招投标中标结果、环评受理
    清单、新化学物质登记、财政预算表。实测全库 165 条，占「出不了摘要」的大头。
    它们恰恰是「重大项目」最相关的内容，丢掉代价太大。

    取第一行做表头、第二行做数据，按列配对。列名太泛的（序号/备注/合计）
    和值太长的（长句）跳过——摘要要的是能扫读的关键字段，不是把表格抄一遍。

    实测真实形态（id=86，生态环境部环评公示）：换行符 0 个、竖线 48 个，
    切出 49 个单元格：
        ['', '序号', '项目名称', ..., '承诺文件', '', '1', '某输电工程', ...]
    采集器把整张表压成**一行**，行与行之间靠**空单元格**分隔。所以不能按行解析，
    得按单元格流解析：第一段非空 = 表头，第二段非空 = 第一行数据，按列配对。
    """
    cells = [cell.strip() for cell in (content or "").replace("\n", "|").split("|")]
    header: list[str] = []
    data: list[str] = []
    in_data = False
    for cell in cells:
        if not cell:
            # 空单元格 = 换行。表头后的第一个空表示「下面是数据行」，
            # 数据行之后的空表示「这张表到头了，收工」。
            if header and not in_data:
                in_data = True
            elif data:
                break
            continue
        (data if in_data else header).append(cell)
    if not header or not data:
        return ""

    skip = {"序号", "备注", "合计", "小计", "无", "/", "-", "来源", "链接"}
    parts: list[str] = []
    for name, value in zip(header, data):
        name, value = name.strip(), value.strip()
        if not name or not value or name in skip or len(name) > 12:
            continue
        if len(value) > 60 or value in ("无", "/", "-", "—"):
            continue
        parts.append(f"{name}：{value}。")
        if len(parts) >= max_pairs:
            break
    return "".join(parts)


def build_summary(title: str, content: str, cfg: Mapping[str, Any],
                  extra_facts: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """生成摘要。返回 (摘要, 空摘要时的原因)。

    摘要 = 〔结构化字段前缀〕 + 〔首段前几个完整句〕

    三条硬约束由构造保证，不靠事后校验：
      1. 最后一个字符必然是终止标点（除非一个句子都取不到）
      2. 每个字都抄自原文，没有生成
      3. 目录行、编号行、表格行在候选阶段就被剔除了
    """
    limits = cfg.get("summary") or {}
    max_chars = int(limits.get("max_chars", 300))
    window = int(limits.get("lead_window_sentences", 8))
    min_sent = int(limits.get("min_sentence_chars", 15))
    max_sent = int(limits.get("max_sentence_chars", 230))
    terminators = str(limits.get("terminators", "。！？；"))
    use_prefix = bool(limits.get("prefix_facts", True))
    toc_res = _patterns(cfg, "chrome", "toc_patterns")

    body = strip_noise(content or "")
    sentences = split_sentences(body, terminators)
    title_key = "".join(_clean(title).split())

    facts = dict(extract_fields(content or "", cfg))
    facts.update({k: v for k, v in (extra_facts or {}).items() if v not in (None, "")})
    prefix = _prefix(title, facts) if use_prefix else ""

    # 只在首段窗口里找，且必须以终止符收尾 —— 宁可少取，不切半个字
    candidates: list[str] = []
    for sentence in sentences[:window]:
        stripped = sentence.strip()
        if not (min_sent <= len(stripped) <= max_sent):
            continue
        if stripped[-1] not in terminators:
            continue
        if any(r.search(stripped) for r in toc_res):
            continue
        # 正文第一行通常就是标题本身。摘要前缀已经带了一次标题，再取一遍
        # 就变成「发布XX。XX\n正文…」——同一句话连着出现两遍。
        # 这条影响每一条记录，不是个别 case。
        if title_key and _same_text(stripped, title_key):
            continue
        candidates.append(stripped)

    if not candidates:
        # 整页都是表格的公告（招投标、环评受理清单、标准物质登记……）没有一句散文，
        # 但它们恰恰是「重大项目」最相关的内容。从表头取键值对来摘要：
        #   序号|项目名称|建设单位|受理日期  +  1|某项目|某公司|2026-9-23
        #   -> 「项目名称：某项目。建设单位：某公司。受理日期：2026-9-23。」
        # strip_noise 会把表格行整行删掉，所以这里用原始 content。
        table = summarize_table(content or "", cfg)
        if table:
            return (prefix + table).strip()[:max_chars + 120], ""

        # 连表格都取不出值 = 正文里没有可摘要的内容（典型是纯导航索引页：
        # 全是「首页/打印/收藏」这种无终止标点的短条目）。
        # 这种情况下**不要**返回只剩标题的前缀——一条 18 字的「发布XXX。」比
        # 没有摘要更糟，它看起来像有摘要，实际零信息。宁可标 skipped。
        return "", "no_usable_sentence"

    # 逐句累加，超预算就停 —— 保证结尾落在句号上
    picked: list[str] = []
    budget = max_chars - len(prefix)
    for sentence in candidates:
        if len(sentence) > budget:
            break
        picked.append(sentence)
        budget -= len(sentence)
        if budget <= 30:
            break
    if not picked:
        picked = [candidates[0][:max_chars]]

    return (prefix + "".join(picked)).strip(), ""


def _same_text(sentence: str, title_key: str, tolerance: float = 0.85) -> bool:
    """判断一个句子是不是标题的复述。

    政务页面的正文第一行几乎总是标题的复制（有时带「- 站名」后缀、有时带
    多余空格或换行）。摘要把标题前缀和这句一起留下，就变成同一句话出现两遍。
    用字符集合的重合度判断，避免因为标点/空格差异就漏判。
    """
    key = "".join(sentence.split())
    if not key or not title_key:
        return False
    short, long_ = (key, title_key) if len(key) <= len(title_key) else (title_key, key)
    if not short:
        return False
    return len(set(short) & set(long_)) / len(set(short)) >= tolerance


def _clean(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip()
