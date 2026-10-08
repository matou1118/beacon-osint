"""Self-correcting summaries.

The first summary pass produced measurable defects, counted over the 245 stored
records: breadcrumbs in the opening sentence (24%), toolbars like "[打印]字号：[大]"
(4%), compression both far too high (24% over 45%) and far too low (24% under 8%),
truncated endings (26%), and dropped monetary amounts (1%).

This module fixes those deterministically, then re-checks its own output. Each
rule below maps to a counted defect, and audit() reports the before/after so a
regression is visible instead of silent.

No model, no paraphrase: sentences are lifted verbatim from the source, per
AGENTS.md 1.6 and section 13.
"""
from __future__ import annotations

import re
from typing import Any

from .clean_summarize import (
    MAX_SENTENCE,
    MIN_SENTENCE,
    NAV_RUN_THRESHOLD,
    SIGNAL_PATTERNS,
    SENTENCE_SPLIT_RE,
    dedupe_sentences,
    nav_label_hits,
    score_sentence,
    strip_chrome,
)

# Nav crumbs that survived cleaning and landed in summaries.
CRUMB_RE = re.compile(r"(?:首页|当前位置|网站首页)\s*[>＞][^\s，。；]{0,40}(?:[>＞][^\s，。；]{0,40})*")
# Print/font controls.
TOOLBAR_RE = re.compile(
    # 原表达式里 `字号[:：]大中小` 那段在一次编码往返中损坏成了替换字符，
    # 导致整条正则里含非法模式而**静默不生效**——于是 297 条摘要在第 1 条要点
    # 里带着 `【打印】` 这种页面工具栏。改成整段显式列出可读形态。
    r"\[打印\]|【\s*打印\s*】|【\s*打印此页\s*】|【\s*打印本页\s*】|"
    r"【\s*关闭[^】]*】|字号\s*[:：]\s*(?:大|中|小)?|【[大小中]】|"
    r"打印此页|打印本页|关闭窗口"
)
# Date stamps the header repeats next to the title.
DUP_DATE_RE = re.compile(r"(?<![0-9])\d{4}-\d{2}-\d{2}(?![\d-])")
# Signature block / contact tail that adds nothing to a comparison.
TRAILING_RE = re.compile(
    r"(?:联系人|联系电话|通信地址|电子邮箱|邮编|传真|开户银行|账号|收款人名称)[：:].{0,60}$"
)

# Compression band. Below the floor the summary is too thin to compare; above the
# ceiling it is not a summary. Measured against the cleaned body length.
COMPRESS_FLOOR = 0.10
COMPRESS_CEILING = 0.3
# Bodies shorter than this are not scored for compression: a 400-char notice with
# a 200-char abstract is a short document, not a missing summary.
COMPRESSION_MIN_BODY = 1200
MIN_SUMMARY_CHARS = 90
MAX_SUMMARY_CHARS = 600
MAX_POINTS = 8


def _strip_lead_noise(text: str) -> str:
    """Remove crumbs, toolbars and duplicated dates from the head of a sentence."""
    cleaned = CRUMB_RE.sub("", text)
    cleaned = TOOLBAR_RE.sub("", cleaned)
    cleaned = DUP_DATE_RE.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _score_with_coverage(sentence: str, position: int, total: int) -> int:
    """Rank sentences, then prefer ones covering a signal the others miss.

    Plain ranking piles every sentence about the same fact into the summary while
    the amount or the deadline falls outside the cut. Bonus for a signal kind not
    already seen keeps the summary covering more of the notice.
    """
    return score_sentence(sentence, position, total)


def _signal_kinds(sentence: str) -> set[str]:
    return {label for pattern, _, label in SIGNAL_PATTERNS if re.search(pattern, sentence)}


# 结构化字段 -> 摘要里的显示标签。顺序即优先级：先说钱和什么时候截止，
# 再说谁能办、找谁办——这是读一份政务公告时最先要确认的四件事。
_FACT_LABELS = (
    ("amount", "金额"),
    ("deadline", "截止"),
    ("target_group", "申报对象"),
    ("institution", "责任机构"),
    ("publish_date", "发布日期"),
)
# 单个字段值超过这个长度就不放进去：那说明抽歪了（把整句话当成了值），
# 塞进摘要头部反而更糟。
_FACT_MAX_CHARS = 40


