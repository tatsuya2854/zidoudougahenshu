from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..db import get_session
from ..models import Candidate, Export, Job, Video
from ..services.jobs import create_job, submit
from ..services.pipeline import record_decision, run_export

router = APIRouter(tags=["exports"])


class ExportIn(BaseModel):
    candidate_ids: list[str]
    options: dict[str, Any] = Field(default_factory=dict)  # reframe_style: face_track|center|blur_fit, captions: bool


@router.post("/videos/{video_id}/exports", status_code=202)
def create_exports(video_id: str, body: ExportIn, s: Session = Depends(get_session)) -> dict:
    v = s.get(Video, video_id)
    if not v:
        raise HTTPException(404)
    if not body.candidate_ids:
        raise HTTPException(400, "候補を1つ以上選んでください")
    running = s.exec(select(Job).where(Job.video_id == video_id, Job.status.in_(["queued", "running"]))).first()  # type: ignore[attr-defined]
    if running:
        raise HTTPException(409, "この動画のジョブが実行中です")
    exports: list[Export] = []
    for cid in body.candidate_ids:
        c = s.get(Candidate, cid)
        if not c or c.video_id != video_id:
            raise HTTPException(404, f"candidate {cid} not found")
        exports.append(Export(candidate_id=cid, video_id=video_id, creator_id=v.creator_id))
    s.add_all(exports)
    s.commit()
    for e in exports:
        s.refresh(e)
    # 書き出し＝人間が採用した、という判断を HumanEdit に残す（Phase5 の学習材料）
    for cid in body.candidate_ids:
        c = s.get(Candidate, cid)
        if c and c.decision != "accepted":
            record_decision(cid, "accepted")
    job = create_job("export", video_id=video_id, creator_id=v.creator_id, payload={"export_ids": [e.id for e in exports], "options": body.options})
    for e in exports:
        e.job_id = job.id
        s.add(e)
    s.commit()
    submit(job, run_export(job.id, [e.id for e in exports], body.options))
    return {"job": job.model_dump(), "exports": [e.model_dump() for e in exports]}


@router.get("/videos/{video_id}/exports")
def list_exports(video_id: str, s: Session = Depends(get_session)) -> list[dict]:
    rows = s.exec(select(Export).where(Export.video_id == video_id).order_by(Export.created_at.desc())).all()  # type: ignore[attr-defined]
    out = []
    for e in rows:
        d = e.model_dump()
        c = s.get(Candidate, e.candidate_id)
        d["candidate_title"] = c.title if c else ""
        d["candidate_rank"] = c.rank if c else 0
        d["filename"] = Path(e.path).name if e.path else None
        out.append(d)
    return out


@router.get("/exports/{export_id}/file")
def export_file(export_id: str, download: bool = False, s: Session = Depends(get_session)) -> FileResponse:
    e = s.get(Export, export_id)
    if not e or not e.path or not Path(e.path).exists():
        raise HTTPException(404)
    kwargs = {"filename": Path(e.path).name} if download else {}
    return FileResponse(e.path, media_type="video/mp4", **kwargs)


@router.delete("/exports/{export_id}", status_code=204)
def delete_export(export_id: str, s: Session = Depends(get_session)) -> None:
    e = s.get(Export, export_id)
    if not e:
        raise HTTPException(404)
    if e.path:
        Path(e.path).unlink(missing_ok=True)
        Path(e.path).with_suffix(".ass").unlink(missing_ok=True)
    s.delete(e)
    s.commit()
