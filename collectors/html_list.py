from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from .base import BaseCollector
from .crawl4ai_adapter import crawl_and_extract_sync
from .playwright_worker import render_page, render_pages, shutdown_renderer  # shutdown_renderer: re-export

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Fallback when a source does not set render_mode. Kept as a constant so the
# self-improvement loop can flip the project-wide default in one place
# (config/defect_registry.yaml -> collection_yield_rate fixes).
DEFAULT_RENDER_MODE = "static"

# ``<script type=...>`` 里**不是** JS 的类型：这些块的内容其实是 HTML/XML
# 数据，BeautifulSoup 按规范当纯文本处理，于是里面的 <a> 对
# ``select("a[href]")`` 完全不可见。
#
# 2026-10-07 实测踩过：政府 CMS（浙大/南京系）的 ``datastore`` 模式把整段
# 文章列表塞在 ``<script type="text/xml"><datastore>…<![CDATA[<a href=…>…]]>
# `` 里。nhsa_gov 页面上原始 67 个 ``<a href>``，BS 只认出 20 个，剩下
# 47 条文章链接全在 script 里 —— 表现为该源永远「0 links found on list page」。
#
# 反过来，真 JS 块里偶尔也有 href 字样（模板字符串、跳转代码），所以必须
# **白名单**而不是「非 JS 就算」。
_NON_JS_SCRIPT_TYPES = frozenset({
    "text/xml", "application/xml", "text/html", "application/xhtml+xml",
    "text/template", "text/x-template",
})


def _html_has_content(html: str) -> bool:
    """页面里到底有没有内容。

    判据是"去标签后还有没有文字"，不是"够不够长"：WAF 拦截时浏览器返回的是
    `<html><head></head><body></body></html>`，去掉标签后什么都没有。
    早先用"文本 >50 字"的阈值，会把短小的合法页面也误判成空（测试就抓到了
    '<a href="/art/1.html">公告</a>' 这种 2 字链接页）。
    """
    stripped = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    return bool(re.sub(r"\s+", " ", stripped).strip())


class SectionLandingError(ValueError):
    """A site-chrome page, not an announcement.

    Distinct from a real failure: a list page legitimately links to "return to
    home" and section landings, and one of those must not mark the source broken.
    """


