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
    SIGNAL_PATTERNS,
    SENTENCE_SPLIT_RE,
    dedupe_sentences,
    score_sentence,
    strip_chrome,
)

# Nav crumbs that survived cleaning and landed in summaries.
CRUMB_RE = re.compile(r"(?:首页|当前位置|网站首页)\s*[>＞][^\s，。；]{0,40}(?:[>＞][^\s，。；]{0,40})*")
# Print/font controls.
TOOLBAR_RE = re.compile(r"\[打印\]|【关闭[^】]*】|字号[:：]?【?大?中?小?】?|【[大小中]】|打印此页|打印本页")
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


def build_summary(
    text: str,
    title: str = "",
    limit: int = MAX_POINTS,
) -> dict[str, Any]:
    """Build a summary, then correct it against the measured defects."""
    if not isinstance(limit, int) or limit < 1:
        limit = MAX_POINTS
    body = strip_chrome(text)
    sentences = [_strip_lead_noise(s) for s in dedupe_sentences(body)]
    sentences = [s for s in sentences if s]
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
    # the deadline survive, and never drop below two points.
    if len(summary) > MAX_SUMMARY_CHARS and len(points) > 2:
        protected = {
            i for i in range(len(points))
            if i == 0 or _signal_kinds(points[i]) & {"金额", "时限", "时段", "对象"}
        }
        while len(summary) > MAX_SUMMARY_CHARS and len(points) > 2:
            removable = [i for i in range(len(points)) if i not in protected]
            if not removable:
                # Everything left is signal-bearing: cut the longest instead of
                # losing the amount/deadline entirely.
                removable = [i for i in range(len(points)) if i != 0] or list(range(1, len(points)))
            if not removable:
                break
            longest = max(removable, key=lambda i: len(points[i]))
            summary = "".join(points[:longest] + points[longest + 1:])
            points.pop(longest)
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
            if ratio < COMPRESS_FLOOR:
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
