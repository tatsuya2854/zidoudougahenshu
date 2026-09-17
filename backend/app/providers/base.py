"""Provider Adapter Layer の共通インターフェース。

アプリ本体はこの抽象だけに依存する。OpenAI / Anthropic / Google / ローカルモデルは
この下に「アダプタ」として差し込む。APIキーはアダプタ内部でしか触らない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
from pydantic import BaseModel

from ..schemas.transcript import TranscriptData

T = TypeVar("T", bound=BaseModel)


# ───────────── 使用量（原価計算の入力） ─────────────


@dataclass
class Usage:
    """Provider が返す使用量。CostTracker が単価を掛けて USD にする。"""

    provider: str
    model: str
    # unit → quantity  例: {"minutes": 12.5} / {"input_tokens": 9000, "output_tokens": 1200}
    quantities: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


# ───────────── 文字起こし ─────────────


@dataclass
class TranscriptionResult:
    transcript: TranscriptData
    usage: Usage


class TranscriptionProvider(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "ja",
        vocabulary: list[str] | None = None,
        progress: Any = None,
    ) -> TranscriptionResult:
        """audio_path（16kHz mono 推奨）を文字起こしする。

        vocabulary: Creator Dictionary の語（方言・固有名詞）。標準語に直させないヒント。
        """


# ───────────── LLM ─────────────


@dataclass
class LLMResult:
    parsed: BaseModel
    usage: Usage
    raw_text: str = ""


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        context: dict[str, Any] | None = None,
        max_tokens: int = 16000,
    ) -> LLMResult:
        """system/user を投げて schema（Pydantic）で検証済みのオブジェクトを返す。

        system は Creator DNA 等の固定文を含む想定なのでアダプタ側でキャッシュ対象にする。
        context は mock 等が使う補助情報（本物の LLM は無視してよい）。
        """


# ───────────── 映像解析 ─────────────


@dataclass
class Face:
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


class VisionProvider(ABC):
    name: str = "base"

    @abstractmethod
    def detect_faces(self, frame_bgr: np.ndarray) -> list[Face]:
        """1フレームから顔を検出。座標はピクセル。"""

    def usage_for_frames(self, n: int) -> Usage:
        return Usage(provider=self.name, model="", quantities={"frames": float(n)})


# ───────────── 動画処理 ─────────────


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    size_bytes: int
    has_audio: bool


@dataclass
class CropKeyframe:
    t: float  # クリップ内の秒
    x: int  # crop 左上 x（ピクセル、元解像度）
    y: int = 0


class VideoProcessingProvider(ABC):
    name: str = "base"

    @abstractmethod
    def probe(self, path: Path) -> MediaInfo: ...

    @abstractmethod
    def extract_audio(self, src: Path, dst: Path, *, sample_rate: int = 16000) -> Path: ...

    @abstractmethod
    def extract_frame(self, src: Path, t: float, dst: Path, *, width: int = 640) -> Path: ...

    @abstractmethod
    def detect_silences(self, audio: Path, *, noise_db: float = -35.0, min_sec: float = 0.5) -> list[tuple[float, float]]: ...

    @abstractmethod
    def render_vertical(
        self,
        src: Path,
        dst: Path,
        *,
        start: float,
        end: float,
        crop_w: int,
        crop_h: int,
        keyframes: list[CropKeyframe],
        out_w: int,
        out_h: int,
        ass_path: Path | None,
        fonts_dir: Path | None,
        style: str = "face_track",
        progress: Any = None,
    ) -> Usage: ...


# ───────────── 字幕 ─────────────


class CaptionProvider(ABC):
    name: str = "base"

    @abstractmethod
    def build_ass(self, words: list, *, clip_start: float, clip_end: float, style: Any, out_w: int, out_h: int) -> str:
        """word 列（絶対秒）からクリップ相対の ASS 文字列を作る。"""
