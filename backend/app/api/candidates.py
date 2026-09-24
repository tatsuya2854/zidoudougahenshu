from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, select

from ..config import get_settings
from ..db import get_session
from ..models import Candidate, Creator, HumanEdit, Transcript, Video, ensure_candidate_overrides_column
from ..providers.registry import get_caption_provider
from ..schemas.transcript import TranscriptData
from ..services.pipeline import record_decision
from ..services.render import caption_style_for, effective_range

router = APIRouter(prefix="/candidates", tags=["candidates"])

# 手直しできる尺の範囲（Shorts の実用域）
MIN_CLIP_SEC = 5.0
MAX_CLIP_SEC = 90.0


@lru_cache
def _ensure_schema() -> None:
    """旧 DB（overrides 列なし）でも動くように、最初のリクエストで一度だけ列を補う。"""
    ensure_candidate_overrides_column()


class DecisionIn(BaseModel):
    decision: str  # accepted | rejected | pending


class CaptionCue(BaseModel):
    """字幕 1 枚。元動画の絶対秒。text が空なら「この字幕は出さない」という人の判断。"""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = ""


class Overrides(BaseModel):
    """Candidate.overrides の正。部分更新のマージ後にこれで検証する。"""

    model_config = ConfigDict(extra="forbid")
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=200)
    crop_mode: Literal["face_track", "center", "blur_fit", "manual"] | None = None
    crop_x: float | None = Field(default=None, ge=0.0, le=1.0)
    font_size_ratio: float | None = Field(default=None, ge=0.015, le=0.12)
    caption_position: Literal["top", "center", "bottom"] | None = None
    captions: list[CaptionCue] | None = None


def _load(s: Session, candidate_id: str) -> tuple[Candidate, Video]:
    _ensure_schema()
    c = s.get(Candidate, candidate_id)
    if not c:
        raise HTTPException(404, "candidate not found")
    v = s.get(Video, c.video_id)
    if not v:
        raise HTTPException(404, "video not found")
    return c, v


def _snapshot(c: Candidate) -> dict[str, Any]:
    """HumanEdit.before 用: 手直し前の overrides と AI 案の元値。"""
    return {"overrides": dict(c.overrides or {}), "start_sec": c.start_sec, "end_sec": c.end_sec, "title": c.title}


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
    c, _ = _load(s, candidate_id)
    p = get_settings().work_dir / c.video_id / "thumbs" / f"{c.id}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@router.put("/{candidate_id}/overrides")
def put_overrides(candidate_id: str, body: dict[str, Any], s: Session = Depends(get_session)) -> dict:
    """人の手直しを部分更新する。値 null でそのキーを外す。before/after を HumanEdit に残す。"""
    c, v = _load(s, candidate_id)
    before = _snapshot(c)
    merged: dict[str, Any] = dict(c.overrides or {})
    for k, val in body.items():
        if val is None:
            merged.pop(k, None)
        else:
            merged[k] = val
    try:
        ov = Overrides(**merged)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        raise HTTPException(400, f"手直しの内容が不正です: {msgs}") from None
    start = ov.start_sec if ov.start_sec is not None else float(c.start_sec)
    end = ov.end_sec if ov.end_sec is not None else float(c.end_sec)
    if end <= start:
        raise HTTPException(400, "終了は開始より後にしてください")
    if v.duration_sec and end > v.duration_sec + 0.05:
        raise HTTPException(400, f"終了が動画の長さ（{v.duration_sec:.1f}秒）を超えています")
    if not (MIN_CLIP_SEC <= end - start <= MAX_CLIP_SEC):
        raise HTTPException(400, f"尺は {MIN_CLIP_SEC:.0f}〜{MAX_CLIP_SEC:.0f} 秒にしてください（今 {end - start:.1f} 秒）")
    if ov.title is not None and not ov.title.strip():
        raise HTTPException(400, "タイトルが空です")
    if ov.captions is not None:
        for cue in ov.captions:
            if cue.end <= cue.start:
                raise HTTPException(400, f"字幕 {cue.start:.2f}s の終了が開始より前です")
    new = ov.model_dump(exclude_none=True)
    if new.get("title") is not None:
        new["title"] = new["title"].strip()
    c.overrides = new
    s.add(c)
    s.add(HumanEdit(creator_id=c.creator_id, video_id=c.video_id, candidate_id=c.id, action="candidate_edited",
                    before=before, after={"overrides": new, "changed_keys": sorted(k for k in body)}))
    s.commit()
    s.refresh(c)
    return c.model_dump()


@router.post("/{candidate_id}/reset")
def reset_overrides(candidate_id: str, s: Session = Depends(get_session)) -> dict:
    """手直しを全部捨てて AI 案に戻す。これも人の判断なので HumanEdit に残す。"""
    c, _ = _load(s, candidate_id)
    before = _snapshot(c)
    c.overrides = {}
    s.add(c)
    s.add(HumanEdit(creator_id=c.creator_id, video_id=c.video_id, candidate_id=c.id, action="candidate_reset",
                    before=before, after={"overrides": {}}))
    s.commit()
    s.refresh(c)
    return c.model_dump()


@router.get("/{candidate_id}/captions")
def captions(candidate_id: str, s: Session = Depends(get_session)) -> dict:
    """この候補区間（overrides 反映後）の字幕 cue を絶対秒で返す。字幕本文編集の初期値。

    overrides.captions があればそれを返す（source=override）。無ければ焼き込みと同じ分割で作る（source=auto）。
    """
    c, v = _load(s, candidate_id)
    start, end, _title = effective_range(c)
    ov = c.overrides or {}
    if isinstance(ov.get("captions"), list):
        return {"source": "override", "start_sec": start, "end_sec": end, "cues": ov["captions"]}
    trow = s.exec(select(Transcript).where(Transcript.video_id == c.video_id)).first()
    if not trow:
        raise HTTPException(404, "まだ文字起こしがありません")
    creator = s.get(Creator, c.creator_id)
    if not creator:
        raise HTTPException(404, "creator not found")
    tr = TranscriptData(**trow.data)
    cues = get_caption_provider().split_cues(tr.words_between(start, end), clip_start=start, clip_end=end,
                                             style=caption_style_for(creator, ov))
    return {"source": "auto", "start_sec": start, "end_sec": end, "cues": cues}
