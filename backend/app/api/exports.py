from __future__ import annotations

import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select
from starlette.background import BackgroundTask

from ..db import get_session
from ..models import Candidate, Creator, Export, Job, Transcript, Video
from ..providers.caption.srt_export import cues_from_ranges, cues_to_srt
from ..schemas.transcript import TranscriptData
from ..services import cost
from ..services.jobs import create_job, submit
from ..services.render import effective_range
from ..services.pipeline import record_decision, run_export
from ..services.render import caption_style_for

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
        d["candidate_title"] = effective_range(c)[2] if c else ""
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


@router.get("/exports/{export_id}/srt")
def export_srt(export_id: str, download: bool = False, s: Session = Depends(get_session)) -> PlainTextResponse:
    """完成品と同じ分割の SRT（クリップ相対）。編集ソフトで字幕を手直しする入口。"""
    e = s.get(Export, export_id)
    if not e or e.status != "done":
        raise HTTPException(404, "完成した書き出しが見つかりません")
    srt = _srt_for_export(s, e)
    headers = {}
    if download:
        # 題名は日本語なので RFC 5987 形式（FileResponse と同じ扱い）。ASCII 名は候補 ID で代替
        headers["Content-Disposition"] = f"attachment; filename=\"{e.candidate_id}.srt\"; filename*=utf-8''{quote(_stem(e))}.srt"
    return PlainTextResponse(srt, media_type="application/x-subrip", headers=headers)


@router.get("/videos/{video_id}/bundle.zip")
def video_bundle(video_id: str, status: str = "done", s: Session = Depends(get_session)) -> FileResponse:
    """その動画の完成品を MP4＋SRT＋編集計画 JSON でまとめて 1 つの ZIP に。"""
    v = s.get(Video, video_id)
    if not v:
        raise HTTPException(404)
    q = select(Export).where(Export.video_id == video_id)
    if status != "all":
        q = q.where(Export.status == status)
    rows = s.exec(q).all()
    return _bundle_response(s, v, rows, f"{_safe(v.title)}_shorts.zip")


@router.get("/exports/{export_id}/bundle.zip")
def export_bundle(export_id: str, s: Session = Depends(get_session)) -> FileResponse:
    """1 本分の ZIP（MP4＋SRT＋編集計画 JSON）。"""
    e = s.get(Export, export_id)
    if not e:
        raise HTTPException(404)
    v = s.get(Video, e.video_id)
    if not v:
        raise HTTPException(404)
    return _bundle_response(s, v, [e], f"{_stem(e)}.zip")


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


# ────────────────────────── ZIP バンドル ──────────────────────────


def _safe(title: str) -> str:
    """pipeline.run_export と同じ規則でファイル名に使える題名にする。"""
    return "".join(ch for ch in (title or "") if ch.isalnum() or ch in "ー_-　 ")[:24].strip() or "short"


def _stem(e: Export) -> str:
    return Path(e.path).stem if e.path else e.id


def _srt_for_export(s: Session, e: Export) -> str:
    """Export.edit_plan から SRT を組む。人間が直した字幕（overrides.captions）があればそれを優先。"""
    plan = e.edit_plan or {}
    overrides = plan.get("overrides") or {}
    caps = overrides.get("captions") if isinstance(overrides.get("captions"), list) else None
    cand = s.get(Candidate, e.candidate_id)
    trow = s.exec(select(Transcript).where(Transcript.video_id == e.video_id)).first()
    if not trow:
        raise HTTPException(404, "文字起こしが見つかりません")
    ranges = plan.get("keep_ranges") or ([[cand.start_sec, cand.end_sec]] if cand else [])
    style = plan.get("caption_style")
    if not style:
        creator = s.get(Creator, e.creator_id)
        style = caption_style_for(creator) if creator else {}
    return cues_to_srt(cues_from_ranges(TranscriptData(**trow.data), ranges, style, overrides=caps))


def _bundle_response(s: Session, v: Video, rows: list[Export], zip_name: str) -> FileResponse:
    ready = [e for e in rows if e.path and Path(e.path).exists()]
    if not ready:
        raise HTTPException(404, "完成した Shorts がありません。先に「AI編集して一括書き出し」を実行してください。")
    tmp = _write_bundle(s, v, ready)
    return FileResponse(tmp, media_type="application/zip", filename=zip_name, background=BackgroundTask(os.unlink, tmp))


def _write_bundle(s: Session, v: Video, exports: list[Export]) -> str:
    """一時ファイルに ZIP を書いてパスを返す（メモリに全部載せない。MP4 は無圧縮で格納）。"""
    creator = s.get(Creator, v.creator_id)
    summary = cost.summarize(s, video_id=v.id)
    fd, tmp = tempfile.mkstemp(prefix="cde_bundle_", suffix=".zip")
    os.close(fd)
    lines = [
        "Creator DNA Editor — 書き出しバンドル",
        "",
        f"動画: {v.title}（{v.original_filename}）",
        f"Creator: {creator.name if creator else v.creator_id}",
        f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"本数: {len(exports)}",
        f"推定原価合計: ${summary['total_usd']:.4f}（内訳は costs.json）",
        "",
        "各フォルダの中身:",
        "  *.mp4             完成した Shorts（1080x1920、字幕焼き込み済み）",
        "  *.srt             同じ字幕の SRT（クリップ相対時刻）。編集ソフトで手直しする用",
        "  *.edit_plan.json  適用した編集計画（採用区間・リフレーム・字幕スタイル）。学習の基準になる",
        "",
    ]
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            for e in sorted(exports, key=lambda x: (_rank(s, x), x.created_at)):
                cand = s.get(Candidate, e.candidate_id)
                rank = cand.rank if cand else 0
                title = effective_range(cand)[2] if cand else ""
                folder = f"{rank:02d}_{_safe(title)}"
                name = _stem(e)
                z.write(e.path, f"{folder}/{name}.mp4", compress_type=zipfile.ZIP_STORED)
                z.writestr(f"{folder}/{name}.srt", _srt_for_export(s, e), compress_type=zipfile.ZIP_DEFLATED)
                z.writestr(f"{folder}/{name}.edit_plan.json", json.dumps(e.edit_plan or {}, ensure_ascii=False, indent=2),
                           compress_type=zipfile.ZIP_DEFLATED)
                if cand:
                    c_start, c_end, _ = effective_range(cand)  # 手直し後の区間
                    lines.append(f"  {folder}/  {title}  ({c_end - c_start:.1f}s, 元動画 {c_start:.1f}-{c_end:.1f}s)")
                else:
                    lines.append(f"  {folder}/")
            z.writestr("README.txt", "\n".join(lines) + "\n", compress_type=zipfile.ZIP_DEFLATED)
            z.writestr("costs.json", json.dumps(summary, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
    except Exception:
        os.unlink(tmp)
        raise
    return tmp


def _rank(s: Session, e: Export) -> int:
    c = s.get(Candidate, e.candidate_id)
    return c.rank if c else 0
