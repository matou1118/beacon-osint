from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, Request

from api.dependencies import get_db
from api.services.announcement_service import query_regions
from storage.db import Database

router = APIRouter()


@router.get("/categories")
def list_categories(request: Request):
    path = Path(request.app.state.categories_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    l1 = data.get("l1") or {}
    return {
        "code": 0,
        "data": {
            "l1": list(l1.keys()),
            "elderly_sub": list((l1.get("养老与社区") or {}).get("subcategories") or []),
            "admission_sub": list((l1.get("招生信息") or {}).get("subcategories") or []),
        },
    }


@router.get("/regions")
def list_regions(db: Database = Depends(get_db)):
    return {"code": 0, "data": query_regions(db)}