class HtmlListCollector(BaseCollector):
    # Section landing pages that live on the same domain and would otherwise be
    # mistaken for announcements when links are harvested from whole-page markdown.
    # These are site-chrome names only. Real column names such as tzgg (通知公告)
    # or gsgg (公示公告) are deliberately absent: they are legitimate parents of
    # announcement URLs, and filtering them would drop valid detail links.
    NAVIGATION_SEGMENTS = frozenset({
        "about", "aboutus", "contact", "copyright", "disclaimer", "feedback", "government",
        "help", "home", "index", "intro", "links", "login", "map", "navigation", "position",
        "privacy", "register", "search", "sitemap", "sitemapxml", "sjgk", "gk", "zcjd",
        "zwgk", "zcwj", "xxgk", "xxgkml", "zc", "hd", "lxwm", "wz",
    })

    def __init__(self, source: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(source, **kwargs)
        self.errors: list[dict[str, str]] = []

    def _soup(self, html: str) -> Any:
        if BeautifulSoup is None:
            return None
        return BeautifulSoup(html, "html.parser")

    # Titles of site sections and chrome pages. URL path segments cannot catch
    # these because government sites abbreviate them (jgyg, ldxx, gzxx), so the
    # title is the only reliable signal.
    SECTION_TITLE_MARKERS = (
        "网站地图", "机构沿革", "机构职能", "机关简介", "机关处室", "政府领导",
        "政务公开", "互动交流", "智能问答", "新媒体矩阵", "长者专区", "无障碍",
        "网站首页", "版权所有", "联系我们", "网站声明", "常见问题", "访问帮助",
        "领导简介", "意见征集", "我要查", "旧版网站", "年度报表", "工作报表",
        # Portal furniture that carries no announcement. Measured on 282 stored
        # records: every undated one turned out to be a page of this kind
        # ("走进四川", "网站导航", "闽政通APP宣传页", "省政府令_…", "互动_…").
        "网站导航", "走进", "提建议", "宣传页", "APP", "邮件系统", "外网",
        "机构设置", "政府部门", "省级服务平台", "政策主题服务", "政务服务网",
        "信息公开指南", "政府信息公开指南", "网站声明", "访问指南",
        # 这几个是政务站首页最常见的入口，词序与上面的"互动交流"不同，
        # 不能只靠 marker 覆盖。注意只加"独立成标题"时才会命中整串，
        # 长公告标题里含"政民互动"不会被误伤（判断走的是 in 整串，需配合后缀剥壳）。
        "政民互动", "依申请公开", "信息公开制度", "政策直达", "统一身份认证",
        "统一社会信用代码", "跳转到", "正在跳转", "答复列表", "相关搜索",
        # 港澳台站点的繁体栏目名。原表全是简体，这些一直没被覆盖。
        # mo_gov 的 41 条兜底里就有大量"公共服務""公布告示"。
        "公布告示", "公共服務", "政府資訊", "新聞公報", "政策公報", "招標公告",
        "職位空缺", "表格下載", "網站導覽", "聯絡我們", "私隱政策", "使用條款",
        "市政府公署", "政府公報", "行政長官", "立法會", "新聞公報", "廉政公署",
    )
    # 平台首页/栏目入口。实测兜底 343 条里，抽样反复出现
    # 「陕西省外商投资公共信息服务平台」「湖北省科学技术奖励服务平台」
    # 「青海数智信访一网通服务平台」「业务综合服务平台」这类——它们是政务站的
    # 业务入口，不是公告。
    #
    # 判别不能靠"含平台/系统二字"：「广东省商务厅关于做好2026年粤贸全球
    # 广东商品境外展览平台相关工作的通知」也含"平台"，却是真公告。
    # 区别在于**整条标题是不是就一个机构/平台名**：平台页主体是名词性名称，
    # 公告的主体是"某机关"+"关于"+公文动词。所以用公文动词做否定判据。
    # 公文动词/文种词。标题里出现任何一个，说明这是一份真文件，不能按入口页处理。
    #
    # 这份清单是被两类误伤逼出来的：
    #   1. 只匹配"的公告"时，「…管理平台国产化适配改造（2026-2027年）中标公告」
    #      会因为含"管理平台"被判成入口页，实际它是中标结果公告；
    #   2. 「宁波?12345政务服务便民热线管理办法」含"政务服务"且长度恰好卡在
    #      阈值边界上，会被入口页规则误杀——管理办法/规定/条例是法规专名。
    # 港澳台站点的繁体栏目名（公共服務/公布告示）也在这里，不另开 marker。
    _NO_DOC_ACTION_WORDS = (
        "关于", "的通知", "的公告", "的通告", "的公示", "的批复", "的决定",
        "的函", "的意见", "的通报", "印发", "发布", "公布", "公开", "征集",
        "申报", "遴选", "评选", "认定", "核准", "备案",
        "中标", "成交", "废止", "失效", "撤回", "延长", "调整", "变更",
        "续期", "征订", "遴选结果", "结果", "目录", "名单", "通报", "预警",
        "提示", "警示",
        "管理办法", "暂行办法", "实施办法", "管理规定", "工作条例",
        "实施细则", "操作规程", "技术规范",
        "公布告示", "公共服務", "政府資訊", "新聞公報", "政策公報",
        "招標公告", "職位空缺", "表格下載", "網站導覽", "聯絡我們",
        "私隱政策", "使用條款",
    )
    SECTION_NO_DOC_ACTION = re.compile("|".join(map(re.escape, _NO_DOC_ACTION_WORDS)))
    # 入口页后缀。覆盖实测出现过的形态：服务平台、门户网��、微信公众账号、
    # 网上信访投诉平台、业务综合服务平台等。
    SECTION_ENTRY_TAIL = re.compile(
        r"(?:服务平台|管理平台|申报平台|监测平台|服务平台|一网通|服务门户"
        r"|门户网站|政府网��|公众账号|微信公众号|微博|短信平台"
        r"|申报系统|业务系统|办公系统|查询系统|管理系统|信息系统"
        r"|网上信访|信访投诉|投诉平台|热线|专栏|专题|资料库|数据库)"
    )
    # 平台入口标题的长度上限。实测这批入口页标题集中在 6-17 字
    # （陕西省外商投资公共信息服务平台 15、青海数智信访一网通服务平台 13、
    # 湖北省科学技术奖励服务平台 12），而 20 字以上基本都是真文件。
    SECTION_ENTRY_MAX_CHARS = 20
    SECTION_TITLE_EXACT = frozenset({"首页", "网站首页", "下载", "地图"})
    # 政务站的 <title> 常把站点名挂在后面："<正文标题> - 湖北省人民政府门户网站"。
    # 注意分隔符不只有下划线：实测湖北省政府用 " - "。
    # 关键：带这个后缀的**不全是栏目页**，真公告也带。实测同一站：
    #   栏目页  政民互动 - 湖北省人民政府门户网站
    #   真文件  湖北省人民政府关于印发《…十五五规划》的通知 - 湖北省人民政府门户网站
    # 所以后缀只用来切分出标题主体，判栏目与否只看主体。
    #
    # 写成"捕获组 + 锚定"而不是一整条正则去 sub，有两个踩过的坑：
    #   1. sub 会把整条吃掉。"政民互动 - 湖北省人民政府门户网站" 整串都符合后缀形状，
    #      sub 之后剩空串，主体为空就什么规则都触发不了，栏目页反而漏放。
    #   2. 后缀的站点名长度不能设下限。曾按 "站点名>=4 字" 写，结果
    #      "政民互动_天津政务网"（站点名只有"天津"2 字）剥不掉。
    # 主体用贪婪 .+ 让切分点落在**最后一个**分隔符上：标题内部也可能含 " - "。
    # 站点名后缀。机构类后缀必须带上：发改委那批站点名是
    # 「河南省发展和改革委员会」「陕西省发改委」，只认"政府网/门户网站"的话
    # 这批源的导航页（新闻中心/通知公告/发改要闻/省领导…）一个都剥不开。
    SITE_NAME_SUFFIX = re.compile(
        r"^(?P<core>.+)\s*[-–—_]\s*"
        r"(?P<site>.{2,32}?(?:门户网站|政务网站|人民政府|政府办公厅|政府网|政务网|信息网"
        r"|委员会|人民政府办公厅|办公室|局|委|厅|院|中心))$"
    )
    # 标题主体这么短，且标题带站点名后缀 —— 那它是门户的栏目名而不是公告。
    #
    # 只在"带后缀"时启用是关键，不能无条件用：项目已有契约
    # tests/test_collectors.py::test_real_announcements_are_not_rejected 明确要求
    # 放行"锦江区公告"(5 字)、"工作动态"(4 字)、"信息公开"(4 字)——真实公告
    # 标题确实可以很短。而门户页会把自己的名字挂在标题后面，"政民互动 - 湖北省
    # 人民政府门户网站"剥完只剩 4 字，这种才敢按长度判。
    # 阈值 8 来自拿库里 1762 条真实标题回归的分界：带后缀的栏目名最长 8 字。
    # 改动阈值或去掉后缀前提，必须重跑 scripts/guard_regression.py。
    SECTION_CORE_MAX_CHARS = 8
    # 栏目页之外的"根本不是公告"的页面。用 URL 判，不看标题——
    # 实测 sc_ny 的 http://nynct.sc.gov.cn/wza/（无障碍声明页）标题整页乱码
    # （UTF-8 字节被按 latin-1 读），任何标题规则都对它无能为力，而 /wza/
    # 正是政务站无障碍栏目的固定路径。
    NON_DOC_URLS = re.compile(
        r"/(?:wza|wzgao|barrier_free|accessibility)/?"
        r"|/(?:copyright|disclaimer|statement)/?"
        r"|/site(?:map|map)/?$"
        # 机构设置/信息公开指南/领导信息/职能介绍等结构性页面：
        # 不是公告，是政府组织架构和办事指南，实测 ID=7167 机构设置页
        # 含 52 个电话号码，ID=7163 信息公开指南含 4 个，全部无用。
        # 注意：/zcjd/(政策解读)和/bmdt/(部门动态)是合法内容页，不排除。
        r"|/(?:jgsz|jgzn|znjs|bnjs|ldxx|ldgk|xxgkzn|xxgkzd)/?",
        re.I)
    # 版权/免责声明/网站声明这类页面。标题形态很固定，风险低：真公告不会以
    # "…官网版权与免责声明"为题。实测 sc_ny 收过一条 2018 年的
    # 「四川省农业农村厅官网版权与免责声明」。
    NON_DOC_TITLE = re.compile(
        r"版权(?:声明|归属)|免责声明|网站声明|网站声明|使用条款|隐私(?:政策|声明)"
        r"|网站地图|无障碍(?:浏览|说明|声明|服务)?(?:页|版)?$")
    # 列表页的形态判据：正文里挤着多条"日期 + 标题"。实测 sn_fgw 收过两条
    # 标题就叫「通知公告」的栏目页，正文分别是
    #   「首页 > 新闻中心 > 通知公告 新闻中心 通知公告 焦点新闻 … 2026-09-22 …」
    #   「- 2026-09-21 关于…公示 - 2026-09-18 关于…公告 - 2026-09-15 …」
    # 这类页面标题只有栏目名（4 字），而项目契约要求放行「工作动态」「信息公开」
    # 这类 4 字真标题，所以不能只看标题长度，必须看正文是不是列表。
    LISTING_MIN_DATES = 3
    LISTING_DATE = re.compile(r"(?:19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}")
    # 列表页与真公告的真正差别，不是"有几个日期"，而是**日期后面紧不紧跟标题**。
    #
    # 踩过的坑：第一版只数日期个数 >=3，结果误伤了两类真公告——
    #   「浙江省成品油价格调整」正文里就有 2026年9月4日/9月1日/9月8日 三处
    #   「创业培训讲师选评公示」正文引用了多个文号日期
    # 真公告本来就会有多个完整日期（印发日期、执行期限、引用文号）。列表页则是
    # 「日期 + 一行标题」这样反复排列。所以判据必须是"日期后面跟着一段像标题的
    # 文字"：从日期往后 8-40 字，且这段里不出现句末标点（出现了就是正文句子，
    # 不是标题）。
    LISTING_ENTRY = re.compile(
        r"(?:19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}"
        r"[^\n。！？；]{0,6}"          # 日期与标题之间通常只有空白或 "- "
        r"[^\n。！？；]{8,40}")
    LISTING_MIN_ENTRIES = 3
    # 真正的公告标题几乎都带这些形状。"通知公告"这种**不带**它们——
    # 它就是把两个公文词并排当栏目名用，所以不能靠"含公文动词"来排除。
    ANNOUNCE_SHAPE = re.compile(
        r"关于|第[一二三四五六七八九十\d]+|年度|批次|"
        r"的(?:通知|公告|公示|通告|批复|决定|函|意见|通报)|"
        r"印发|发布|征集|申报|遴选|评选|认定|中标|成交|废止|"
        r"办法|条例|规定|细则|方案|规划|名单|结果")

    def _strip_site_suffix(self, title: str) -> str:
        """剥掉尾部站点名，返回标题主体。切不出就原样返回。"""
        text = (title or "").strip()
        match = self.SITE_NAME_SUFFIX.match(text)
        if match and len(match.group("core").strip()) >= 2:
            return match.group("core").strip()
        return text

    def _is_section_page(self, title: str) -> bool:
        value = re.sub(r"[\s　_|-]+", "", title or "")
        if not value:
            return False
        if self.NON_DOC_TITLE.search(value):
            return True
        if value in self.SECTION_TITLE_EXACT:
            return True
        if any(re.sub(r"[\s　_|-]+", "", marker) in value for marker in self.SECTION_TITLE_MARKERS):
            return True
        # 判标题主体（已剥掉站点名后缀）。不能反过来——" - 门户网站"后缀真公告也有，
        # 直接按后缀拦会把湖北省政府全站真文件一起杀掉。
        stripped = self._strip_site_suffix(title)
        if stripped != (title or "").strip():
            core = re.sub(r"[\s　_|-]+", "", stripped)
            if core and len(core) <= self.SECTION_CORE_MAX_CHARS:
                return True
        core_plain = re.sub(r"[\s　_|-]+", "", stripped)
        # 平台名通常带这些后缀，本身就是"一个系统/一个入口"的标志。
        # 必须整条标题就是个平台名，不能"含平台词就算"——否则会杀掉
        # 「宁波"12345"政务服务便民热线管理办法」这种 5419 字的真文件。
        #
        # 长度分档来自实测：真文件标题即使短也带公文动词（已在下面排除），
        # 而平台页标题是纯名词性名称，长度集中在 6-17 字。20 字以上基本
        # 都是真文件，不用再靠长度赌。
        if self.SECTION_ENTRY_TAIL.search(stripped) and not self.SECTION_NO_DOC_ACTION.search(stripped):
            # 唯一实测到的漏网：比选/招标/采购类——标题里是"项目比选公告"，
            # 既不含公文动词（不是"…的公告"），又含"专题""服务"这类入口页词。
            if not re.search(r"比选|招标|采购|磋商|询价|竞谈", title or ""):
                if len(core_plain) <= self.SECTION_ENTRY_MAX_CHARS:
                    return True
        # 用 | 分隔的一串栏目名：站点的"专题导航条"，不可能是一份公告。
        # 实测 sc_qh_gov 的「民生服务 民生聚焦 | 便民通告 | 民生保障 | 民生实事 |
        # 民生进展 | 民生心声」——6 个栏目名加 5 个竖线，标题长度 37 字，绕过了
        # 长度阈值；又因为不含任何公文动词，也绕过了 NO_DOC_ACTION。
        # 判据是分隔符数量：公文标题里出现 3 个以上竖线只可能是表格或导航条。
        if stripped.count("|") >= 3 and not self.SECTION_NO_DOC_ACTION.search(stripped):
            return True
        # "某某厅 某某平台" 这种两段式（中间空格或下划线分隔）也是入口页
        if re.match(r"^[^，,。]{4,14}厅\s{1,2}[^，,。]{2,20}(?:平台|系统|门户)$", title or ""):
            return True
        # "某某厅"/"某某局"/"某某政府"/"某某委员会" on its own names an organisation
        # page. 实测 sn_fgw 因此漏进过「陕西省发展和改革委员会」「陕西省发改委」两条，
        # 前者 12 字超过了带后缀时的长度阈值，后者没带后缀，只能靠这条兜。
        # "部" is excluded: a ministry name is often the subject of a real notice
        # (e.g. "生态环境部关于召回...")。
        return len(value) <= 24 and value.endswith(
            ("厅", "局", "政府", "学院", "中心", "委员会", "委", "办公厅", "办公室"))

    # 电话密度守卫：机构设置/通讯录等页面电话号码密集，不是公告。
    # 实测 ID=7167 机构设置页 1468 字正文含 52 个电话，ID=1152 三支一扶
    # 招募公告 34993 字含 305 个电话（真公告但电话占比低）。
    # 判据：电话号码 ≥15 个 且 电话字符占正文比 ≥8% → 目录页。
    _PHONE_RE = re.compile(r"0?\d{3,4}[-\s]?\d{7,8}")

    def _is_phone_directory(self, content: str) -> bool:
        if not content or len(content) < 100:
            return False
        phones = self._PHONE_RE.findall(content)
        if len(phones) < 15:
            return False
        phone_chars = sum(len(p) for p in phones)
        return phone_chars / len(content) >= 0.08

    def _navigation_like(self, url: str) -> bool:
        segments = [part for part in urlparse(url).path.lower().split("/") if part]
        if not segments:
            return True
        tail = segments[-1].split(".")[0]
        if tail in self.NAVIGATION_SEGMENTS:
            return True
        extra = self.source.get("navigation_segments") or []
        return bool(set(segments) & {str(segment).lower() for segment in extra})

    def _looks_like_listing(self, title: str, content: str) -> bool:
        """正文是列表（多条"日期 + 标题"）且标题就是个栏目名。

        为什么不能只看标题长度：项目契约 tests/test_collectors.py 里的
        test_real_announcements_are_not_rejected 明确要求放行「工作动态」
        「信息公开」这类 4 字真标题，真实公告的标题确实可以很短。

        为什么不能只看日期数量：真公告正文里也会出现多个日期（印发日期、
        执行期限、引用文号日期）。所以必须两个条件同时成立——标题不是公告的
        形状，且正文挤着多条日期。

        也不能要求"标题必须是已知栏目名"：最早那版这么写，结果「通知公告」
        根本不在 SECTION_TITLE_MARKERS 里，判据自己把自己拦了。改成只看形态。

        公告形状用 ANNOUNCE_SHAPE 而不是 SECTION_NO_DOC_ACTION：后者认的是
        「的通知」「的公告」这种带"的"的形状，而「通知公告」正好不含——
        它就是把两个公文词并排当栏目名用。

        实测 sn_fgw 收过两条这样的栏目页，标题都叫「通知公告」（4 字）：
          「首页 > 新闻中心 > 通知公告 新闻中心 通知公告 焦点新闻 … 2026-09-22 …」
          「- 2026-09-21 关于…公示 - 2026-09-18 关于…公告 - 2026-09-15 …」
        """
        core = re.sub(r"[\s　_|-]+", "", self._strip_site_suffix(title))
        if not core or len(core) > 12:
            return False
        if self.ANNOUNCE_SHAPE.search(title or ""):
            return False   # 标题是公告的形状，放行
        return len(self.LISTING_ENTRY.findall(content or "")) >= self.LISTING_MIN_ENTRIES

    def _link_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or not self._is_allowed_url(url):
            return False
        pattern = self.source.get("detail_pattern")
        pattern_ok = bool(pattern) and bool(re.search(str(pattern), url))

        # 通用启发式（导航段 / 末段 index.*）要放行显式配置的 detail_pattern。
        #
        # 为什么：NAVIGATION_SEGMENTS 里有 'index'，而判定是拿路径末段去白名单里
        # 查——「xxx/index.html」的末段正是 index，于是被判成栏目首页丢掉。可浙江、
        # 河北、辽宁这类政府 CMS 恰恰把**文章**地址写成 col1229225203/index.html、
        # 2026092109480258270/index.html。实测 7 个源因此一条链接都取不到（浙江
        # 科技厅/农业厅/人社厅、河北科技厅/工信厅、辽宁政府），每轮各白烧 300~600
        # 秒在确认"没链接"，日志里是 no links found on list page。
        #
        # 代价是失去一道防栏目页的闸。但那不是损失：这些源同时配了 detail_pattern
        # 之外的 selector，且下游还有 content_quality / link_flood 门禁兜底。操作员
        # 显式写下的 pattern 是比通用启发式更强的证据。
        if not pattern_ok:
            if self._navigation_like(url):
                return False
            if path in {"", "/"} or path.endswith(("/index.htm", "/index.html")):
                return False
            # 以 / 结尾的**目录型**链接（没有文件名）也是栏目页。
            #
            # 为什么必须单独拦：上面的守卫只认「index.* 结尾」，而政府站的栏目
            # 地址大量是裸目录 —— 2026-10-07 实测漏进来的就是这些：
            #   https://www.stats.gov.cn/znwd/          → 标题「RSS订阅」
            #   https://www.stats.gov.cn/wzgl/rss/      → 标题「局领导」
            #   https://zjj.sz.gov.cn/xxgk/tjsj/        → 标题「统计信息」
            #   https://zjj.sz.gov.cn/xxgk/zjxx/czyjs/ → 标题「局长信箱」
            # 它们都不是 index.html，于是整批穿过守卫被当详情抓走。
            #
            # 依据：库里 15332 条 URL 中以 / 结尾的有 617 条（4.02%），
            # **逐条看过全是栏目页**（正文是导航，或干脆只有「联系我们」四个字），
            # 零真文章。而 94.18% 的 URL 是带文件名的文章形态。所以这条守卫
            # 的误杀面在实测数据上是空的。
            #
            # 放在 pattern_ok 之外：**即便配了 detail_pattern 也拦**。
            # detail_pattern 是白名单（命中就绕过上面的启发式），拿它放行目录型
            # 链接等于把刚堵上的口子重新打开——实测有 7 个源配着 pattern。
            if path.endswith("/"):
                return False
        # 无障碍/版权/网站地图这类页面根本不是公告，在这里就拦掉。
        # 必须在取标题之前拦：实测 sc_ny 的 /wza/ 标题整页乱码（UTF-8 字节被
        # 当 latin-1 读），等拿到标题再判已经晚了——那串乱码既不含栏目词也不含
        # 公文动词，所有标题规则都放行。
        if self.NON_DOC_URLS.search(parsed.path):
            return False
        if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".zip", ".rar")):
            return False
        if pattern and not pattern_ok:
            return False
        return True

    def _container(self, soup: Any) -> Any:
        selector = self.source.get("list_selector")
        if not selector:
            return soup
        container = soup.select_one(str(selector))
        if container is None:
            raise ValueError(f"list_selector did not match: {selector}")
        return container

    def _extract_links(self, html: str, base_url: str | None = None) -> list[str]:
        if BeautifulSoup is None:
            raise RuntimeError("beautifulsoup4 is required for HTML list parsing; install requirements.txt")
        soup = self._soup(html)
        container = self._container(soup)
        link_selector = str(self.source.get("link_selector", "a[href]"))
        try:
            anchors = container.select(link_selector)
        except Exception as exc:
            raise ValueError(f"invalid link_selector: {link_selector}") from exc
        base = base_url or self.list_url
        links = list(self._links_from(anchors, base))

        # 有些政府 CMS 把**整段文章列表**塞在 <script type="text/xml"><datastore>
        # 里（浙大/南京系 CMS 的 datastore 模式，nhsa_gov / sd_swt / sd_gxt /
        # sd_kjt / sd_fgw / sc_sd_gov 都是这个形态）。BeautifulSoup 按规范把
        # <script> 内容当纯文本，所以 container.select() **看不见**那些 <a>：
        # 实测 nhsa_gov 页面上原始有 67 个 <a href>，BS 只认出 21 个，其余
        # 46 条文章链接全在 script 里 —— 表现为该源永远「0 links found on
        # list page」，配了源也一条采不到。
        #
        # 所以额外把**明确声明为非 JS 类型**的 script 内容当 HTML 再解析一遍。
        # 白名单而不是「非 JS 就算」：真 JS 块里也有 href 字样（模板字符串、
        # 跳转代码），硬解析会捞出一堆假链接。
        for node in soup.find_all("script"):
            stype = str(node.get("type") or "").strip().lower()
            if stype not in _NON_JS_SCRIPT_TYPES:
                continue
            inner = node.string or node.get_text() or ""
            if "<a" not in inner:
                continue
            # datastore 的内容通常整段包在 <![CDATA[ ... ]]> 里。html.parser
            # 把它当声明节点，里面的 <a> 全变成该节点的文本 —— select 取不到。
            # 实测 nhsa_gov：剥掉 CDATA 包装后 a[href] 从 0 条变 46 条，
            # 其中 45 条是文章链接。两头都要剥（只剥 ]]> 无效）。
            inner = inner.replace("<![CDATA[", "").replace("]]>", "")
            try:
                inner_soup = self._soup(inner)
            except Exception:                              # noqa: BLE001
                continue
            if inner_soup is None:
                continue
            try:
                inner_anchors = inner_soup.select("a[href]")
            except Exception:                              # noqa: BLE001
                continue
            extra = list(self._links_from(inner_anchors, base))
            if extra:
                self._note("links_from_script_datastore",
                           "列表藏在 script/datastore 里，额外取出 %d 条" % len(extra))
            links.extend(extra)

        links = list(dict.fromkeys(links))
        # 注意：这里**不**把已知 URL 滤掉。滤掉之后 collect() 就看不到它们，
        # 也就没法判断"这一页全是旧的"而提前收工——而提前收工才是省时间的关键
        # （实测不收工的话，跳过 61 个已知之后一路翻页到超时，从 186.8 秒变成
        # 426.1 秒）。已知的判断放在 collect() 里做，由它抛 KnownDetailError。
        return links

    def _links_from(self, anchors: Any, base: str) -> list[str]:
        """按 _link_allowed 过滤一组锚点。列表页与 script 内嵌页共用这一份。"""
        out: list[str] = []
        for anchor in anchors:
            href = anchor.get("href") if hasattr(anchor, "get") else None
            if not href or not isinstance(href, str):
                continue
            href = href.strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(base, href)
            if self._link_allowed(url) and url.rstrip("/") != base.rstrip("/"):
                out.append(url)
        return out

    def _crawl4ai_enabled(self) -> bool:
        settings = self.source.get("crawl4ai", {})
        return self.source.get("render_mode") in {"crawl4ai", "static_then_crawl4ai"} or bool(settings.get("enabled", False))

    def _crawl4ai_result(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        if self.source.get("check_robots", True):
            self._assert_robots_allows(url)
        settings = dict(self.source.get("crawl4ai", {}))
        return crawl_and_extract_sync(url, settings)

    def _crawl4ai_links(self, result: Mapping[str, Any], base_url: str) -> list[str]:
        links = []
        structured = result.get("structured")
        if isinstance(structured, Mapping):
            structured = structured.get("items") or structured.get("data") or []
        if isinstance(structured, list):
            for item in structured:
                if not isinstance(item, Mapping):
                    continue
                href = item.get("链接") or item.get("link") or item.get("url")
                if href:
                    url = urljoin(base_url, str(href))
                    if self._link_allowed(url):
                        links.append(url)
        for item in result.get("links", []):
            href = item.get("href") or item.get("url") if isinstance(item, Mapping) else item
            if href:
                url = urljoin(base_url, str(href))
                if self._link_allowed(url):
                    links.append(url)
        markdown = str(result.get("markdown") or "")
        for href in re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", markdown):
            if self._link_allowed(href):
                links.append(href)
        return list(dict.fromkeys(links))

    def _crawl4ai_quality(self, result: Mapping[str, Any]) -> dict[str, Any]:
        fit = str(result.get("markdown") or "")
        raw = str(result.get("raw_markdown") or "")
        noise_markers = sum(fit.count(marker) for marker in ("登录", "注册", "首页", "导航", "广告", "版权"))
        return {
            "fit_markdown_length": len(fit),
            "raw_markdown_length": len(raw),
            "fit_ratio": round(len(fit) / len(raw), 4) if raw else 0.0,
            "noise_marker_count": noise_markers,
            "fit_markdown_fallback": bool((result.get("metadata") or {}).get("fit_markdown_fallback")),
            "usable": bool(fit.strip()) and len(fit) >= int(self.source.get("minimum_content_chars", 80)),
        }

    def _parse_crawl4ai_result(self, result: Mapping[str, Any], url: str) -> dict[str, Any]:
        markdown = str(result.get("markdown") or "")
        metadata = result.get("metadata") or {}
        title = metadata.get("title") or metadata.get("og_title")
        if not title:
            match = re.search(r"^#\s+(.+)$", markdown, flags=re.M)
            title = match.group(1).strip() if match else ""
        return {
            "title": str(title or "").strip(),
            "publish_date": metadata.get("publish_date") or metadata.get("date"),
            "content": markdown,
            "url": metadata.get("source_url") or metadata.get("url") or url,
        }

    def _playwright(self) -> Any:
        # Rendering moved to a worker process (see _render_with_playwright): the
        # sync API cannot start inside crawl4ai's asyncio loop.
        raise RuntimeError("Playwright now runs in a worker process; see render_page")

    def close(self) -> None:
        # The warm browser is shared by every source and released at process exit
        # (atexit in playwright_worker). Shutting it down here relaunched Chromium
        # on every collector.close() -- 52 launches per pass -- and, under
        # concurrency, killed the browser another source was driving. base.close()
        # releases the httpx client this collector owns, which this override used
        # to skip and so leaked one connection per source.
        super().close()

    def _render_with_playwright(self, url: str) -> str:
        """Fetch a JS-rendered page in a worker process.

        Rendering in-process mixed two event loops: crawl4ai_adapter calls
        asyncio.run(), and the sync Playwright API refuses to start once that
        loop has run ("Playwright Sync API inside the asyncio loop"). That
        failure hit 46 of 145 registered sources. A worker process keeps the
        loops separate and also isolates crashes from the collector.
        """
        if not self.source.get("playwright_fallback", False):
            raise ValueError("Playwright fallback is disabled for this source")
        return render_page(
            url,
            executable_path=self.source.get("playwright_executable_path"),
            timeout_ms=int(self.source.get("playwright_timeout_ms", 60000)),
            wait_ms=int(self.source.get("playwright_wait_ms", 5000)),
            wait_until=str(self.source.get("playwright_wait_until", "networkidle")),
            locale=str(self.source.get("playwright_locale", "zh-CN")),
        )

    def _archive_pages(self) -> int:
        """How many list pages to walk. 0 or 1 means the first page only."""
        try:
            return max(1, int(self.source.get("archive_pages", 1)))
        except (TypeError, ValueError):
            return 1

    def _next_page_link(self, html: str, base_url: str, seen: set[str]) -> str | None:
        """从已抓到的页面里找出"下一页"的真实链接。

        为什么不能继续猜 URL
          _page_url 按 list_N.shtml / index_N.shtml / ?page=N 去套，实测证明
          套不对：江苏/浙江/山东的 col/col284/index.html 加 ?pageNum=2 或
          ?page=2，返回的链接集合与第 1 页**完全相同**（站点根本不认这个参数）；
          广东/河北的 index_1.shtml 直接 404。也就是说这批省站是 JS 翻页，
          静态猜不出来。而页面本身通常带着真正的翻页控件——那个 href 才是对的。

        认得三种写法，够覆盖绝大多数政务站
          1. 链接文字/标题是「下一页」「下页」「后一页」「next」「>」「»」
          2. href 里带 index_1.htm / index_2.htm 这类序号（即使文字是数字）
          3. href 里带 ?page=2 / &pageNum=2（即使文字不是"下一页"）

        seen 用来防环：有些站的"下一页"在最后一页仍指向自己或前一页。
        """
        base = base_url
        best: str | None = None
        for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html or "", re.S | re.I):
            attrs, inner = m.group(1), m.group(2)
            href_m = re.search(r'href\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            if not href_m:
                continue
            href = href_m.group(1).strip()
            # 只认真正的 http/https。**不要枚举要排除的伪协议**——
            # 实测 mem.gov.cn 的翻页控件里写的是 ``javascirpt:void(0)``
            # （少一个 e），按 ``startswith("javascript:")`` 过滤会漏掉它，
            # 于是把「点击无效」当成真实 URL 去请求，翻页永远停在第 1 页，
            # 而现象是「源没产出」——看不出是这里错了。
            #
            # 反过来做（协议白名单）就不用猜站方会怎么拼。
            scheme = re.match(r"([a-zA-Z][a-zA-Z0-9+.\-]*):", href)
            if scheme and scheme.group(1).lower() not in ("http", "https"):
                continue
            # 只拒锚点。``?pageNum=4`` 是合法的翻页链接（同一路径换页码），
            # 别顺手一起拒掉——第一版加了 "?" 结果把这个写法也干掉了，
            # 被本文件的 test_real_next_link_still_recognised_after_the_fix 抓住。
            if not href or href.startswith("#"):
                continue
            text = re.sub(r"<[^>]+>", "", inner)
            title = re.search(r'title\s*=\s*["\']([^"\']*)["\']', attrs, re.I)
            label = f"{text} {title.group(1) if title else ''}".strip()
            candidate = urljoin(base, href)
            if candidate in seen or candidate == base:
                continue

            # 1) 明确的"下一页"文字
            if re.search(r"下一页|下页|后一页|next|»|>", label, re.I):
                return candidate
            # 2) href 里的序号页（index_1 / index_2 …），排除当前页自身
            if re.search(r"index_\d+\.(?:s?html?|htm)", candidate, re.I):
                best = best or candidate
            # 3) href 里的页码参数
            elif re.search(r"[?&](?:page|pageNum|pageno|pageNo)=\d+", candidate, re.I):
                best = best or candidate
        return best

    def _static_archive_pages(self) -> list[str]:
        """Walk archive pages, following the page's own "next" link.

        archive_pages only reached the rendered path before, so static sources
        kept returning page 1 -- which is why the 1-10 year history came back
        empty. Static archives are plain links, so no browser is needed.

        现在不再按 URL 模式猜页码（实测对江苏/浙江/山东全部无效：?pageNum=2 与
        ?page=2 返回的链接集与第 1 页完全相同，广东/河北的 index_1.shtml 直接
        404），改成跟着页面上真实的"下一页"链接走。猜不出来时退回 _page_url，
        保持对财政部/教育部那批静态 index_N.shtml 站点的原有行为。
        """
        pages = self._archive_pages()
        if pages <= 1:
            return []
        collected: list[str] = []
        seen: set[str] = set()
        current: str | None = self.list_url
        try:
            response = self._request(current)
            html = self._response_text(response)
            current = str(getattr(response, "url", current)) or current
        except Exception as exc:  # noqa: BLE001 - a missing page is not fatal
            self._note("static_page_failed", f"page 1 failed: {exc}")
            return []
        seen.add(current)
        collected.append(html)

        for index in range(2, pages + 1):
            nxt = self._next_page_link(html, current, seen)
            if not nxt:
                # 页面没给"下一页"就退回猜 URL；猜不出来就到此为止
                nxt = self._page_url(index)
            if not nxt or nxt in seen:
                break
            try:
                response = self._request(nxt)
                page_html = self._response_text(response)
            except Exception as exc:  # noqa: BLE001
                self._note("static_page_failed", f"page {index} failed: {exc}")
                break
            final = str(getattr(response, "url", nxt)) or nxt
            # 第 2 页起内容与上一页完全一样 = 站点在骗我们（实测多省如此）
            if page_html == html:
                self._note("static_page_same_as_previous", f"page {index} identical to previous")
                break
            seen.add(nxt)
            seen.add(final)
            collected.append(page_html)
            html, current = page_html, final
        return collected

    def _page_url(self, index: int) -> str | None:
        """Derive page N from the list URL. Supports the common static patterns."""
        if index <= 1:
            return None
        text = self.list_url
        # list.shtml / list_1.shtml / index.html / ?page=1
        for pattern, template in (
            (r"list_(\d+)\.s?html?$", "list_{n}.shtml"),
            (r"list\.s?html?$", "list_{n}.shtml"),
            (r"index\.s?html?$", "index_{n}.shtml"),
            (r"index_(\d+)\.s?html?$", "index_{n}.shtml"),
        ):
            if re.search(pattern, text):
                if re.search(r"list_\d+\.s?html?$", text) or re.search(r"index_\d+\.s?html?$", text):
                    return None  # zero-based family: guessed, leave it to the renderer
                # 模板里的 {n} 必须替换。原来的 re.sub(pattern, template, text)
                # 直接返回字面量 "index_{n}.shtml"——archive_pages 对
                # list.shtml / index.shtml 这一族从来没生效过，测出来就是
                # "第 2 页 0 条"，因为请求的是一个不存在的 URL。
                return re.sub(pattern, template, text).replace("{n}", str(index))
        if re.search(r"[?&]page=\d+", text):
            return re.sub(r"([?&]page=)\d+", rf"\g<1>{index}", text)
        return None

    def _render_archive(self, url: str) -> list[str]:
        pages = self._archive_pages()
        if pages <= 1:
            return [self._render_with_playwright(url)]
        return render_pages(
            url,
            executable_path=self.source.get("playwright_executable_path"),
            timeout_ms=int(self.source.get("playwright_timeout_ms", 60000)),
            wait_ms=int(self.source.get("playwright_wait_ms", 5000)),
            wait_until=str(self.source.get("playwright_wait_until", "networkidle")),
            locale=str(self.source.get("playwright_locale", "zh-CN")),
            pages=pages,
        )

    def _list_html(self) -> tuple[str, str]:
        response = self._request(self.list_url)
        html = self._response_text(response)
        base = str(getattr(response, "url", self.list_url))
        # 感知：列表页拿回来就判断入口是否还灵。失效必须被看见，而不是静默
        # 返回零条 —— 「静默写入零行」正是采集层自我修复要消灭的那个行为。
        # 只观测 + 推导修复方案 + 记事件，**不写回注册表**（见 repair.heal_and_report）。
        # 修不了也不硬改：注册表零改动，由 self_tune 在轮后过验证再决定要不要改。
        self._observe_selector_health(html, base)
        return html, base

    def _observe_selector_health(self, html: str, base_url: str) -> None:
        """判断本源的采集入口（list_selector 或 detail_pattern）是否失效。

        只观测 + 推导 + 记事件，**不写回注册表** —— 采集时正在跑着，改配置
        改坏了当场就体现为数据错误。写回由 self_tune 在轮后过 before/after 决定。

        best-effort：观测失败绝不能影响采集本身，所以整段吞异常，只留一条 note。
        """
        try:
            from lighthouse import repair
        except Exception as exc:                       # noqa: BLE001
            self._note("selector_observe_skipped", f"{type(exc).__name__}: {exc}")
            return
        try:
            obs = repair.observe_list_page(html, self.source, list_url=base_url)
        except Exception as exc:                       # noqa: BLE001
            self._note("selector_observe_failed", f"{type(exc).__name__}: {exc}")
            return
        if not obs.get("broken"):
            return
        detail = str(obs.get("detail") or "入口失效")
        self._note("selector_broken", detail)
        try:
            kind = str(obs.get("kind") or "")
            soup = self._soup(html)
            if kind == "list_selector" and soup is not None:
                # 容器类：手里就有 soup，直接尝试推导。kind 必须显式传过去 ——
                # 否则会按「哪个字段非空」猜，而 list_selector 坏了但字段仍非空，
                # 会被当成形态失效去修形态，事件类型也对不上。
                repair.heal_and_report(self.source, soup, list_url=base_url, kind=kind)
            else:
                repair.report_broken(self.source, obs, html=html, url=base_url)
        except Exception as exc:                       # noqa: BLE001
            self._note("selector_heal_failed", f"{type(exc).__name__}: {exc}")


    def _static_quality_ok(self, parsed: Mapping[str, Any]) -> bool:
        content = str(parsed.get("content") or "")
        return bool(parsed.get("title")) and len(content) >= int(self.source.get("minimum_content_chars", 80))

    def fetch_list(self) -> list[str]:
        """List links for this source, trying several strategies in order.

        Sources are heterogeneous, so a single strategy leaves most of them
        empty: static HTML misses JS-built columns, rendering misses nothing but
        costs a browser launch, and crawl4ai is the last resort. Order is cheapest
        first and each step records why it did not work, so a failure is
        diagnosable instead of just "0 records".
        """
        feed_urls = self.feed_urls()
        if feed_urls:
            return feed_urls[: int(self.source.get("max_items", 20))]

        render_mode = self.source.get("render_mode", DEFAULT_RENDER_MODE)
        cap = int(self.source.get("max_items", 20))

        if render_mode == "crawl4ai":
            result = self._crawl4ai_result(self.list_url)
            links = self._crawl4ai_links(result, self.list_url)
            if links:
                return links[:cap]

        html, base_url = "", self.list_url
        try:
            html, base_url = self._list_html()
        except Exception as exc:
            if not self.source.get("playwright_fallback", False):
                # No fallback configured: a broken list page is a broken source,
                # and swallowing it would report "0 records" instead of the cause.
                raise
            self._note("static_list_failed", f"static list fetch failed: {exc}")

        links = self._extract_links(html, base_url) if html else []

        # Static archives first: cheaper than a browser, and it is the only path
        # that reaches 1-10 year history for sites that do not need rendering.
        if len(links) < cap:
            for page_html in self._static_archive_pages():
                # 归档页也是列表页，同样要感知（翻页结构常与首页不同）
                self._observe_selector_health(page_html, base_url)
                links.extend(self._extract_links(page_html, base_url))

        # Still short: the column is JS-rendered or paginated behind a widget.
        if len(links) < cap and self.source.get("playwright_fallback", False):
            try:
                rendered_any = False
                for rendered in self._render_archive(self.list_url):
                    if not _html_has_content(rendered):
                        continue
                    rendered_any = True
                    # 渲染出来的 HTML 同样要过感知。JS 渲染型政府站恰恰是
                    # 最容易改版、最需要自愈的那批 —— 只感知首屏静态 HTML，
                    # 等于对它们完全失明。
                    self._observe_selector_health(rendered, base_url)
                    links.extend(self._extract_links(rendered, base_url))
                # An empty <body> means the browser loaded nothing usable: typically
                # HTTP 412 anti-bot (chengdu.gov.cn does exactly this) rather than a
                # column with no announcements. Staying silent here reported
                # "0 records, no reason" and made blocked sites look empty, so say so.
                if not rendered_any:
                    self._note(
                        "render_empty_body",
                        "browser returned an empty body (likely anti-bot/WAF, not an "
                        f"empty column): {self.list_url}",
                    )
            except Exception as exc:
                self._note("archive_render_failed", f"archive render failed: {exc}")

        if not links and render_mode == "static_then_crawl4ai" and self._crawl4ai_enabled():
            result = self._crawl4ai_result(self.list_url)
            links = self._crawl4ai_links(result, base_url)
        return self._dedupe_links(links)[:cap]

    def _note(self, kind: str, message: str) -> None:
        if not any(item.get("error", "").startswith(message[:40]) for item in self.errors):
            self.errors.append({"url": self.list_url, "error": f"{kind}: {message}"})

    def _dedupe_links(self, links: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for link in links:
            if link not in seen:
                seen.add(link)
                out.append(link)
        return out

    def fetch_detail(self, url: str) -> dict[str, Any]:
        if not self._is_allowed_url(url):
            raise PermissionError(f"URL outside source allowlist: {url}")
        render_mode = self.source.get("render_mode", DEFAULT_RENDER_MODE)
        if render_mode == "crawl4ai":
            result = self._crawl4ai_result(url)
            parsed = self._parse_crawl4ai_result(result, url)
        else:
            parsed = {}
            try:
                response = self._request(url)
                final_url = str(getattr(response, "url", url))
                parsed = self.parse(response, final_url)
            except Exception as exc:
                final_url = url
                if not self.source.get("playwright_fallback", False):
                    raise
                self.errors.append({"url": url, "error": f"static fetch failed, trying rendered page: {exc}"})
            if (not parsed.get("title") or not parsed.get("content")) and self.source.get("playwright_fallback", False):
                rendered = self._render_with_playwright(final_url)
                parsed = self.parse(rendered, final_url)
            if not self._static_quality_ok(parsed) and render_mode == "static_then_crawl4ai" and self._crawl4ai_enabled():
                result = self._crawl4ai_result(final_url)
                parsed = self._parse_crawl4ai_result(result, url)
        if not parsed.get("title") or not parsed.get("content"):
            raise ValueError(f"detail page has no usable title or content: {url}")
        if self._is_section_page(str(parsed.get("title") or "")):
            raise SectionLandingError(str(parsed.get("title")))
        if self._is_phone_directory(str(parsed.get("content") or "")):
            raise SectionLandingError(
                f"phone-directory page (high phone density): {parsed.get('title')}")
        if self._looks_like_listing(str(parsed.get("title") or ""),
                                    str(parsed.get("content") or "")):
            raise SectionLandingError(
                f"listing-shaped body with a column-name title: {parsed.get('title')}")
        record = self.normalize_record(parsed)
        if "result" in locals() and self._crawl4ai_enabled():
            quality = self._crawl4ai_quality(result)
            record["content_source"] = "crawl4ai_fit_markdown"
            record["content_quality"] = quality
            if not quality["usable"] or quality["fit_markdown_fallback"]:
                record["needs_review"] = 1
        return record

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Fetch details for the list links, trying past dead candidates.

        The first link on a gov list page is often "return to home" or a section
        landing, so probing with limit=1 made the whole source look broken. That
        misjudged 40 of 145 sources. Try several candidates before giving up and
        report the real reason: a landing page is skipped, not fatal.

        已知 URL 的处理：跳过它们，但**不会为了凑满 limit 继续往下翻页**。
        列表页是按时间倒序的，越深越旧，而旧的几乎必然早就采过了。实测加上
        跳过之后不设这个上限，nea_gov 从 186.8 秒变成 426.1 秒、mohrss_gov 从
        202.1 秒变成 441.3 秒——它跳掉 61 个已知的，然后一路翻页到超时，
        什么也没多拿到。省抓取的前提是**允许早收工**。
        """
        records: list[dict[str, Any]] = []
        landing_titles: list[str] = []
        stale_pages = 0
        # 连续几个列表页全是已知链接就收工。取 2 是折中：翻一页可能刚好是上个月
        # 的内容还在列表里；连着两页都没有新的，基本就是采完了。
        max_stale_pages = 2
        for url in self.fetch_list():
            if limit is not None and len(records) >= limit:
                break
            if self.out_of_time():
                # Budget spent: hand back what was collected instead of nothing.
                # The batch runner used to discard timed-out sources whole, which
                # is why a full pass returned 0 records for most sources.
                self.errors.append({
                    "url": url,
                    "error": f"deadline reached after {len(records)} records, returning partial result",
                })
                break
            if self.known_urls and url in self.known_urls:
                # 详情页早就存过了。跳过抓取——但要记住它，让连续几个都是这样时
                # 提前收工。只按 URL 精确匹配，不做归一化：URL 变体（带不带 query
                # 串、大小写不同）会被当成新链接多抓一次，那是保守方向（多抓不漏）。
                self.skipped_known += 1
                stale_pages += 1
                if stale_pages >= max_stale_pages:
                    self.errors.append({
                        "url": url,
                        "error": (f"{stale_pages} consecutive list items were already known, "
                                  f"stopping early instead of paging to the timeout"),
                    })
                    break
                continue
            try:
                records.append(self.fetch_detail(url))
                stale_pages = 0
            except SectionLandingError as exc:
                # Expected on list pages: the site chrome is not an announcement.
                landing_titles.append(str(exc))
            except Exception as exc:
                self.errors.append({"url": url, "error": str(exc)})
        if not records and landing_titles and not self.errors:
            self.errors.append({
                "url": self.list_url,
                "error": f"only landing pages found among {len(landing_titles)} candidates: "
                          f"{landing_titles[0][:80]}",
            })
        return records
