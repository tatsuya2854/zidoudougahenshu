"""DBテーブル定義（SQLModel / SQLite）。

Creator ごとに Profile を完全分離する: すべての解析結果は creator_id を持つ。
JSON 列には schemas/ の Pydantic モデルをシリアライズして入れる。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, Text, event
from sqlalchemy.engine import Engine
from sqlmodel import Field, SQLModel


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.utcnow()


class Creator(SQLModel, table=True):
    id: str = Field(default_factory=_uid, primary_key=True)
    name: str
    channel_url: Optional[str] = None
    notes: Optional[str] = None
    # Creator Dictionary: [{term, reading, category, note}]  (Phase1から使う)
    dictionary: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Creator DNA (Phase2) / Editing DNA (Phase3) — schemas/creator_dna.py, editing_dna.py
    creator_dna: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    editing_dna: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    # Phase1 の字幕スタイル（Editing DNA が育つまでの手動設定）
    caption_style: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Video(SQLModel, table=True):
    id: str = Field(default_factory=_uid, primary_key=True)
    creator_id: str = Field(foreign_key="creator.id", index=True)
    title: str
    original_filename: str
    path: str
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    size_bytes: int = 0
    # uploaded | analyzing | analyzed | failed
    status: str = "uploaded"
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Transcript(SQLModel, table=True):
    id: str = Field(default_factory=_uid, primary_key=True)
    video_id: str = Field(foreign_key="video.id", index=True)
    creator_id: str = Field(index=True)
    provider: str
    model: str
    language: str = "ja"
    # schemas.transcript.TranscriptData
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class Candidate(SQLModel, table=True):
    id: str = Field(default_factory=_uid, primary_key=True)
    video_id: str = Field(foreign_key="video.id", index=True)
    creator_id: str = Field(index=True)
    rank: int = 0
    start_sec: float
    end_sec: float
    score: float = 0.0
    title: str = ""
    # schemas.candidates.ShortsCandidate 全体
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # 人間の判断（Phase5 の学習材料）: pending | accepted | rejected
    decision: str = "pending"
    decided_at: Optional[datetime] = None
    # 人間の手直し（GUI）。元の start/end/title/data は残し、書き出し時にこれを上乗せする。
    # {start_sec, end_sec, title, crop_mode, crop_x, font_size_ratio, caption_position, captions:[{start,end,text}]}
    # 変更は必ず HumanEdit(candidate_edited) に before/after で残す（Phase5 の学習材料）。
    overrides: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class Export(SQLModel, table=True):
    id: str = Field(default_factory=_uid, primary_key=True)
    candidate_id: str = Field(foreign_key="candidate.id", index=True)
    video_id: str = Field(index=True)
    creator_id: str = Field(index=True)
    job_id: Optional[str] = None
    # queued | rendering | done | failed
    status: str = "queued"
    path: Optional[str] = None
    # 実際に適用した編集設定（EDL）。Phase5 で人間の修正と diff を取る基準。
    edit_plan: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None


class Job(SQLModel, table=True):
    id: str = Field(default_factory=_uid, primary_key=True)
    # analyze | export
    kind: str
    video_id: Optional[str] = Field(default=None, index=True)
    creator_id: Optional[str] = Field(default=None, index=True)
    # queued | running | done | failed | cancelled
    status: str = "queued"
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class CostEntry(SQLModel, table=True):
    """原価台帳。動画1本・Creator1人単位で集計できる。"""

    id: str = Field(default_factory=_uid, primary_key=True)
    creator_id: Optional[str] = Field(default=None, index=True)
    video_id: Optional[str] = Field(default=None, index=True)
    job_id: Optional[str] = Field(default=None, index=True)
    # transcription | llm | vision | video_processing | storage
    category: str
    provider: str
    model: str = ""
    quantity: float = 0.0
    # minutes | input_tokens | output_tokens | cache_read_tokens | cache_write_tokens | frames | cpu_minutes | gb_month
    unit: str = ""
    usd: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class HumanEdit(SQLModel, table=True):
    """人間の修正ログ（Phase5）。Phase1 では候補の採用/却下だけ記録する。"""

    id: str = Field(default_factory=_uid, primary_key=True)
    creator_id: str = Field(index=True)
    video_id: Optional[str] = Field(default=None, index=True)
    candidate_id: Optional[str] = None
    export_id: Optional[str] = None
    # candidate_accepted | candidate_rejected | cut_removed | cut_added | caption_edited | ...
    action: str
    before: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    after: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # LLM が推定した「なぜ直したか」（Phase5）
    inferred_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


# ────────────────────────── 軽量マイグレーション ──────────────────────────
# SQLModel.metadata.create_all は既存テーブルに列を足さない。列追加は「無ければ ALTER」で追従する。
# (table, column, DDL)
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("candidate", "overrides", "JSON DEFAULT '{}'"),
)


def _migrate_dbapi(conn) -> list[str]:  # noqa: ANN001
    """生の sqlite3 接続に対して不足列を足す。戻り値: 足した "table.column"。"""
    added: list[str] = []
    for table, col, ddl in _ADDED_COLUMNS:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and col not in cols:  # テーブル未作成なら create_all に任せる
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            added.append(f"{table}.{col}")
    if added:
        conn.commit()
    return added


@event.listens_for(Engine, "connect")
def _migrate_on_connect(dbapi_conn, _record) -> None:  # noqa: ANN001
    """どの Engine でも接続時に列を補う（旧 DB でも SELECT candidate.overrides が落ちないように）。"""
    try:
        _migrate_dbapi(dbapi_conn)
    except Exception:  # noqa: BLE001  — 読み取り専用 DB 等。以降の SELECT で本当のエラーが出る
        pass


def ensure_candidate_overrides_column(engine=None) -> list[str]:  # noqa: ANN001
    """Candidate.overrides 列を明示的に保証する（接続時フックの手動版。冪等）。"""
    if engine is None:
        from .db import get_engine

        engine = get_engine()
    with engine.begin() as conn:
        return _migrate_dbapi(conn.connection.dbapi_connection)
