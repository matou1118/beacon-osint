from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.dependencies import get_current_user, get_db
from api.schemas import FeedbackPayload
from storage.db import Database
from storage.models import Announcement, Feedback

router = APIRouter()


@router.post("")
def create_feedback(payload: FeedbackPayload, user_id: str = Depends(get_current_user), db: Database = Depends(get_db)):
    with db.session() as session:
        announcement = session.get(Announcement, payload.announcement_id)
        if announcement is None or announcement.archived_at is not None:
            raise HTTPException(status_code=404, detail={"code": 404, "message": "announcement not found"})
        row = session.scalar(select(Feedback).where(Feedback.user_id == user_id, Feedback.announcement_id == payload.announcement_id))
        if row is None:
            row = Feedback(user_id=user_id, announcement_id=payload.announcement_id, useful=payload.useful, comment=payload.comment)
            session.add(row)
        else:
            row.useful = payload.useful
            row.comment = payload.comment
        session.flush()
        data = {"id": row.id, "announcement_id": row.announcement_id, "useful": row.useful, "comment": row.comment, "updated_at": row.updated_at.isoformat() if row.updated_at else None}
    return {"code": 0, "data": data}
