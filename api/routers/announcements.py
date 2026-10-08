from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from api.dependencies import get_db, get_optional_user
from api.services.announcement_service import export_announcements_csv, query_announcement_detail, query_announcements, stats_announcements
from storage.db import Database

router = APIRouter()


@router.get("")
def list_announcements(
    request: Request,
    gov_level: str | None = Query(default=None),
    region: str | None = Query(default=None),
    category: str | None = Query(default=None),
    category_l2: str | None = Query(default=None),
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
        data = query_announcements(db, request.app.state.match_service, user_id, gov_level, region, category, category_l2, target_group, target_industry, keyword, subscription_id, page, page_size)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail={"code": 401, "message": str(exc)}) from exc
    return {"code": 0, "data": data}


@router.get("/stats")
def stats_over_announcements(
    request: Request,
    gov_level: str | None = Query(default=None),
    region: str | None = Query(default=None),
    category: str | None = Query(default=None),
    category_l2: str | None = Query(default=None),
    target_group: str | None = Query(default=None),
    target_industry: str | None = Query(default=None),
    keyword: list[str] | None = Query(default=None),
    db: Database = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    # 声明必须在 /{announcement_id} 之前 —— 否则 "stats" 被当成 id 吃掉（int 转换失败直接 422）
    data = stats_announcements(db, user_id, gov_level, region, category, category_l2, target_group, target_industry, keyword)
    return {"code": 0, "data": data}


@router.get("/export")
def export_announcements(
    gov_level: str | None = Query(default=None),
    region: str | None = Query(default=None),
    category: str | None = Query(default=None),
    category_l2: str | None = Query(default=None),
    target_group: str | None = Query(default=None),
    target_industry: str | None = Query(default=None),
    keyword: list[str] | None = Query(default=None),
    db: Database = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    # 同样声明在 /{announcement_id} 之前 —— "export" 会被当成 id 走 404
    # 全量导出（不分页），筛选口径与列表/统计共用 _load_filtered
    payload = export_announcements_csv(db, user_id, gov_level, region, category, category_l2, target_group, target_industry, keyword)
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="beacon-announcements.csv"'},
    )


@router.get("/{announcement_id}")
def get_announcement(announcement_id: str, db: Database = Depends(get_db)):
    try:
        data = query_announcement_detail(db, announcement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": 404, "message": str(exc)}) from exc
    return {"code": 0, "data": data}
