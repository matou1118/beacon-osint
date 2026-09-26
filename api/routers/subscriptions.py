from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select

from api.dependencies import get_current_user, get_db
from api.schemas import PushPreferencePayload, SubscriptionPayload, SubscriptionUpdate
from storage.db import Database
from storage.models import MatchResult, PushPreference, UserSubscription

router = APIRouter()


def _region(row: UserSubscription) -> str | None:
    return row.region_county or row.region_city or row.region_province


def _serialize(row: UserSubscription, preference: PushPreference | None) -> dict:
    return {
        "id": row.id,
        "gov_levels": list(row.gov_levels or []),
        "region": _region(row),
        "categories": list(row.categories or []),
        "target_group": row.target_group,
        "target_industry": row.target_industry,
        "keywords": list(row.keywords or []),
        "push_frequency": preference.push_frequency if preference else "daily",
        "enabled": bool(row.enabled and (preference.enabled if preference else True)),
        "created_at": row.created_at.date().isoformat() if row.created_at else None,
    }


@router.get("")
def list_subscriptions(user_id: str = Depends(get_current_user), db: Database = Depends(get_db)):
    with db.session() as session:
        rows = list(session.scalars(select(UserSubscription).where(UserSubscription.user_id == user_id).order_by(UserSubscription.created_at.desc())).all())
        ids = [row.id for row in rows]
        preferences = {item.subscription_id: item for item in session.scalars(select(PushPreference).where(PushPreference.subscription_id.in_(ids))).all()} if ids else {}
        data = [_serialize(row, preferences.get(row.id)) for row in rows]
    return {"code": 0, "data": data}


@router.post("")
def create_subscription(payload: SubscriptionPayload, user_id: str = Depends(get_current_user), db: Database = Depends(get_db)):
    if not any((payload.gov_levels, payload.region, payload.categories, payload.target_group, payload.target_industry, payload.keywords)):
        raise HTTPException(status_code=422, detail={"code": 422, "message": "at least one subscription condition is required"})
    with db.session() as session:
        row = UserSubscription(
            user_id=user_id,
            gov_levels=payload.gov_levels,
            region_province=payload.region,
            categories=payload.categories,
            target_group=payload.target_group,
            target_industry=payload.target_industry,
            keywords=payload.keywords,
            enabled=payload.enabled,
        )
        session.add(row)
        session.flush()
        preference = PushPreference(subscription_id=row.id, push_frequency=payload.push_frequency, enabled=payload.enabled)
        session.add(preference)
        session.flush()
        data = _serialize(row, preference)
    return {"code": 0, "data": data}


@router.put("/{subscription_id}")
def update_subscription(subscription_id: int, payload: SubscriptionUpdate, user_id: str = Depends(get_current_user), db: Database = Depends(get_db)):
    with db.session() as session:
        row = session.scalar(select(UserSubscription).where(UserSubscription.id == subscription_id, UserSubscription.user_id == user_id))
        if row is None:
            raise HTTPException(status_code=404, detail={"code": 404, "message": "subscription not found"})
        values = payload.model_dump(exclude_unset=True)
        for field in ("gov_levels", "categories", "target_group", "target_industry", "keywords", "enabled"):
            if field in values:
                setattr(row, field, values[field])
        if "region" in values:
            row.region_province = values["region"]
            row.region_city = None
            row.region_county = None
        preference = session.scalar(select(PushPreference).where(PushPreference.subscription_id == row.id))
        if preference is None:
            preference = PushPreference(subscription_id=row.id)
            session.add(preference)
        if "push_frequency" in values and values["push_frequency"] is not None:
            preference.push_frequency = values["push_frequency"]
        if "enabled" in values:
            preference.enabled = bool(values["enabled"])
        session.flush()
        data = _serialize(row, preference)
    return {"code": 0, "data": data}


@router.delete("/{subscription_id}")
def delete_subscription(subscription_id: int, user_id: str = Depends(get_current_user), db: Database = Depends(get_db)):
    with db.session() as session:
        row = session.scalar(select(UserSubscription).where(UserSubscription.id == subscription_id, UserSubscription.user_id == user_id))
        if row is None:
            raise HTTPException(status_code=404, detail={"code": 404, "message": "subscription not found"})
        session.execute(delete(MatchResult).where(MatchResult.subscription_id == row.id))
        preference = session.scalar(select(PushPreference).where(PushPreference.subscription_id == row.id))
        if preference is not None:
            session.delete(preference)
        session.delete(row)
    return {"code": 0, "data": {"deleted": True}}


@router.put("/{subscription_id}/push")
def update_push_preference(subscription_id: int, payload: PushPreferencePayload, user_id: str = Depends(get_current_user), db: Database = Depends(get_db)):
    with db.session() as session:
        row = session.scalar(select(UserSubscription).where(UserSubscription.id == subscription_id, UserSubscription.user_id == user_id))
        if row is None:
            raise HTTPException(status_code=404, detail={"code": 404, "message": "subscription not found"})
        preference = session.scalar(select(PushPreference).where(PushPreference.subscription_id == row.id))
        if preference is None:
            preference = PushPreference(subscription_id=row.id)
            session.add(preference)
        preference.push_frequency = payload.push_frequency
        preference.enabled = payload.enabled
        row.enabled = payload.enabled
        session.flush()
        data = _serialize(row, preference)
    return {"code": 0, "data": data}
