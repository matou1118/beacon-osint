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

# 章节归类。键是标题里出现的特征词，值是标准段落名。
# 顺序有意义：先匹配"回顾"再看"目标"，因为"过去一年工作回顾"里也常出现
# "完成"和数字，不能因为它含"工作"就落到重点任务里。
_SECTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("review", ("工作回顾", "回顾", "过去一年", "上年", "年度工作", "工作成效")),
    ("plan", ("规划", "建议", "纲要", "计划", "布局")),
    ("target", ("主要目标", "目标", "主要任务", "重点任务", "工作任务", "主要安排",
                "下一阶段", "工作安排", "总体要求")),
    ("reform", ("政府治理", "自身建设", "改革", "作风", "廉政")),
)


def _classify_section(heading: str) -> str:
    for name, cues in _SECTION_RULES:
        if any(cue in heading for cue in cues):
            return name
    return "other"


def split_sections(text: str) -> tuple[str, list[dict[str, Any]]]:
    """按一级标题切段。返回 (前言, sections)。

    前言是第一个标题之前的内容——政府工作报告的"一、xxx年工作回顾"之前
    通常有一段"现在，我代表……人民政府，向大会报告政府工作"的套话，不属于
    任何章节，单列出来免得污染 review 段。
    """
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    preface: list[str] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    for line in lines:
        stripped = line.strip()
        match = _HEADING_RE.match(stripped) if stripped else None
        # 标题本身要够短且不能以句号收尾，否则正文里"一、xxx。"也会被当成标题
        if match and len(stripped) <= 60 and not stripped.endswith(("。", "；", "!")):
            if current is not None:
                current["text"] = "\n".join(buffer).strip()
                current["chars"] = len(current["text"])
                sections.append(current)
            current = {
                "order": len(sections) + 1,
                "heading": stripped,
                "number": match.group(1),
                "name": match.group(2).strip(),
                "key": _classify_section(match.group(2)),
            }
            buffer = []
            continue
        if current is None:
            if stripped:
                preface.append(stripped)
        else:
            buffer.append(line)

    if current is not None:
        current["text"] = "\n".join(buffer).strip()
        current["chars"] = len(current["text"])
        sections.append(current)

    return "\n".join(preface).strip(), sections


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
        # A plain length floor drops the section headings: "一、2025年工作回顾" is
        # 10 characters, so a >=12 filter silently discarded 上年回顾 and 主要任务
        # and left the report looking like it had two sections. Short paragraphs
        # that look like a heading are kept; everything else needs the floor.
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
