"""政府工作报告的采集、解析、分节与指标抽取。

为什么单独一个模块，而不是走 announcements 那条管线：

  1. 度量。报告 2-4 万字，混进 announcements 会让 median_content_chars、
     under_200_chars_rate、chrome_residue_rate 全部失真——那三个指标正是用来
     判断公告质量的。实测一份 45565 字的北京市报告在公告摘要路径下产出了
     4636 字的"摘要"，22% 的记录摘要超过 600 字上限。
  2. 文体。报告是长篇连续散文且章节结构规整，公告是高度模板化的短文
     （991/1885 条标题含"关于"，553/1885 含"的通知"）。两者的解析策略相反。
  3. 产出。公告的产出是"这条通知讲了什么"，报告的产出是数值与年度序列。

不做摘要。报告的价值不在"用 200 字概括"，而在"某地区某年达到了什么数值"，
以及"这些年份之间变了多少"。所以这里做的是分节 + 指标抽取。
"""
from __future__ import annotations

import html as html_lib
import re
import unicodedata
from typing import Any

# ── 章节切分 ──────────────────────────────────────────────
# 政府工作报告的一级标题高度规整：上海市 2026 年报告是
#   一、2025年工作回顾 / 二、关于《…五年规划纲要（草案）》
#   三、2026年主要任务 / 四、深入推进政府治理创新
# 北京市与重庆市的归档页 URL 也同构（./YYYYMM/tXXXXXX_XXXXXXX.html）。
# 数字一到十即可覆盖直辖市、省、市三级现行体例。
_HEADING_RE = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*[、.．]\s*(\S.{2,48})$")

# 章节归类。
#
# 顺序有讲究：target 必须排在 plan 前面。早先把"建议"归给 plan，于是
# 「二、2014年工作建议」「三、2022年重点工作」这类**年度目标**段落被判成规划类，
# is_target 变 False，本年的目标值被当成实际值。交叉验证一次报出 12 处
# 同类冲突，全是"一、20XX年工作回顾"的实际值 vs "二、20XX年工作建议"的目标值。
# 「建议」既可指年度工作建议（本年目标），也可指《五年规划纲要》的说明
# （长期规划），靠词表区分不开，所以顺序决定归属：目标类词先匹配，
# 落不到目标才当规划。
_SECTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("review", ("工作回顾", "回顾", "过去一年", "上年", "年度工作", "工作成效",
                "取得显著成就", "发展历程")),
    ("target", ("主要目标", "目标", "主要任务", "重点任务", "工作任务", "主要安排",
                "工作安排", "总体要求", "工作建议", "主要工作", "重点工作",
                "工作打算", "奋斗目标", "工作思路", "工作措施", "行动计划")),
    ("plan", ("规划", "建议", "纲要", "计划", "布局", "展望")),
    ("reform", ("政府治理", "自身建设", "改革", "作风", "廉政")),
)


def _classify_section(heading: str) -> str:
    for name, cues in _SECTION_RULES:
        if any(cue in heading for cue in cues):
            return name
    return "other"


# 段落内容里的目标信号。回顾段不会写"预期目标"，本年目标段几乎一定写。
# 只认"预期目标"：早先把"主要目标""经济社会发展目标"也算信号，结果规划说明段
# 反而被误判——上海 2016 的「二、关于《…第十三个五年规划纲要(草案)》的说明」
# 正文里讨论的就是"经济社会发展目标"，被判成 target；而真正该判的「三、全力
# 以赴做好"十三五"开局工作」写的是"主要预期目标"。"预期目标"才有区分度。
_TARGET_SENTENCE_RE = re.compile(r"预期目标")


def _split_cells(line: str) -> list[str]:
    """把一行按 2+ 空格切成格子，丢掉空格子。"""
    return [cell.strip() for cell in _CELL_SPLIT.split(line) if cell.strip()]


def split_sections(text: str) -> tuple[str, list[dict[str, Any]]]:
    """按一级标题切段。返回 (前言, sections)。

    前言是第一个标题之前的内容——政府工作报告的"一、xxx年工作回顾"之前
    通常有一段"现在，我代表……人民政府，向大会报告政府工作"的套话，不属于
    任何章节，单列出来免得污染 review 段。
    """
    matches = list(_INLINE_HEADING.finditer(text or ""))
    if not matches:
        return (text or "").strip(), []
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():stop].strip()
        sections.append({
            "order": index + 1,
            "heading": match.group(0).strip(),
            "number": match.group(1),
            "name": match.group(2).strip(),
            "key": _classify_section(match.group(2)),
            "text": body,
            "chars": len(body),
        })
    # 内容级兜底：标题里没有"目标/任务/建议"字样、但正文写着"主要预期目标"
    # 的段落，本质仍是本年目标段。上海 2016 那份的
    # 「三、全力以赴做好"十三五"开局工作」就是一个——标题一个目标词都没有，
    # 只有正文里"城镇登记失业率控制在4.4%以内"能认出它是目标段。交叉验证
    # 因为它报出 4.1%（上一年实际）与 4.4%（本年目标）同格冲突。
    # 补标题关键词是打补丁，内容信号才是本质，所以两层都留。
    for section in sections:
        if section["key"] != "review" and _TARGET_SENTENCE_RE.search(section["text"]):
            section["key"] = "target"
    return text[:matches[0].start()].strip(), sections


