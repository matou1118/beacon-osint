"""Seed comparison topics, backfill topic-doc links, extract schema fields.

Usage:
    python seed_compare.py                 # seed + backfill + extract (idempotent)
    python seed_compare.py --backfill      # only re-score links
    python seed_compare.py --extract       # only run field extraction
    python seed_compare.py --db other.db

Deterministic pipeline: keyword scoring decides links (>=90 auto, 60-90 pending,
<60 dropped), then rule-based extraction fills schema fields for linked docs.
"""
from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy import select

from policy_fields import extract_fields, key_measures_from
from policy_relations import link_status, score_link
from storage.db import Database
from storage.models import Announcement, FieldExtraction, PolicyTopic, TopicDocLink

SEED_TOPICS: list[dict[str, Any]] = [
    {
        "name": "灵活就业人员社保补贴",
        "category": "民政保障",
        "keywords": ["灵活就业", "社保补贴", "社会保险补贴", "灵活就业人员", "就业困难人员", "4050"],
        "field_schema": [
            {"field_key": "subsidy_amount", "label": "补贴标准"},
            {"field_key": "target_group", "label": "申报条件"},
            {"field_key": "deadline", "label": "申报截止"},
            {"field_key": "institution", "label": "责任机构"},
            {"field_key": "region", "label": "所属地区"},
            {"field_key": "key_measures", "label": "关键举措"},
        ],
    },
]


def seed_topics(db: Database) -> dict[str, int]:
    inserted = 0
    with db.session() as session:
        for spec in SEED_TOPICS:
            existing = session.scalar(select(PolicyTopic).where(PolicyTopic.name == spec["name"]))
            if existing is not None:
                continue
            session.add(
                PolicyTopic(
                    name=spec["name"],
                    category=spec.get("category"),
                    keywords=list(spec.get("keywords") or []),
                    field_schema=list(spec.get("field_schema") or []),
                    status="active",
                )
            )
            inserted += 1
    return {"inserted": inserted, "total": len(SEED_TOPICS)}


def backfill_links(db: Database) -> dict[str, int]:
    counts = {"auto": 0, "pending": 0, "dropped": 0, "kept": 0}
    with db.session() as session:
        topics = list(session.scalars(select(PolicyTopic).where(PolicyTopic.status == "active")).all())
        announcements = list(session.scalars(select(Announcement)).all())
        for topic in topics:
            topic_dict = {
                "name": topic.name,
                "category": topic.category,
                "keywords": list(topic.keywords or []),
            }
            for ann in announcements:
                record = {
                    "title": ann.title,
                    "content": ann.content,
                    "category_l1": ann.category_l1,
                    "gov_level": ann.gov_level,
                }
                confidence, evidence = score_link(record, topic_dict)
                status = link_status(confidence, len(evidence.get("body_hits") or []))
                if status is None:
                    counts["dropped"] += 1
                    continue
                link = session.scalar(
                    select(TopicDocLink).where(
                        TopicDocLink.topic_id == topic.id,
                        TopicDocLink.announcement_id == ann.id,
                    )
                )
                if link is None:
                    link = TopicDocLink(
                        topic_id=topic.id,
                        announcement_id=ann.id,
                        gov_level=ann.gov_level,
                        confidence=confidence,
                        status=status,
                        evidence=evidence,
                    )
                    session.add(link)
                    counts[status] += 1
                else:
                    # 已有人工结论（approved/rejected）不回退；仅刷新 auto/pending 的分数。
                    if link.status not in ("approved", "rejected"):
                        link.confidence = confidence
                        link.status = status
                        link.evidence = evidence
                    counts["kept"] += 1
    return counts


def extract_one(db: Database, announcement_id: int, topic_id: int) -> int:
    """Run rule extraction for one (announcement, topic) pair; returns fields written."""
    written = 0
    with db.session() as session:
        topic = session.get(PolicyTopic, topic_id)
        ann = session.get(Announcement, announcement_id)
        if topic is None or ann is None:
            return 0
        schema = list(topic.field_schema or [])
        record = {
            "content": ann.content,
            "amount": ann.amount,
            "target_group": ann.target_group,
            "deadline": ann.deadline,
            "institution_name": ann.institution_name,
            "region_province": ann.region_province,
            "key_points": ann.key_points,
        }
        results = extract_fields(record, schema)
        if any(spec.get("field_key") == "key_measures" for spec in schema):
            results = [r for r in results if r["field_key"] != "key_measures"]
            results.append(key_measures_from(record))
        for item in results:
            existing = session.scalar(
                select(FieldExtraction).where(
                    FieldExtraction.announcement_id == announcement_id,
                    FieldExtraction.topic_id == topic_id,
                    FieldExtraction.field_key == item["field_key"],
                )
            )
            if existing is not None:
                continue
            session.add(
                FieldExtraction(
                    announcement_id=announcement_id,
                    topic_id=topic_id,
                    field_key=item["field_key"],
                    value=item.get("value"),
                    confidence=int(item.get("confidence") or 0),
                    evidence_text=item.get("evidence_text"),
                    method=item.get("method", "rule"),
                    status=item.get("status", "pending"),
                )
            )
            written += 1
    return written


def extract_for_links(db: Database) -> dict[str, int]:
    counts = {"extracted": 0, "skipped": 0}
    with db.session() as session:
        links = list(session.scalars(select(TopicDocLink).where(TopicDocLink.status.in_(("auto", "approved")))).all())
    for link in links:
        written = extract_one(db, link.announcement_id, link.topic_id)
        counts["extracted"] += written
        if written == 0:
            counts["skipped"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed topics / backfill links / extract fields")
    parser.add_argument("--db", default="data/beacon.db", help="database path")
    parser.add_argument("--seed", action="store_true", help="insert seed topics")
    parser.add_argument("--backfill", action="store_true", help="score and upsert topic-doc links")
    parser.add_argument("--extract", action="store_true", help="run rule-based field extraction")
    parser.add_argument("--all", action="store_true", help="run every stage (default)")
    args = parser.parse_args()

    run_all = args.all or not (args.seed or args.backfill or args.extract)
    db = Database(args.db)
    try:
        if run_all or args.seed:
            print("seed:", seed_topics(db))
        if run_all or args.backfill:
            print("backfill:", backfill_links(db))
        if run_all or args.extract:
            print("extract:", extract_for_links(db))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
