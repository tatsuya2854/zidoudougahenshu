from fastapi import APIRouter

from ..providers.registry import provider_status

router = APIRouter(tags=["status"])


@router.get("/status")
def status() -> dict:
    return {"ok": True, "version": "0.1.0-phase1", "providers": provider_status()}
