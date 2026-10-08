from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_db
from storage.db import Database

router = APIRouter()


@router.get("/dates")
def heat_dates(db: Database = Depends(get_db)):
    return {"code": 0, "data": {"dates": db.list_heat_dates()}}


@router.get("/trend")
def heat_trend(db: Database = Depends(get_db)):
    return {"code": 0, "data": {"trend": db.heat_trend()}}


@router.get("/topics")
def list_topics(date: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200), db: Database = Depends(get_db)):
    report_date = date or datetime.now(timezone.utc).date().isoformat()
    return {"code": 0, "data": {"date": report_date, "topics": db.list_heat_topics(report_date, limit)}}


@router.get("/topics/{cluster_id}")
def topic_detail(cluster_id: str, db: Database = Depends(get_db)):
    data = db.get_heat_topic(cluster_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "heat topic not found"})
    return {"code": 0, "data": data}
