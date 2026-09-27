from __future__ import annotations

import re
from typing import Any, Mapping

from .category_rules import classify
from .summary_refine import build_summary


class RegexFallbackExtractor:
    def extract(self, text: str, source: Mapping[str, Any] | None = None) -> dict[str, Any]:
        value = str(text or "")
        if not value.strip():
            raise ValueError("text must be non-empty")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", value, flags=re.I | re.S)
        title = re.sub(r"<[^>]+>", " ", title_match.group(1)).strip() if title_match else ""
        date_match = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", value)
        publish_date = None
        if date_match:
            publish_date = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        amount_match = re.search(r"(?:金额|补助|资金|预算|投资)[^\d一二三四五六七八九十百千万亿]{0,8}([0-9]+(?:\.[0-9]+)?\s*(?:万元|亿元|元|万|亿)?)", value)
        deadline_match = re.search(r"(?:截止|截至|申报期限|报名时间)[^\d一二三四五六七八九十百千万亿]{0,12}(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2})", value)
        contact_match = re.search(r"(?:联系电话|咨询电话|联系方式|电话)[：:\s]*([0-9\-() ]{7,})", value)
        plain = re.sub(r"<[^>]+>", " ", value)
        plain = re.sub(r"\s+", " ", plain).strip()
        region_match = re.search(r"(北京市|天津市|上海市|重庆市|[\u4e00-\u9fff]{2,8}省|[\u4e00-\u9fff]{2,8}市|[\u4e00-\u9fff]{2,8}县)", plain)
        key_points = [part.strip() for part in re.split(r"[。！？]", plain) if len(part.strip()) >= 8][:3]
        result: dict[str, Any] = {
            "title": title,
            "publish_date": publish_date,
            "amount": amount_match.group(1).strip() if amount_match else None,
            "deadline": deadline_match.group(1).strip() if deadline_match else None,
            "conditions": None,
            "contact": contact_match.group(1).strip() if contact_match else None,
            "region": region_match.group(1) if region_match else None,
            "key_points": key_points,
            "issuing_body": source.get("name") if source else None,
            "category_l1": "其他",
            "admission_sub_category": None,
        }
        if source:
            result["gov_level"] = source.get("gov_level")
            result["jurisdiction_code"] = source.get("jurisdiction_code")
        classified = classify({**result, "source": source})
        result["category_l1"] = classified["category_l1"]
        result["category_l2"] = classified["category_l2"]
        # Scrape -> clean -> summarise. The summary is extractive: real source
        # sentences, never a model rewrite, so it stays testable.
        # Scrape -> clean -> summarise -> self-correct. The refine pass fixes the
        # defects measured on real pages (crumbs, toolbars, compression drift).
        #
        # The text handed to build_summary must be the body that was actually
        # parsed. This dict never carried a "content" key, so reading
        # result.get("content") here always yielded "" and every record came out
        # of the collection path with an empty summary and a
        # no_usable_sentence repair -- 1409 of 1883 rows in the store. Only
        # scripts/resummarize.py, which reads the body back out of the database,
        # ever produced a summary, which is why a hand-run backfill "fixed" it
        # and the next collection round undid it.
        digested = build_summary(plain, title)
        result["content"] = digested["clean_text"]
        result["summary"] = digested["summary"]
        result["key_points"] = digested["key_points"] or key_points
        result["extraction_tags"] = digested["tags"]
        result["summary_repairs"] = digested["repairs"]
        # Carry the reason through so storage can record why a summary is empty.
        if digested.get("drop_reason"):
            result["summary_drop_reason"] = digested["drop_reason"]
        if result["category_l1"] == "招生信息":
            result["admission_sub_category"] = classified["category_l2"]
        else:
            result["admission_sub_category"] = None
            if result["category_l1"] in ("其他", "新闻动态"):
                # Nothing identified it, so keep it only long enough to review.
                # The retention job drops these after 30 days.
                result["retention_class"] = "transient"
        result["needs_review"] = int(not result.get("title") or not result.get("issuing_body") or not result.get("gov_level"))
        return result
