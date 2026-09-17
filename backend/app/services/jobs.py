"""簡易ジョブランナー（プロセス内スレッド）。Phase 6 で Celery/Redis 等に差し替える前提の薄い層。"""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from sqlmodel import select

from ..db import session_scope
from ..models import Job

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
_lock = threading.Lock()
_cancel_flags: dict[str, bool] = {}


class JobContext:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def update(self, stage: str | None = None, progress: float | None = None, message: str | None = None) -> None:
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if not job:
                return
            if stage is not None:
                job.stage = stage
            if progress is not None:
                job.progress = float(max(0.0, min(1.0, progress)))
            if message is not None:
                job.message = message
            s.add(job)
            s.commit()

    def cancelled(self) -> bool:
        return _cancel_flags.get(self.job_id, False)


def create_job(kind: str, *, video_id: str | None, creator_id: str | None, payload: dict[str, Any]) -> Job:
    with session_scope() as s:
        job = Job(kind=kind, video_id=video_id, creator_id=creator_id, payload=payload)
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def submit(job: Job, fn: Callable[[JobContext], dict[str, Any]]) -> None:
    def _run() -> None:
        ctx = JobContext(job.id)
        with session_scope() as s:
            j = s.get(Job, job.id)
            if j:
                j.status = "running"
                j.started_at = datetime.utcnow()
                s.add(j)
                s.commit()
        try:
            result = fn(ctx)
            with session_scope() as s:
                j = s.get(Job, job.id)
                if j:
                    j.status = "done"
                    j.progress = 1.0
                    j.result = result or {}
                    j.finished_at = datetime.utcnow()
                    s.add(j)
                    s.commit()
        except Exception as e:  # noqa: BLE001
            log.exception("job %s failed", job.id)
            with session_scope() as s:
                j = s.get(Job, job.id)
                if j:
                    j.status = "failed"
                    j.error = f"{e}\n\n{traceback.format_exc()[-2000:]}"
                    j.finished_at = datetime.utcnow()
                    s.add(j)
                    s.commit()
        finally:
            _cancel_flags.pop(job.id, None)

    _executor.submit(_run)


def cancel(job_id: str) -> None:
    _cancel_flags[job_id] = True


def recover_stale_jobs() -> None:
    """再起動時に running のまま残ったジョブを failed にする。"""
    with session_scope() as s:
        for j in s.exec(select(Job).where(Job.status.in_(["running", "queued"]))).all():  # type: ignore[attr-defined]
            j.status = "failed"
            j.error = "アプリ再起動により中断"
            j.finished_at = datetime.utcnow()
            s.add(j)
        s.commit()
