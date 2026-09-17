from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from ..config import get_settings
from ..db import get_session
from ..models import Candidate
from ..services.pipeline import record_decision

router = APIRouter(prefix="/candidates", tags=["candidates"])


class DecisionIn(BaseModel):
    decision: str  # accepted | rejected | pending


@router.post("/{candidate_id}/decision")
def decide(candidate_id: str, body: DecisionIn) -> dict:
    if body.decision not in ("accepted", "rejected", "pending"):
        raise HTTPException(400)
    try:
        record_decision(candidate_id, body.decision)
    except KeyError:
        raise HTTPException(404) from None
    return {"ok": True}


@router.get("/{candidate_id}/thumb")
def thumb(candidate_id: str, s: Session = Depends(get_session)) -> FileResponse:
    c = s.get(Candidate, candidate_id)
    if not c:
        raise HTTPException(404)
    p = get_settings().work_dir / c.video_id / "thumbs" / f"{c.id}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")
