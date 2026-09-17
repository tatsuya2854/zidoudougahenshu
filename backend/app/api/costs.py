from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..db import get_session
from ..models import CostEntry
from ..services import cost

router = APIRouter(prefix="/costs", tags=["costs"])


@router.get("")
def costs(creator_id: str | None = None, video_id: str | None = None, s: Session = Depends(get_session)) -> dict:
    summary = cost.summarize(s, creator_id=creator_id, video_id=video_id)
    q = select(CostEntry).order_by(CostEntry.created_at.desc()).limit(200)  # type: ignore[attr-defined]
    if creator_id:
        q = q.where(CostEntry.creator_id == creator_id)
    if video_id:
        q = q.where(CostEntry.video_id == video_id)
    summary["recent"] = [e.model_dump() for e in s.exec(q).all()]
    return summary
