from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_db
from graph_service import graph_neighbors, graph_search, graph_summary
from storage.db import Database

router = APIRouter()


@router.get("/summary")
def summary(db: Database = Depends(get_db)):
    return {"code": 0, "data": graph_summary(db)}


@router.get("/nodes")
def search_nodes(keyword: str = "", limit: int = 20, db: Database = Depends(get_db)):
    return {"code": 0, "data": {"nodes": graph_search(db, keyword, limit)}}


@router.get("/nodes/{node_id:path}")
def node_detail(node_id: str, db: Database = Depends(get_db)):
    data = graph_neighbors(db, node_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "graph node not found"})
    return {"code": 0, "data": data}
