from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.dependencies import get_db, get_optional_user
from api.services.announcement_service import query_announcement_detail, query_announcements
from storage.db import Database

router = APIRouter()


@router.get("")
def list_announcements(
    request: Request,
    gov_level: str | None = Query(default=None),
    region: str | None = Query(default=None),
    category: str | None = Query(default=None),
    target_group: str | None = Query(default=None),
    target_industry: str | None = Query(default=None),
    keyword: list[str] | None = Query(default=None),
    subscription_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    try:
        data = query_announcements(db, request.app.state.match_service, user_id, gov_level, region, category, target_group, target_industry, keyword, subscription_id, page, page_size)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail={"code": 401, "message": str(exc)}) from exc
    return {"code": 0, "data": data}


@router.get("/{announcement_id}")
def get_announcement(announcement_id: str, db: Database = Depends(get_db)):
    try:
        data = query_announcement_detail(db, announcement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": 404, "message": str(exc)}) from exc
    return {"code": 0, "data": data}
