from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..db import get_session
from ..models import Creator, Video

router = APIRouter(prefix="/creators", tags=["creators"])


class CreatorIn(BaseModel):
    name: str
    channel_url: str | None = None
    notes: str | None = None
    dictionary: list[dict[str, Any]] = Field(default_factory=list)
    caption_style: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def list_creators(s: Session = Depends(get_session)) -> list[dict]:
    rows = s.exec(select(Creator).order_by(Creator.created_at)).all()
    out = []
    for c in rows:
        n = len(s.exec(select(Video.id).where(Video.creator_id == c.id)).all())
        d = c.model_dump()
        d["video_count"] = n
        d["has_creator_dna"] = bool(c.creator_dna)
        d["has_editing_dna"] = bool(c.editing_dna)
        out.append(d)
    return out


@router.post("", status_code=201)
def create_creator(body: CreatorIn, s: Session = Depends(get_session)) -> dict:
    c = Creator(**body.model_dump())
    s.add(c)
    s.commit()
    s.refresh(c)
    return c.model_dump()


@router.get("/{creator_id}")
def get_creator(creator_id: str, s: Session = Depends(get_session)) -> dict:
    c = s.get(Creator, creator_id)
    if not c:
        raise HTTPException(404)
    return c.model_dump()


@router.put("/{creator_id}")
def update_creator(creator_id: str, body: CreatorIn, s: Session = Depends(get_session)) -> dict:
    c = s.get(Creator, creator_id)
    if not c:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    c.updated_at = datetime.utcnow()
    s.add(c)
    s.commit()
    s.refresh(c)
    return c.model_dump()


@router.delete("/{creator_id}", status_code=204)
def delete_creator(creator_id: str, s: Session = Depends(get_session)) -> None:
    c = s.get(Creator, creator_id)
    if not c:
        raise HTTPException(404)
    if s.exec(select(Video).where(Video.creator_id == creator_id)).first():
        raise HTTPException(409, "動画が残っているCreatorは削除できません")
    s.delete(c)
    s.commit()