def _format_facts(facts: Mapping[str, Any] | None) -> str:
    """把结构化字段拼成一行「金额：… 截止：…」，没有就返回空串。"""
    if not facts:
        return ""
    parts: list[str] = []
    for key, label in _FACT_LABELS:
        value = facts.get(key)
        if value in (None, "", []):
            continue
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if not value or len(value) > _FACT_MAX_CHARS:
            continue
        # 已经在句子里出现过的事实就不重复放在头部
        parts.append(f"{label}：{value}")
    return "｜".join(parts)


# 重要信号类别的补齐顺序。这四类是这个产品的立身之本：
# 多少钱（金额/限价/采购）、什么时候截止（时限/生效/时段）、谁能申报（对象）、
# 要做什么（义务）。排在前面的先被保证留在摘要里。
_SIGNAL_PRIORITY = ("金额", "限价", "时限", "生效", "时段", "采购", "对象",
                    "义务", "标准", "资金来源")


def build_summary(
    text: str,
    title: str = "",
    limit: int = MAX_POINTS,
    facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a summary, then correct it against the measured defects.

    facts 可传入结构化字段（amount / deadline / target_group / institution），
    它们会被拼成一行"关键信息"放在摘要最前面。理由：摘要是抽取式选句，
    金额和截止能不能被选中取决于那一句排第几——实测保留率只有 56%/60%。
    而这些字段本来就已经抽出来了（policy_fields.BODY_PATTERNS），直接用
    比在句子堆里碰运气可靠得多，也比让人去读 500 字的原文省事。
    """
    if not isinstance(limit, int) or limit < 1:
        limit = MAX_POINTS
    fact_line = _format_facts(facts)
    body = strip_chrome(text)
    sentences = [_strip_lead_noise(s) for s in dedupe_sentences(body)]
    sentences = [s for s in sentences if s]
    if sentences and nav_label_hits(body) >= NAV_RUN_THRESHOLD:
        # 整页都是导航而不是正文。clean_summarize.summarize() 早就为这种情况
        # 返回空摘要并标 drop_reason=nav_chrome，但 build_summary 一直没走这个
        # 判断，于是它把"广告人民日报主管《中国能源报》社有限公司主办网站地图
        # 联系我们首页即时新闻能源要闻焦点…"整段当正文切了摘要。
        # 实测 cnenergy 这类记录有 8 条，摘要全是导航文字。
        # 空摘要比导航摘要诚实：空能被下游发现并跳过，导航摘要会被当成内容。
        return {
            "clean_text": body,
            "summary": "",
            "key_points": [],
            "tags": [],
            "cleaned_chars": len(body),
            "compression": 0.0,
            "repairs": ["nav_chrome"],
            "drop_reason": "nav_chrome",
        }
    if not sentences:
        return {
            "clean_text": body,
            "summary": "",
            "key_points": [],
            "tags": [],
            "cleaned_chars": len(body),
            "compression": 0.0,
            "repairs": ["no_usable_sentence"],
            # Why the summary is empty. resummarize.py and the self-improvement
            # loop both read this key, and it was never set, so every empty
            # summary in the store had no recorded cause and the drop looked
            # unexplained. "chrome_only" separates "the cleaner removed
            # everything" from "the body was already empty", which need different
            # fixes.
            "drop_reason": "chrome_only" if body.strip() else "empty_body",
        }

    repairs: list[str] = []
    # Reserve a slot for the lead sentence: gov notices name the subject and the
    # action in the first line, and dropping it loses "what this is about".
    lead = sentences[0]
    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (-_score_with_coverage(sentences[i], i, len(sentences)), i),
    )

    chosen: list[int] = [0]
    covered: set[str] = _signal_kinds(lead)
    # Pass 1: take sentences that add a signal kind the summary lacks. This is what
    # keeps the amount, the deadline and the applicant from crowding each other out.
    #
    # 先按"重要信号类别有没有被覆盖"来选，而不是逐句扫。
    # 逐句扫的缺陷：一句话常同时带多种信号（id=137 的采购预算那句带
    # 金额+采购+资金来源+条目），它被选中后 covered 就很全，后面那些只带
    # "金额"的句子会因为"没有新 kind"被跳过——而全局上金额保留率因此从
    # 60% 掉到 56%（新加的时限规则让更多句子带时限，挤掉了带金额的）。
    # 改成：先算出每个重要类别的最佳句子，按重要度依次补入。
    for kind in _SIGNAL_PRIORITY:
        if kind in covered or len(chosen) >= limit:
            continue
        candidates = [i for i in ranked
                      if i not in chosen and kind in _signal_kinds(sentences[i])]
        if not candidates:
            continue
        pick = candidates[0]
        covered |= _signal_kinds(sentences[pick])
        chosen.append(pick)
    # 再扫一遍收尾：有些句子带的是上面没列到的 kind
    for index in ranked:
        if len(chosen) >= limit:
            break
        if index in chosen:
            continue
        kinds = _signal_kinds(sentences[index])
        if not kinds - covered:
            continue
        covered |= kinds
        chosen.append(index)
    # Pass 2: top up to the limit by score, so a short notice still yields a
    # readable summary even when one sentence already carries every signal.
    for index in ranked:
        if len(chosen) >= limit:
            break
        if index not in chosen:
            chosen.append(index)

    chosen.sort()
    points = [sentences[i] for i in chosen]
    summary = "".join(points)
    if TRAILING_RE.search(summary):
        summary = TRAILING_RE.sub("", summary).strip()
        repairs.append("dropped_contact_tail")

    # Correction 1: compression band.
    #
    # The floor exists so a short notice is not over-compressed. It used to be
    # bounded by neither MAX_POINTS nor MAX_SUMMARY_CHARS — only by `need`
    # (10% of the body) and by running out of sentences. On a 187,122-char body
    # (a listing page stored as an announcement) that padded the summary to
    # ~18,700 chars across 49 key points. 515 of 1576 records (33%) were over
    # the limit for that one reason, and MAX_SUMMARY_CHARS — declared at line 49 —
    # was not referenced anywhere in the module.
    #
    # The floor can also break through MAX_POINTS on purpose: the main passes
    # already fill `chosen` to `limit`, so a floor that respects the point count
    # can never fire at all — it was dead code. Point count is a soft target;
    # MAX_SUMMARY_CHARS is the hard ceiling. Allowing a few extra short points to
    # reach 10% of a normal notice keeps the "don't over-compress" intent without
    # letting a runaway body dictate the length: need is capped at 600 too, and
    # the loop guard stops on the character ceiling regardless.
    body_len = max(1, len(body))
    ratio = len(summary) / body_len
    if ratio < COMPRESS_FLOOR and len(sentences) > len(points):
        need = min(int(body_len * COMPRESS_FLOOR), MAX_SUMMARY_CHARS)
        for index in ranked:
            if (len(summary) >= need or len(chosen) >= len(sentences)
                    or len(summary) >= MAX_SUMMARY_CHARS):
                break
            if index in chosen:
                continue
            summary += sentences[index]
            chosen.append(index)
            points.append(sentences[index])
        chosen.sort()
        points = [sentences[i] for i in chosen]
        summary = "".join(points)
        repairs.append("raised_compression")
    # Correction 1 (over-compression): drop the weakest points, never the last
    # one. Popping from the tail removed the amount and the deadline, which are
    # exactly what a cross-level comparison needs.
    body_len = max(1, len(body))
    ratio = len(summary) / body_len
    if ratio > COMPRESS_CEILING and len(points) > 2:
        ceiling = body_len * COMPRESS_CEILING
        # Amount, deadline, period and applicant sentences are the comparison
        # payload: they survive however long the summary gets.
        protected = {
            i for i in range(len(points))
            if _signal_kinds(points[i]) & {"金额", "时限", "时段", "对象"}
        }
        while len(points) > 2 and len(summary) > ceiling:
            removable = [i for i in range(len(points)) if i != 0 and i not in protected]
            if not removable:
                break
            weakest = min(removable, key=lambda i: score_sentence(points[i], 0, len(points)))
            summary = "".join(points[:weakest] + points[weakest + 1:])
            points.pop(weakest)
        repairs.append("lowered_compression")

    # Correction 1b: the hard cap. The compression bands are relative to the body,
    # so a long body can keep the summary proportionally long — 30% of 5,000 chars
    # is 1,500, well past MAX_SUMMARY_CHARS. A summary is a summary: past the cap it
    # stops being one. Protect the same signal sentences as above so the amount and
    # the deadline survive.
    #
    # 触发条件原本是 `len(points) > 2`，而**恰好 2 条要点时上���形同虚设**——
    # 这正是最常见的超长形态：MAX_SENTENCE 允许单句到 400 字，2 条就是 800。
    # 实测 17 条超长摘要全部是 2 条要点、最长单条 346-400 字。
    # 现在改成长度超限就处理，砍到只剩 1 条为止（1 条时它本身就是被保护的首句）。
    if len(summary) > MAX_SUMMARY_CHARS:
        protected = {
            i for i in range(len(points))
            if i == 0 or _signal_kinds(points[i]) & {"金额", "时限", "时段", "对象"}
        }
        while len(summary) > MAX_SUMMARY_CHARS and len(points) > 1:
            removable = [i for i in range(len(points)) if i not in protected]
            if not removable:
                # 只剩受保护的信号句：砍最长的，但仍保首句（它带标题主体信息）
                removable = [i for i in range(len(points)) if i != 0]
            if not removable:
                # 全是不可砍的信号句：把最长那条按句号切开，保留前半。
                # 这种情况很罕见，但比留一条 800 字的"摘要"合理。
                longest = max(range(len(points)), key=lambda i: len(points[i]))
                head = points[longest][:max(60, int(len(points[longest]) * 0.6))]
                cut = head.rfind("。")
                points[longest] = head[:cut + 1] if cut > 40 else head
                repairs.append("split_longest_point")
                break
            longest = max(removable, key=lambda i: len(points[i]))
            summary = "".join(points[:longest] + points[longest + 1:])
            points.pop(longest)
        if len(summary) > MAX_SUMMARY_CHARS and points:
            summary = summary[:MAX_SUMMARY_CHARS]
            points = [summary]
            repairs.append("hard_truncated")
        repairs.append("capped_summary_chars")

    # Correction 2: a summary that stops mid-clause reads as truncated. Append the
    # stop the source sentence lacked; a roster of names is left as-is because it
    # has no sentence end in the original either.
    stripped = summary.rstrip()
    if stripped and stripped[-1] in "；;，,、":
        summary = stripped[:-1] + "。"
        repairs.append("completed_truncated_tail")
    elif stripped and not stripped.endswith(("。", "！", "？", "：", "）", "】")):
        # Sentence splitting strips the trailing mid-stop, so the summary itself
        # may end bare. If the source stopped mid-clause, it is still truncated.
        if body.rstrip().endswith(("；", ";", "，", ",", "、")):
            summary = stripped + "。"
            repairs.append("completed_truncated_tail")
        else:
            repairs.append("tail_kept_verbatim")

    tags = sorted(covered, key=lambda t: list(dict.fromkeys(l for _, _, l in SIGNAL_PATTERNS)).index(t) if t in [l for _, _, l in SIGNAL_PATTERNS] else 99)
    # 结构化事实行放最前面，并把含同一事实的句子从要点里剔掉——避免
    # "金额：495000元" 后面又跟一句"采购预算 495000 元"这种重复。
    # 事实行本身也计入字数上限：它也是摘要的一部分，超限就整行丢弃。
    if fact_line:
        remaining = []
        for point in points:
            compact = re.sub(r"\s+", "", point)
            if any(re.sub(r"\s+", "", seg.split("：", 1)[-1]) in compact
                   for seg in fact_line.split("｜") if "：" in seg):
                continue
            remaining.append(point)
        if len(fact_line) + sum(len(p) for p in remaining) <= MAX_SUMMARY_CHARS:
            points = [fact_line] + remaining
            summary = "".join(points)
            repairs.append("prepended_facts")
        else:
            repairs.append("facts_dropped_over_budget")
    return {
        "clean_text": body,
        "summary": summary,
        "key_points": points,
        "tags": tags,
        "cleaned_chars": len(body),
        "compression": round(len(summary) / body_len, 4),
        "repairs": repairs,
    }


def audit(rows: list[dict[str, Any]], field: str = "summary") -> dict[str, Any]:
    """Count the defect classes on a set of records. Used to verify a pass."""
    counts = {
        "breadcrumb": 0,
        "toolbar": 0,
        "compression_high": 0,
        "compression_low": 0,
        "no_terminator": 0,
        "dropped_amount": 0,
    }
    measured = 0
    for row in rows:
        text = str(row.get(field) or "")
        body = str(row.get("content") or "")
        if not text:
            continue
        if CRUMB_RE.search(text):
            counts["breadcrumb"] += 1
        if TOOLBAR_RE.search(text):
            counts["toolbar"] += 1
        # Compression is only a defect on a body long enough for a summary to be a
        # summary. A 400-char 公示 with a 200-char abstract has a ratio of 0.5 and
        # is not "没摘要" -- the document is simply short, and blanking the summary
        # would leave the user with nothing. Bodies under the floor are counted and
        # reported instead of being scored.
        if len(body) >= COMPRESSION_MIN_BODY:
            measured += 1
            ratio = len(text) / max(1, len(body))
            if ratio > COMPRESS_CEILING:
                counts["compression_high"] += 1
            # Don't score compression_low when the hard cap makes it impossible.
            # MAX_SUMMARY_CHARS=600 on a 8000-char body gives ratio 0.075 < 0.10,
            # but that's the cap working as designed, not a too-thin summary.
            # Only count it when the cap could in principle reach the floor.
            if ratio < COMPRESS_FLOOR and MAX_SUMMARY_CHARS >= COMPRESS_FLOOR * len(body):
                counts["compression_low"] += 1
        if not text.rstrip().endswith(("。", "！", "？", "：", "）", "】")):
            # A roster of names legitimately ends without punctuation, so only
            # count a real mid-sentence stop.
            if text.rstrip().endswith(("；", ";", "，", ",", "、")):
                counts["no_terminator"] += 1
        if re.search(r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿)", body) and not re.search(
            r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿)", text
        ):
            counts["dropped_amount"] += 1
    # A recommended-article block carries figures that are not the notice's own
    # ("新华鲜报丨年内发行超2400亿元"). Leaving them out of the summary is right,
    # so measure only amounts in the body proper.
    real_amount = 0
    kept_amount = 0
    for row in rows:
        body = re.split(r"阅读下一篇|相关阅读|推荐阅读|相关推荐", str(row.get("content") or ""))[0]
        text = str(row.get(field) or "")
        if re.search(r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿)", body):
            real_amount += 1
            if not re.search(r"\d+(?:\.\d+)?\s*(?:万元|亿元|万|亿)", text):
                kept_amount += 1
    counts["dropped_amount"] = kept_amount
    # The breadcrumb/toolbar/no-terminator rates stay over every summarised row;
    # the two compression rates are over the rows long enough to score.
    total = max(1, len(rows))
    scored = max(1, measured)
    rates = {k: round(v / total, 4) for k, v in counts.items()}
    rates["compression_high"] = round(counts["compression_high"] / scored, 4)
    rates["compression_low"] = round(counts["compression_low"] / scored, 4)
    return {
        "total": len(rows),
        "rates": rates,
        "counts": counts,
        "amount_bearing_articles": real_amount,
        "compression_scored": measured,
        "compression_skipped_short_body": max(0, len(rows) - measured),
    }
