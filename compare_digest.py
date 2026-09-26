"""Daily compare digest — notify topic subscribers about new links/field changes.

Runs as an APScheduler job once per day. For every active topic it re-scores
links and re-runs extraction (both idempotent), then diffs against a watermark
(the previous digest notice for that topic) and writes UserNotice rows for
matching subscriptions. Email/push stay out of scope for phase 1.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from policy_fields import FIELD_SCHEMA
from seed_compare import backfill_links, extract_for_links
from storage.db import Database
from storage.models import Announcement, FieldExtraction, PolicyTopic, TopicDocLink, TopicSubscription, UserNotice, utcnow

DIGEST_TYPE = "digest"
NOTICE_TYPES = ("new_links", "field_changes")


def list_subscriptions(db_path: str, user_id: str = "local") -> list[dict[str, Any]]:
    db = Database(db_path)
    try:
        with db.session() as session:
            rows = list(
                session.execute(
                    select(TopicSubscription, PolicyTopic.name)
                    .join(PolicyTopic, TopicSubscription.topic_id == PolicyTopic.id)
                    .where(TopicSubscription.user_id == user_id)
                    .order_by(TopicSubscription.id)
                ).all()
            )
            return [
                {
                    "id": sub.id,
                    "topic_id": sub.topic_id,
                    "topic_name": name,
                    "regions": list(sub.regions or []),
                    "notice_types": list(sub.notice_types or []),
                    "enabled": bool(sub.enabled),
                }
                for sub, name in rows
            ]
    finally:
        db.close()


def save_subscription(
    db_path: str,
    topic_id: int,
    regions: list[str] | None = None,
    notice_types: list[str] | None = None,
    user_id: str = "local",
) -> int:
    """Upsert one subscription; returns the subscription id."""
    db = Database(db_path)
    try:
        with db.session() as session:
            sub = session.scalar(
                select(TopicSubscription).where(
                    TopicSubscription.user_id == user_id,
                    TopicSubscription.topic_id == topic_id,
                )
            )
            if sub is None:
                sub = TopicSubscription(user_id=user_id, topic_id=topic_id)
                session.add(sub)
            sub.regions = regions or []
            sub.notice_types = notice_types or list(NOTICE_TYPES)
            sub.enabled = True
            session.flush()
            return int(sub.id)
    finally:
        db.close()


def remove_subscription(db_path: str, subscription_id: int) -> bool:
    db = Database(db_path)
    try:
        with db.session() as session:
            sub = session.get(TopicSubscription, subscription_id)
            if sub is None:
                return False
            session.delete(sub)
            return True
    finally:
        db.close()


def set_subscription_enabled(db_path: str, subscription_id: int, enabled: bool) -> bool:
    db = Database(db_path)
    try:
        with db.session() as session:
            sub = session.get(TopicSubscription, subscription_id)
            if sub is None:
                return False
            sub.enabled = enabled
            return True
    finally:
        db.close()


def list_notices(db_path: str, user_id: str = "local", unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    db = Database(db_path)
    try:
        with db.session() as session:
            query = (
                select(UserNotice, PolicyTopic.name)
                .outerjoin(PolicyTopic, UserNotice.topic_id == PolicyTopic.id)
                .where(UserNotice.user_id == user_id)
                .order_by(UserNotice.created_at.desc())
                .limit(limit)
            )
            if unread_only:
                query = query.where(UserNotice.read.is_(False))
            rows = list(session.execute(query).all())
            return [
                {
                    "id": notice.id,
                    "topic_name": name or "",
                    "notice_type": notice.notice_type,
                    "message": notice.message,
                    "read": bool(notice.read),
                    "created_at": notice.created_at.isoformat() if notice.created_at else "",
                }
                for notice, name in rows
            ]
    finally:
        db.close()


def unread_count(db_path: str, user_id: str = "local") -> int:
    db = Database(db_path)
    try:
        with db.session() as session:
            return _count_unread(session, user_id)
    finally:
        db.close()


def _count_unread(session: Any, user_id: str) -> int:
    from sqlalchemy import func

    return int(session.scalar(select(func.count()).select_from(UserNotice).where(UserNotice.user_id == user_id, UserNotice.read.is_(False))) or 0)


def mark_notice_read(db_path: str, notice_id: int) -> bool:
    db = Database(db_path)
    try:
        with db.session() as session:
            notice = session.get(UserNotice, notice_id)
            if notice is None:
                return False
            notice.read = True
            return True
    finally:
        db.close()


def mark_all_read(db_path: str, user_id: str = "local") -> int:
    db = Database(db_path)
    try:
        with db.session() as session:
            notices = list(session.scalars(select(UserNotice).where(UserNotice.user_id == user_id, UserNotice.read.is_(False))).all())
            for notice in notices:
                notice.read = True
            return len(notices)
    finally:
        db.close()


def _region_match(sub_regions: list[str], seen_regions: set[str]) -> bool:
    if not sub_regions:
        return True
    return bool(seen_regions.intersection(sub_regions))


def _notify(db_path: str, topic_id: int | None, notice_type: str, message: str, user_ids: list[str]) -> int:
    db = Database(db_path)
    try:
        with db.session() as session:
            for user_id in user_ids:
                session.add(UserNotice(user_id=user_id, topic_id=topic_id, notice_type=notice_type, message=message))
            return len(user_ids)
    finally:
        db.close()


def run_daily_digest(db_path: str, user_id: str = "local") -> dict[str, Any]:
    """Refresh links/fields, then write one digest notice per topic with changes."""
    db = Database(db_path)
    backfill = backfill_links(db)
    extracted = extract_for_links(db)

    summary: dict[str, Any] = {"backfill": backfill, "extract": extracted, "notified": 0, "topics": []}
    try:
        with db.session() as session:
            topics = list(session.scalars(select(PolicyTopic).where(PolicyTopic.status == "active")).all())
            for topic in topics:
                last_digest = session.scalar(
                    select(UserNotice.created_at)
                    .where(UserNotice.topic_id == topic.id, UserNotice.notice_type == DIGEST_TYPE)
                    .order_by(UserNotice.created_at.desc())
                    .limit(1)
                )
                if last_digest is None:
                    watermark = utcnow() - timedelta(days=1)
                else:
                    watermark = last_digest if last_digest.tzinfo else last_digest.replace(tzinfo=timezone.utc)

                links = list(
                    session.execute(
                        select(TopicDocLink, Announcement.region_province)
                        .join(Announcement, TopicDocLink.announcement_id == Announcement.id)
                        .where(
                            TopicDocLink.topic_id == topic.id,
                            TopicDocLink.status.in_(("auto", "approved")),
                            TopicDocLink.updated_at > watermark,
                        )
                    ).all()
                )
                fields = list(
                    session.scalars(
                        select(FieldExtraction).where(
                            FieldExtraction.topic_id == topic.id,
                            FieldExtraction.updated_at > watermark,
                        )
                    ).all()
                )
                if not links and not fields:
                    continue

                seen_regions = {region for _, region in links if region}
                subs = list(
                    session.scalars(
                        select(TopicSubscription).where(
                            TopicSubscription.topic_id == topic.id, TopicSubscription.enabled.is_(True)
                        )
                    ).all()
                )
                matched: list[str] = []
                for sub in subs:
                    kinds = set(sub.notice_types or NOTICE_TYPES)
                    has_link_change = bool(links) and "new_links" in kinds
                    has_field_change = bool(fields) and "field_changes" in kinds
                    if not (has_link_change or has_field_change):
                        continue
                    if not _region_match(list(sub.regions or []), seen_regions):
                        continue
                    matched.append(sub.user_id)

                parts: list[str] = []
                if links:
                    parts.append(f"新增/更新关联 {len(links)} 篇（{'、'.join(sorted(seen_regions)) or '地区未知'}）")
                if fields:
                    parts.append(f"字段更新 {len(fields)} 项")
                message = f"【{topic.name}】" + "；".join(parts)
                if matched:
                    notified = _notify(db_path, topic.id, DIGEST_TYPE, message, matched)
                    summary["notified"] += notified
                summary["topics"].append({"topic": topic.name, "links": len(links), "fields": len(fields), "notified": len(matched)})
    finally:
        db.close()
    return summary


def field_label(key: str) -> str:
    return {spec["field_key"]: spec["label"] for spec in FIELD_SCHEMA}.get(key, key)
