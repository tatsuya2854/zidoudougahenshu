from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models import Job
from ..services import jobs as jobsvc

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(video_id: str | None = None, limit: int = 20, s: Session = Depends(get_session)) -> list[dict]:
    q = select(Job).order_by(Job.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    if video_id:
        q = q.where(Job.video_id == video_id)
    return [j.model_dump() for j in s.exec(q).all()]


@router.get("/{job_id}")
def get_job(job_id: str, s: Session = Depends(get_session)) -> dict:
    j = s.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    return j.model_dump()


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, s: Session = Depends(get_session)) -> dict:
    j = s.get(Job, job_id)
    if not j:
        raise HTTPException(404)
    jobsvc.cancel(job_id)
    return {"ok": True}
