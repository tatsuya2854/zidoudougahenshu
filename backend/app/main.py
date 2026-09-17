"""Creator DNA Editor — バックエンド。`python -m app` または `uvicorn app.main:app`。

フロント（frontend/dist）をビルド済みなら同じポートで配信する。日常運用はブラウザだけで完結。
"""
from __future__ import annotations

import logging
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .config import REPO_DIR, get_settings
from .db import init_db
from .services.jobs import recover_stale_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

DIST_DIR = REPO_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    init_db()
    recover_stale_jobs()
    log.info("data dir: %s", s.data_path)
    if s.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{s.host}:{s.port}/")).start()
    yield


app = FastAPI(title="Creator DNA Editor", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = DIST_DIR / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def no_frontend() -> dict:
        return {"message": "frontend/dist がありません。`cd frontend && npm run build` を実行してください。", "api_docs": "/docs"}
