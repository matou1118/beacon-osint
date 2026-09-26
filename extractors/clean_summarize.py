"""Deterministic clean + summarise for stored announcement bodies.

The pipeline is scrape -> clean -> summarise -> return. Without the clean step
the store keeps page chrome ("相关阅读推荐 外交部 国防部…", breadcrumbs, print
controls), which inflates the corpus and poisons downstream comparison.

The hard part on Chinese government portals is the font-size switcher
("大 中 小" / "【大】"). It is a header control on most pages, so the article
starts *after* it -- but on some layouts it sits in the footer and the article
ends *before* it. Trimming it unconditionally as a footer marker destroyed the
body on 81 of 245 records (807 chars in, 37 chars out). Position decides which
side is kept: measured 81/81 pages with the switcher in the first third have the
article after it.

Summaries are extractive: real sentences lifted from the source, ordered by a
fixed score, plus a fixed template. No model, no paraphrase, so the output is
reproducible and testable, per AGENTS.md 1 and 13.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Page chrome seen on gov.cn portals and ministry sites.
CHROME_PATTERNS = (
    r"^相关阅读.*$", r"^推荐阅读.*$", r"^热门搜索.*$", r"^您可能对以下文章感兴趣.*$",
    r"^扫描二维码.*$", r"^打印本页.*$", r"^关闭窗口.*$", r"^字体[:：]?.*$", r"^字号[:：]?.*$",
    # Registration and copyright lines carry no article content, and portals
    # spell them several ways ("京ICP备05046837号-1", "粤ICP 证", "公网安备110...").
    r"^[^\n]{0,20}ICP\s*备?[\s\d.\-号]*$", r"^[^\n]{0,20}公网安备[\s\d号]*$",
    r"^(?:网站标识码|主办单位|承办单位|开发单位|技术支持)\s*[:：].{0,40}$",
    # "网站地图" and "版权所有" are nav labels, so only a line that is nothing
    # else counts. Matching them inline deleted body text on 31 pages.
    r"^(?:网站地图|版权所有|免责声明)\s*$",
    r"^分享到[:：]?.*$", r"^收藏.*$", r"^责任编辑[:：].*$", r"^文章来源[:：].*$",
    r"^上一篇[:：].*$", r"^下一篇[:：].*$", r"^附件[:：]?$", r"^附件下载.*$",
    r"^手机版.*$", r"^邮箱\s*EN.*$", r"^网站地图.*$", r"^版权所有.*$", r"^主办单位[:：].*$",
    r"^承办单位[:：].*$", r"^技术支持[:：].*$", r"^备案号.*$", r"^网站标识码.*$",
    r"^访问量[:：].*$", r"^发布时间[:：]?\s*\d{4}-\d{2}-\d{2}.*$", r"^\s*\[打印\]\s*$",
    r"^无障碍.*$", r"^长辈版.*$", r"^返回顶部.*$", r"^导航.*$",
)
CHROME_RE = re.compile("|".join(CHROME_PATTERNS))

HEAD_CUT_RE = re.compile(r"(?:当前位置|您所在的位置|你的位置|您当前的位置)\s*[:：]")
# News portals use a bare breadcrumb with no label ("首页 > 新闻 > 全球频道"), and
# 政务 sites emit a channel chain with no 首页 prefix at all
# ("要闻动态>公示公告>公示>公示关于……的公示"). Two or more ">"-joined CJK segments at
# the start of a line is a breadcrumb; prose does not use ">" as a separator.
BREADCRUMB_RE = re.compile(
    r"^\s*(?:首页|网站首页)\s*[>＞]\s*[^\n]{0,60}?(?=\s{2,}|[^\s>＞])"
    r"|^\s*[一-鿿]{2,10}(?:[>＞][一-鿿]{2,10}){1,}[>＞]?"
)

# Inline detail-page metadata that list pages glue between the title and the body
# ("……的通知索引号：357A08-03-2026-0003文号：文旅非遗发〔2026〕……",
# "……月亮怎么拍2026-09-23 18:28:23来源：新华社……"). Removal, not cutting: a
# cut-from rule deletes the article, because the metadata sits *before* the body.
# 19.4% of stored summaries carried these runs.
# Values are bounded per key rather than matched to end-of-line: "资金来源：财政
# 资金" is a real policy fact, so the generic 来源 key is deliberately excluded.
METADATA_RE = re.compile(
    r"(?:索引号|文号|信息源|信息来源|稿件来源|文章地址|原文链接|超链接地址|主题分类|"
    r"发布机构|公文种类|解读形式|有效性|公开方式|责任部门|文秘)\s*[:：]\s*[^\s，。；、]{1,80}"
    r"|(?:发布时间|发文日期|成文日期|发布日期|浏览次数|点击量|阅读次数)\s*[:：]\s*[^\n]{1,30}"
    r"|\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}(?::\d{2})?"
    r"|(?:来源|出处)\s*[:：]\s*(?:新华社|新华网|人民网|央视网|央广网|经济日报|本报|本台|记者)[^\s，。；]{0,10}"
    # Bare 来源： on a 政务 detail page is the originating desk ("来源：安全与管制局",
    # "来源：中国建设报"), not a policy fact. The lookbehinds keep the real ones
    # ("资金来源：财政资金", "财政来源", "项目来源") intact.
    r"|(?<!资金)(?<!财政)(?<!项目)(?<!货源)(?:来源|出处)\s*[:：]\s*[^\s，。；、]{2,20}"
    r"|选择字体\s*[:：]?|字号\s*[:：]\s*$"
    # A date printed twice in a row is a list page showing it in the row and again
    # in the detail header: "...公示2026-09-24 2026-09-24[大][中][小]..." (11.6%).
    r"|(\d{4}-\d{2}-\d{2})(?=\s*\1)"
)

# The font-size switcher. Position decides which side survives: a switcher in the
# first third is a header control (article follows), one in the last third is a
# footer control (article precedes it). Verified against 245 stored pages.
# Forms seen: "【大 中 小 】", "【字号： 大 中 小 】", "大 中 小", "字体： 小 中 大"
# (news.cn reverses the order), "【大】", and the ASCII-bracket form some 政务
# sites use: "[大][中][小]".
FONT_SWITCH_RE = re.compile(
    r"(?:[【\[（(]\s*(?:字号\s*[:：]\s*)?[大小中]\s*(?:[大小中]\s*)*[】\]）)]|大\s*中\s*小|"
    r"字体\s*[:：]\s*[大小中]\s*(?:[大小中]\s*){0,2})"
)
HEAD_ZONE = 0.34
TAIL_ZONE = 0.66

# Markers that end the article wherever they appear: "相关阅读" style blocks sit
# between the body and the footer, so a position test would miss them.
# Markers that end the article wherever they appear: "相关阅读" blocks sit between
# the body and the footer, so a position test would miss them.
DROP_FROM_RE = re.compile(
    r"相关阅读|相关文章|推荐阅读|热门搜索|您可能对以下文章感兴趣|责任编辑[:：]|"
    r"责编[:：]|文章来源[:：]"
)
# Share and QR blocks appear both before the body (a share bar under the headline)
# and after it. Cutting on them unconditionally removed ~1.7k chars of article from
# news pages, so they only count past the tail zone.
SHARE_CHROME_RE = re.compile(
    r"分享到微信|分享至朋友圈|分享到[:：]|扫一扫|打开微信|点击底部的"
)

# Whatever the position logic leaves behind. split_on_font_switcher keeps one side
# of the switcher, so "[大][中][小]" can survive as "[中] [小]" at the head of the
# body (every mee.gov.cn row). A bracketed single 大/中/小 is a font control and
# nothing else.
RESIDUAL_SWITCH_RE = re.compile(r"[\[【（(]\s*[大小中]\s*[\]】）)]")

# Footer chrome. Only counts when it sits in the footer: gov.cn portals repeat the
# same nav labels ("网站地图", "主办单位") in the header toolbar as well, and cutting
# on a first-occurrence match wiped the body of 27 of 245 pages.
TAIL_CHROME_RE = re.compile(
    r"打印此页|【\s*打印此页\s*】|【\s*关闭窗口\s*】|关闭窗口|网站地图|主办单位[:：]|"
    r"承办单位[:：]|技术支持[:：]|网站标识码|ICP备|版权所有"
)

# Header controls that sit immediately before the body and must be skipped, not
# treated as a cut point.
SKIP_PREFIX_RE = re.compile(
    r"^[\s\W]*(?:中文|English|无障碍|打印本页|【\s*打印本页\s*】|分享|收藏|字号\s*[:：])[\s\W]*"
)

# Sentences that carry the actionable core of a notice: who must do what, by when,
# how much. Ordered by weight; the template joins the top hits.
SIGNAL_PATTERNS = (
    # Deadline. The cue word and the date are often separated ("申报截止时间为
    # 2026年9月30日"), so a fixed gap would miss the single most compared fact.
    (r"(?:截止|截至|不迟于|最迟)(?:时间|日期|日)?(?:为|是|在|:|：)?\s*"
     r"\d{4}年\d{1,2}月\d{1,2}日", 5, "时限"),
    (r"(?:截止|截至)日期?\s*[:：]?\s*\d{4}年\d{1,2}月\d{1,2}日", 5, "时限"),
    (r"\d{4}年\d{1,2}月\d{1,2}日(?:之)?前(?:完成|提交|报送|申报|上报|拨付)", 5, "时限"),
    (r"(?:自|从)\s*\d{4}年\d{1,2}月\d{1,2}日(?:起|开始|施行|实施|执行|至|到)", 5, "生效"),
    (r"\d{4}年\d{1,2}月\d{1,2}日(?:至|—|-)\d{4}年\d{1,2}月\d{1,2}日", 4, "时段"),
    (r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿)(?:元)?", 4, "金额"),
    (r"(?:支持标准|补助标准|补贴标准|资助标准|奖励标准|扶持力度)", 4, "标准"),
    (r"(?:申报条件|申报范围|申报方式|申报材料|申报时间|申报流程|申报主体|申报截止|申报要求|申报)", 4, "申报"),
    (r"(?:支持对象|申报主体|适用对象|面向(?:的)?[^\s，。]{2,12})", 3, "对象"),
    (r"(?:应当|必须|须|应)(?:于|在)?[^，。；]{2,30}(?:申报|报送|提交|办理|上报)", 3, "义务"),
    (r"(?:不(?:再)?(?:予|得)受理|视为(?:自动)?放弃|逾期(?:视同)?作废|无效)", 3, "排除"),
    (r"(?:特此(?:公告|通知|通告|公示)|请(?:各|有关|贵)|现将[^。]{0,40}予以?公布)", 2, "结语"),
    (r"(?:根据|依据|按照)[^，。；]{2,40}(?:法|条例|办法|规定|意见|方案|通知)", 2, "依据"),
    (r"(?:一|二|三|四|五)、", 2, "条目"),
)
# Split on sentence punctuation and hard line breaks. Enumeration commas
# ("各省、自治区、") are not sentence ends and must not be cut on: doing so
# truncated policy clauses mid-word. "；" ends a clause but stays attached, so the
# summary layer can tell a truncated tail from a finished sentence.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？])|\n+")
BOILERPLATE_RE = re.compile(r"^(?:附件[:：]?\s*)?$")
MIN_SENTENCE = 12
# Policy clauses run long ("各省、自治区、直辖市…申报截止时间为2026年9月30日").
# At 220 chars a single clause lost its own deadline, which is the one fact a
# comparison needs.
MAX_SENTENCE = 400
SUMMARY_TARGET = 6


def _split_long(sentence: str, limit: int = MAX_SENTENCE) -> list[str]:
    """Break an over-long block on its own internal punctuation.

    A 2k-character run of "电子邮箱：…传真：…通讯地址：…" is one block but several
    facts. Cutting on commas keeps the facts individually selectable.
    """
    if len(sentence) <= limit:
        return [sentence]
    parts = re.split(r"(?<=[，,、])", sentence)
    out: list[str] = []
    buffer = ""
    for part in parts:
        if len(buffer) + len(part) <= limit:
            buffer += part
            continue
        if buffer:
            out.append(buffer)
        buffer = part
        while len(buffer) > limit:
            out.append(buffer[:limit])
            buffer = buffer[limit:]
    if buffer:
        out.append(buffer)
    return [p for p in out if p]


def split_on_font_switcher(text: str) -> str:
    """Keep the article side of the font-size switcher.

    Unconditional footer trimming here is what reduced an 807-char article to
    37 chars: the switcher is a header control on most Chinese gov pages.
    """
    match = FONT_SWITCH_RE.search(text)
    if not match:
        return text
    ratio = match.start() / max(1, len(text))
    if ratio < HEAD_ZONE:
        kept = text[match.end():]
        # The switcher is followed by the rest of the header toolbar
        # ("打印本页", "分享", "收藏", "字号："). Drop that run, not the article.
        skip = re.match(
            r"[\s\W]*(?:打印本页|【\s*打印本页\s*】|打印此页|分享|收藏|字号\s*[:：]|字体\s*[:：])[\s\W]*",
            kept,
        )
        return kept[skip.end():] if skip else kept
    if ratio > TAIL_ZONE:
        return text[: match.start()]
    return text


def trim_tail_chrome(text: str) -> str:
    """Cut trailing boilerplate.

    Three rules, because position alone is not enough. "相关阅读" blocks sit
    between the body and the footer, so those markers cut wherever they appear.
    Share and QR buttons are behaviour, not prose, so they also cut anywhere.
    Plain footer labels ("网站地图", "主办单位") repeat in the header toolbar, so
    only the *last* run counts -- taking the first wiped the body of 27 of 245
    pages, and skipping the switcher case entirely wiped 24 more.
    """
    drop = DROP_FROM_RE.search(text)
    best = drop.start() if drop else len(text)
    for pattern in (TAIL_CHROME_RE, SHARE_CHROME_RE):
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        candidate = matches[0] if len(matches) == 1 else matches[-1]
        if candidate.start() / max(1, len(text)) >= TAIL_ZONE:
            best = min(best, candidate.start())
    kept = text[:best]
    # The cut lands mid-token ("京ICP备…" keeps the province prefix, "网站标识码"
    # keeps its label), so drop the orphan fragment in front of the marker. Kept
    # narrow on purpose: a bare "号" also ends ordinary sentences.
    orphan = re.search(r"[^\n]{0,8}(?:ICP\s*备|网站标识码|公网安备|备编号)[\s:：]*$", kept)
    if orphan:
        kept = kept[: orphan.start()]
    return kept


def strip_chrome(text: str) -> str:
    """Remove navigation, print controls and breadcrumbs from a page body."""
    lines = [line for line in str(text or "").splitlines() if line.strip() and not CHROME_RE.match(line.strip())]
    cleaned_lines: list[str] = []
    for line in lines:
        trimmed = line.strip()
        head = HEAD_CUT_RE.search(trimmed)
        if head:
            # Keep the tail, then fall through to the footer trimming below.
            trimmed = trimmed[head.end():]
        else:
            crumb = BREADCRUMB_RE.match(trimmed)
            if crumb:
                trimmed = trimmed[crumb.end():]
        if not trimmed.strip():
            continue
        trimmed = METADATA_RE.sub("", trimmed)
        if not trimmed.strip():
            continue
        trimmed = split_on_font_switcher(trimmed)
        trimmed = RESIDUAL_SWITCH_RE.sub("", trimmed)
        trimmed = trim_tail_chrome(trimmed)
        trimmed = re.sub(r"[·、,，。:：\-—|]+$", "", trimmed).strip()
        if trimmed:
            cleaned_lines.append(trimmed)
    return re.sub(r"\n{2,}", "\n", "\n".join(cleaned_lines)).strip()


def dedupe_sentences(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    # Split before stripping whitespace: \n+ is a split boundary, and collapsing
    # it first made every block one run-on sentence.
    for raw in SENTENCE_SPLIT_RE.split(text or ""):
        for piece in _split_long(re.sub(r"[^\S\n]+", "", raw or "").strip()):
            if not piece or BOILERPLATE_RE.match(piece):
                continue
            if not MIN_SENTENCE <= len(piece) <= MAX_SENTENCE:
                continue
            if piece in seen:
                continue
            seen.add(piece)
            out.append(piece)
    return out


def score_sentence(sentence: str, position: int, total: int) -> int:
    score = 0
    for pattern, weight, _ in SIGNAL_PATTERNS:
        if re.search(pattern, sentence):
            score += weight
    # Signal density, not position, decides. A late sentence that packs amount,
    # deadline and applicant into one line matters more than an early one-liner.
    signals = sum(1 for pattern, _, _ in SIGNAL_PATTERNS if re.search(pattern, sentence))
    score += signals * 2
    if sentence.startswith("特此") or sentence.startswith("请"):
        score += 1
    if sentence.endswith("。"):
        score += 1
    return score


# Nav bars. When a list page yields no article body the collector stores the whole
# navigation, and a nav bar wins on length and position: the stored summaries for
# cnenergy/cena read "八月全社会用电量再破万亿广告人民日报主管《中国能源报》社
# 有限公司主办网站地图联系我们首页……". 27.3% of summaries carried these labels.
# An empty summary is honest; a nav-bar summary is not, so the whole thing is
# dropped rather than trimmed.
NAV_LABELS = (
    "网站地图", "联系我们", "主办", "主管", "投稿", "数字报", "微信公众号", "新浪微博",
    "抖音号", "视频号", "返回顶部", "关于我们", "版权声明", "免责声明", "广告",
    "首页", "时政要闻", "产业新闻", "政策解读", "产业分析", "省市新闻", "信息消费",
    "焦点关注", "环球视野", "人事动态", "生态环保", "能源党建", "商业航天",
)
# A run of this many *distinct* nav labels means navigation, not prose. Real
# articles mention one or two at most.
NAV_RUN_THRESHOLD = 6


def nav_label_hits(text: str) -> int:
    return sum(1 for label in NAV_LABELS if label in (text or ""))


def summarize(text: str, limit: int = SUMMARY_TARGET) -> dict[str, Any]:
    """Extractive summary: highest-signal source sentences, source order kept."""
    body = strip_chrome(text)
    sentences = dedupe_sentences(body)
    if not sentences:
        return {"clean_text": body, "summary": "", "key_points": [], "tags": [], "cleaned_chars": len(body)}
    if nav_label_hits(body) >= NAV_RUN_THRESHOLD:
        return {
            "clean_text": body,
            "summary": "",
            "key_points": [],
            "tags": [],
            "cleaned_chars": len(body),
            "drop_reason": "nav_chrome",
        }
    ranked = sorted(
        range(len(sentences)),
        key=lambda index: (-score_sentence(sentences[index], index, len(sentences)), index),
    )[: max(1, limit)]
    chosen = sorted(ranked)
    tags: list[str] = []
    for pattern, _, label in SIGNAL_PATTERNS:
        if any(re.search(pattern, sentences[index]) for index in chosen) and label not in tags:
            tags.append(label)
    return {
        "clean_text": body,
        "summary": "".join(sentences[index] for index in chosen),
        "key_points": [sentences[index] for index in chosen],
        "tags": tags,
        "cleaned_chars": len(body),
    }


def process_record(record: dict[str, Any], limit: int = SUMMARY_TARGET) -> dict[str, Any]:
    """Return the record with clean_text/summary/key_points filled in."""
    result = summarize(str(record.get("content") or ""), limit=limit)
    record["content"] = result["clean_text"]
    record["summary"] = result["summary"]
    record["key_points"] = result["key_points"]
    record["extraction_tags"] = result["tags"]
    return record


def apply_to_rows(rows: Iterable[dict[str, Any]], limit: int = SUMMARY_TARGET) -> list[dict[str, Any]]:
    return [process_record(row, limit=limit) for row in rows]
