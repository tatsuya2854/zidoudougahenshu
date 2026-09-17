"""原価台帳。Usage × 単価 → CostEntry。動画単位・Creator単位で集計。"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml
from sqlmodel import Session, select

from ..config import get_settings
from ..models import CostEntry
from ..providers.base import Usage


@lru_cache
def _pricing() -> dict[str, Any]:
    with open(get_settings().pricing_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def price_usage(category: str, usage: Usage) -> float:
    table = _pricing().get(category, {}).get(usage.provider, {})
    rates = table.get(usage.model) or table.get("*") or {}
    total = 0.0
    for unit, qty in usage.quantities.items():
        total += float(rates.get(unit, 0.0)) * float(qty)
    return round(total, 6)


def record_usage(session: Session, *, category: str, usage: Usage, creator_id: str | None, video_id: str | None,
                 job_id: str | None) -> CostEntry:
    """quantities が複数単位ある場合も 1 エントリにまとめ、内訳は meta に残す。"""
    usd = price_usage(category, usage)
    main_unit = next(iter(usage.quantities), "")
    entry = CostEntry(
        creator_id=creator_id, video_id=video_id, job_id=job_id, category=category,
        provider=usage.provider, model=usage.model, quantity=float(usage.quantities.get(main_unit, 0.0)),
        unit=main_unit, usd=usd, meta={"quantities": usage.quantities, **usage.meta},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def record_storage(session: Session, *, size_bytes: int, creator_id: str | None, video_id: str | None) -> CostEntry:
    gb = size_bytes / (1024**3)
    return record_usage(session, category="storage", usage=Usage(provider="local", model="", quantities={"gb_month": gb}),
                        creator_id=creator_id, video_id=video_id, job_id=None)


def summarize(session: Session, *, video_id: str | None = None, creator_id: str | None = None) -> dict[str, Any]:
    q = select(CostEntry)
    if video_id:
        q = q.where(CostEntry.video_id == video_id)
    if creator_id:
        q = q.where(CostEntry.creator_id == creator_id)
    rows = session.exec(q).all()
    by_cat: dict[str, float] = {}
    for r in rows:
        by_cat[r.category] = round(by_cat.get(r.category, 0.0) + r.usd, 6)
    for cat in ("transcription", "llm", "vision", "video_processing", "storage"):
        by_cat.setdefault(cat, 0.0)
    return {"by_category": by_cat, "total_usd": round(sum(by_cat.values()), 6), "entries": len(rows)}
