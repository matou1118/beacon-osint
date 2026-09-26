from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import or_, select

from storage.db import Database
from storage.models import Announcement, UserSubscription
from privacy import redact_sensitive

from .match_service import MatchService


def _values(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _record(row: Announcement) -> dict[str, Any]:
    extra = dict(row.extra or {})
    return {
        "id": str(row.id),
        "title": row.title,
        "issuing_body": extra.get("issuing_body") or row.source_id,
        "gov_level": row.gov_level,
        "region": row.region_county or row.region_city or row.region_province,
        "region_province": row.region_province,
        "region_city": row.region_city,
        "region_county": row.region_county,
        "category_l1": row.category_l1,
        "category_l2": row.category_l2,
        "target_group": row.target_group,
        "target_industry": row.target_industry,
        "amount": row.amount,
        "deadline": row.deadline,
        "key_points": [redact_sensitive(item) for item in (row.key_points or [])],
        "source_url": row.source_url,
        "publish_date": row.publish_date,
        "media_type": row.media_type,
        "media_tags": list(row.media_tags or []),
        "media_cluster_id": row.media_cluster_id,
        "media_heat": row.media_heat,
        "corroboration_status": row.corroboration_status,
        "corroboration_count": row.corroboration_count,
        "corroboration_reason": row.corroboration_reason,
        "corroborating_sources": list(row.corroborating_sources or []),
    }


def _subscription_profile(row: UserSubscription) -> dict[str, Any]:
    return {
        "levels": list(row.gov_levels or []),
        "region": row.region_county or row.region_city or row.region_province,
        "categories": list(row.categories or []),
        "target_group": row.target_group,
        "target_industry": row.target_industry,
        "keywords": list(row.keywords or []),
        "exclude_review": True,
    }


def query_announcements(
    db: Database,
    match_service: MatchService,
    user_id: str | None,
    gov_level: str | None,
    region: str | None,
    category: str | None,
    target_group: str | None,
    target_industry: str | None,
    keywords: list[str] | None,
    subscription_id: int | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    levels = _values(gov_level)
    categories = _values(category)
    with db.session() as session:
        subscription = None
        if subscription_id is not None:
            if not user_id:
                raise PermissionError("subscription_id requires authentication")
            subscription = session.scalar(select(UserSubscription).where(UserSubscription.id == subscription_id, UserSubscription.user_id == user_id))
            if subscription is None:
                raise LookupError("subscription not found")
        conditions = [Announcement.needs_review.is_(False), Announcement.archived_at.is_(None)]
        if subscription is not None:
            levels = list(subscription.gov_levels or [])
            categories = list(subscription.categories or [])
            region = subscription.region_county or subscription.region_city or subscription.region_province
        if levels:
            conditions.append(Announcement.gov_level.in_(levels))
        if categories:
            conditions.append(Announcement.category_l1.in_(categories))
        if region:
            pattern = f"%{region}%"
            conditions.append(or_(Announcement.region_province.ilike(pattern), Announcement.region_city.ilike(pattern), Announcement.region_county.ilike(pattern)))
        rows = list(session.scalars(select(Announcement).where(*conditions).order_by(Announcement.publish_date.desc(), Announcement.id.desc())).all())
        records = [_record(row) for row in rows]
    profile = _subscription_profile(subscription) if subscription is not None else {
        "levels": levels,
        "region": region,
        "categories": categories,
        "target_group": target_group,
        "target_industry": target_industry,
        "keywords": keywords or [],
        "exclude_review": True,
    }
    matched = [match_service.match(record, profile) for record in records]
    all_items = [item for item in matched if item is not None]
    start = (page - 1) * page_size
    items = all_items[start : start + page_size]
    return {"total": len(all_items), "page": page, "page_size": page_size, "items": items}


def query_announcement_detail(db: Database, announcement_id: str, include_review: bool = False) -> dict[str, Any]:
    try:
        identifier = int(announcement_id)
    except ValueError as exc:
        raise LookupError("announcement not found") from exc
    with db.session() as session:
        row = session.get(Announcement, identifier)
        if row is None or (row.needs_review and not include_review) or row.archived_at is not None:
            raise LookupError("announcement not found")
        result = _record(row)
        result["raw_text"] = redact_sensitive(row.content)
        result["content_hash"] = row.content_hash
        result["needs_review"] = bool(row.needs_review)
        return result


def query_regions(db: Database) -> dict[str, Any]:
    with db.session() as session:
        provinces = set(session.scalars(select(Announcement.region_province).where(Announcement.region_province.is_not(None), Announcement.region_province != "")).all())
        cities: dict[str, set[str]] = {}
        counties: dict[str, set[str]] = {}
        for province, city, county in session.execute(select(Announcement.region_province, Announcement.region_city, Announcement.region_county)).all():
            if province:
                cities.setdefault(str(province), set())
                if city:
                    cities[str(province)].add(str(city))
            if city:
                counties.setdefault(str(city), set())
                if county:
                    counties[str(city)].add(str(county))
    return {
        "provinces": sorted(provinces),
        "cities": {key: sorted(value) for key, value in sorted(cities.items())},
        "counties": {key: sorted(value) for key, value in sorted(counties.items())},
    }
