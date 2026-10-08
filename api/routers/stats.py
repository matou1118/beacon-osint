from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_db
from api.services.stats_service import query_stats
from storage.db import Database

router = APIRouter()


@router.get("")
def stats_overview(db: Database = Depends(get_db)):
    return {"code": 0, "data": query_stats(db)}
