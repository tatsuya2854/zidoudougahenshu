"""文字起こしの共通表現。どの Provider もこの形に正規化する。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Word(BaseModel):
    text: str
    start: float
    end: float
    confidence: float | None = None


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    speaker: str | None = None
    # 音声イベント（笑い・拍手など）。Scribe 等が返す。
    events: list[str] = Field(default_factory=list)


class TranscriptData(BaseModel):
    language: str = "ja"
    duration: float = 0.0
    segments: list[Segment] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    # 方言/口癖の保持のために渡した語彙（監査用）
    vocabulary_hint: list[str] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "".join(s.text for s in self.segments)

    def as_timed_lines(self) -> str:
        """LLM に渡す [mm:ss.s] 形式。"""
        out = []
        for s in self.segments:
            spk = f"{s.speaker}: " if s.speaker else ""
            out.append(f"[{_fmt(s.start)}-{_fmt(s.end)}] {spk}{s.text}")
        return "\n".join(out)

    def words_between(self, start: float, end: float) -> list[Word]:
        res: list[Word] = []
        for s in self.segments:
            if s.end < start or s.start > end:
                continue
            if s.words:
                res.extend(w for w in s.words if w.end > start and w.start < end)
            else:
                # 単語タイムスタンプが無い Provider: セグメントを等分配
                n = max(len(s.text), 1)
                for i, ch in enumerate(s.text):
                    ws = s.start + (s.end - s.start) * i / n
                    we = s.start + (s.end - s.start) * (i + 1) / n
                    if we > start and ws < end:
                        res.append(Word(text=ch, start=ws, end=we))
        return res


def _fmt(t: float) -> str:
    m = int(t // 60)
    s = t - m * 60
    return f"{m:02d}:{s:04.1f}"
