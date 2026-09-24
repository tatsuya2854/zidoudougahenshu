from __future__ import annotations

import re
import shutil
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from ..config import get_settings
from ..db import get_session
from ..models import Candidate, Creator, Export, Job, Transcript, Video
from ..providers.registry import get_video_provider
from ..providers.transcription.srt_import import PROVIDER_NAME as SRT_PROVIDER, parse_srt
from ..services import cost
from ..services.jobs import create_job, submit
from ..services.pipeline import run_analyze

router = APIRouter(tags=["videos"])
ALLOWED = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mts"}
SRT_MAX_BYTES = 5 * 1024 * 1024


def _running_job(s: Session, video_id: str) -> Job | None:
    return s.exec(select(Job).where(Job.video_id == video_id, Job.status.in_(["queued", "running"]))).first()  # type: ignore[attr-defined]


@router.post("/creators/{creator_id}/videos", status_code=201)
async def upload_video(creator_id: str, file: UploadFile = File(...), s: Session = Depends(get_session)) -> dict:
    creator = s.get(Creator, creator_id)
    if not creator:
        raise HTTPException(404, "creator not found")
    name = file.filename or "video.mp4"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"対応していない形式: {ext}")
    settings = get_settings()
    v = Video(creator_id=creator_id, title=Path(name).stem, original_filename=name, path="")
    dest_dir = settings.videos_dir / creator_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-　 ]", "_", name)[-80:]
    dest = dest_dir / f"{v.id}_{safe}"
    size = 0
    async with aiofiles.open(dest, "wb") as out:
        while chunk := await file.read(8 * 1024 * 1024):
            size += len(chunk)
            await out.write(chunk)
    try:
        info = get_video_provider().probe(dest)
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"動画として読めませんでした: {e}") from e
    v.path = str(dest)
    v.duration_sec, v.width, v.height, v.fps, v.size_bytes = info.duration, info.width, info.height, info.fps, size
    s.add(v)
    s.commit()
    s.refresh(v)
    cost.record_storage(s, size_bytes=size, creator_id=creator_id, video_id=v.id)
    return v.model_dump()


@router.get("/videos")
def list_videos(creator_id: str | None = None, s: Session = Depends(get_session)) -> list[dict]:
    q = select(Video).order_by(Video.created_at.desc())  # type: ignore[attr-defined]
    if creator_id:
        q = q.where(Video.creator_id == creator_id)
    out = []
    for v in s.exec(q).all():
        d = v.model_dump()
        d["candidate_count"] = len(s.exec(select(Candidate.id).where(Candidate.video_id == v.id)).all())
        d["export_count"] = len(s.exec(select(Export.id).where(Export.video_id == v.id, Export.status == "done")).all())
        out.append(d)
    return out


@router.get("/videos/{video_id}")
def get_video(video_id: str, s: Session = Depends(get_session)) -> dict:
    v = s.get(Video, video_id)
    if not v:
        raise HTTPException(404)
    d = v.model_dump()
    d["latest_job"] = None
    job = s.exec(select(Job).where(Job.video_id == video_id, Job.kind == "analyze").order_by(Job.created_at.desc())).first()  # type: ignore[attr-defined]
    if job:
        d["latest_job"] = job.model_dump()
    return d


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: str, s: Session = Depends(get_session)) -> None:
    v = s.get(Video, video_id)
    if not v:
        raise HTTPException(404)
    settings = get_settings()
    for e in s.exec(select(Export).where(Export.video_id == video_id)).all():
        s.delete(e)
    for c in s.exec(select(Candidate).where(Candidate.video_id == video_id)).all():
        s.delete(c)
    for t in s.exec(select(Transcript).where(Transcript.video_id == video_id)).all():
        s.delete(t)
    s.delete(v)
    s.commit()
    Path(v.path).unlink(missing_ok=True)
    shutil.rmtree(settings.work_dir / video_id, ignore_errors=True)
    shutil.rmtree(settings.exports_dir / v.creator_id / video_id, ignore_errors=True)


@router.get("/videos/{video_id}/file")
def video_file(video_id: str, s: Session = Depends(get_session)) -> FileResponse:
    v = s.get(Video, video_id)
    if not v or not Path(v.path).exists():
        raise HTTPException(404)
    return FileResponse(v.path, media_type="video/mp4", filename=v.original_filename)


@router.post("/videos/{video_id}/analyze", status_code=202)
def analyze(video_id: str, n: int = 10, reuse_transcript: bool = False, s: Session = Depends(get_session)) -> dict:
    """reuse_transcript=true: 保存済みの文字起こし（SRT 読込含む）を使い、候補選定だけやり直す。"""
    v = s.get(Video, video_id)
    if not v:
        raise HTTPException(404)
    if _running_job(s, video_id):
        raise HTTPException(409, "この動画のジョブが実行中です")
    job = create_job("analyze", video_id=video_id, creator_id=v.creator_id, payload={"n": n, "reuse_transcript": reuse_transcript})
    submit(job, run_analyze(job.id, video_id, n_candidates=max(10, n), reuse_transcript=reuse_transcript))
    return job.model_dump()


@router.post("/videos/{video_id}/transcript/srt")
async def import_transcript_srt(video_id: str, file: UploadFile = File(...), s: Session = Depends(get_session)) -> dict:
    """SRT 字幕を文字起こしとして登録（既存の Transcript は置換）。続けて analyze?reuse_transcript=true で候補を出す。"""
    v = s.get(Video, video_id)
    if not v:
        raise HTTPException(404)
    if _running_job(s, video_id):
        raise HTTPException(409, "この動画のジョブが実行中です")
    raw = await file.read(SRT_MAX_BYTES + 1)
    if len(raw) > SRT_MAX_BYTES:
        raise HTTPException(400, "SRT は 5MB 以下にしてください")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(400, "UTF-8 の SRT を選んでください（Shift_JIS は未対応）") from e
    try:
        data = parse_srt(text, duration=v.duration_sec or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    for old in s.exec(select(Transcript).where(Transcript.video_id == video_id)).all():
        s.delete(old)
    s.add(Transcript(video_id=video_id, creator_id=v.creator_id, provider=SRT_PROVIDER, model="",
                     language=data.language, data=data.model_dump()))
    s.commit()
    return {"segments": len(data.segments), "duration": data.duration, "provider": SRT_PROVIDER}


@router.get("/videos/{video_id}/transcript")
def transcript(video_id: str, s: Session = Depends(get_session)) -> dict:
    t = s.exec(select(Transcript).where(Transcript.video_id == video_id)).first()
    if not t:
        raise HTTPException(404, "まだ文字起こしがありません")
    return t.model_dump()


@router.get("/videos/{video_id}/candidates")
def candidates(video_id: str, s: Session = Depends(get_session)) -> list[dict]:
    rows = s.exec(select(Candidate).where(Candidate.video_id == video_id).order_by(Candidate.rank)).all()
    # 再解析で置き換えられた旧候補（書き出し済みのため残している）は一覧に出さない
    return [c.model_dump() for c in rows if not (c.data or {}).get("superseded")]


@router.get("/videos/{video_id}/cost")
def video_cost(video_id: str, s: Session = Depends(get_session)) -> dict:
    return cost.summarize(s, video_id=video_id)
