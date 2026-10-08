from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_db
from api.services.ops_service import (
    query_lighthouse_status,
    query_source_doctor,
    query_source_health,
)
from storage.db import Database

router = APIRouter()


# 明细返回全部源 —— 前端要能搜/筛/切"只看异常"，
# 只给异常清单的话过滤和搜索就没得做了。总量在 totals 里。
@router.get("/sources")
def source_health(db: Database = Depends(get_db)):
    return {"code": 0, "data": query_source_health(db)}


@router.get("/lighthouse")
def lighthouse_status():
    return {"code": 0, "data": query_lighthouse_status()}


# doctor 回答"为什么整体停摆"——它是唯一带处置建议（hint）的来源。
@router.get("/doctor")
def source_doctor():
    return {"code": 0, "data": query_source_doctor()}