# ── 年份解析 ──────────────────────────────────────────────
# 不用 publish_date：每年报告发布日不同（上海 2026 年报告发布于 02-25），
# 而归档列表页的链接文字里就带年份，最可靠。
_YEAR_RE = re.compile(r"(19[5-9]\d|20[0-4]\d)\s*年")
_TITLE_YEAR_RE = re.compile(r"(?:^|[^\d])((?:19[5-9]|20[0-4])\d)\s*年")


def parse_report_year(title: str, url: str = "", text: str = "") -> int | None:
    """从标题/URL/正文里取报告年份。

    优先标题：归档列表的链接文字通常是"2026年上海市政府工作报告"或
    "上海市2026年政府工作报告（2026年1月20日）"。
    """
    for source in (title, url):
        match = _TITLE_YEAR_RE.search(source or "")
        if match:
            year = int(match.group(1))
            # 归档页 URL 里的 /202602/ 之类不算，那是发布月份
            if 1950 <= year <= 2049:
                return year
    match = _YEAR_RE.search(text or "")
    if match:
        return int(match.group(1))
    return None


# ── 正文提取 ──────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
# 段落内部的"格子"。政府站的排版有两种形态：
#   标题独占一段        "一、2025年工作回顾"
#   标题与正文同段      "现在,我代表…报告工作…     一、2012年工作回顾     2012年,是本届…"
# 后者在武汉 2013 那份里整份都是，早先只按行首匹配，导致它分出 0 节。
# 按 2 个以上空格（含全角空格）切格子，标题在哪一格都能认出来。
_CELL_SPLIT = re.compile(r"[ \t　]{2,}")

# 标题可以不在线首。实测三种变体：
#   "一、2025年工作回顾"                      标题独占一段
#   "…报告工作…     一、2012年工作回顾     2012年…"  行内，多个空格分隔
#   "…报告工作。 一、2006年工作回顾 2006年…"          行内，单个空格分隔（上海 2007/2010、广州 2015）
# 前两种按行切格子能处理，第三种切不出格子；而且"标题必须行首"的写法对三种
# 全都只覆盖第一种。改成全文扫描，三种走同一条路径。
# 前置条件：前面是行首或标点/空白，后面紧跟空白或行尾。这样正文里的
# "一是…"（是而非顿号）与阿拉伯数字编号不会被误判成章节标题。
_INLINE_HEADING = re.compile(
    r"(?:^|(?<=[\s。，、；：！？]))"
    r"([一二三四五六七八九十]{1,3})\s*[、.．]\s*"
    r"([^\s。，；！？]{2,45})"
    r"(?=\s|$)"
)

# 页脚。广州 2015 那份把版权、联系方式、网站标识码、ICP 备案号都混进了正文段落。
_FOOTER_RE = re.compile(
    r"^(?:版权所有|主办单位|承办单位|网站标识码|备案号|ICP备案号|技术支持|"
    r"地址[：:]|邮编[：:]|电话[：:]|传真[：:]|邮箱[：:]|扫一扫|分享到|打印|"
    r"来源[：:]|编辑[：:]|责任编辑)"
)


def extract_body(html: str) -> str:
    """从详情页 HTML 抽正文。

    政府工作报告正文全在 <p> 里（实测上海 2026 年报告 112 段 / 20563 字），
    按 <p> 抽取比整页去标签干净得多：不会把导航、相关阅读、字号工具条
    一起吞进来——那正是公告路径上 chrome_residue_rate 居高不下的原因。
    """
    if not html:
        return ""
    stripped = _SCRIPT_RE.sub(" ", html)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", stripped, re.S | re.I)
    kept: list[str] = []
    for para in paragraphs:
        text = _TAG_RE.sub("", para)
        # 必须用 html.unescape 而不是手写 &nbsp;/&amp; 列表。政府站正文里
        # 大量使用 &emsp;&emsp; 缩进和 &ldquo;/&rdquo; 引号，未解码时行首变成
        # "&emsp;&emsp;一、2024年工作回顾"，锚定 ^\s* 的标题正则一个都匹配不到：
        # 上海 2025 与 2024 两份报告因此分出 0 节，而 2026 那份恰好没有缩进实体。
        text = html_lib.unescape(text)
        text = re.sub(r"[ \t　]+", " ", text).strip()
        # 短标题段落要留：早先的 >=12 下限把 10 个字的"一、2025年工作回顾"丢掉，
        # 上海 2026 报告因此只分出 2 节。页脚要丢：广州 2015 那份把版权、
        # 联系方式、网站标识码、ICP 备案号都混在正文段落里。
        if _FOOTER_RE.match(text):
            continue
        if len(text) >= 12 or (len(text) >= 4 and _HEADING_RE.match(text)):
            kept.append(text)
    if kept:
        return "\n".join(kept)
    # 没有 <p> 的降级路径：整页去标签
    text = html_lib.unescape(_TAG_RE.sub("\n", stripped))
    return "\n".join(line.strip() for line in text.splitlines() if len(line.strip()) >= 4)


def normalize(text: str) -> str:
    """全角数字/字母转半角，避免"２０２６年"这类写法漏掉年份解析。"""
    return unicodedata.normalize("NFKC", text or "")
