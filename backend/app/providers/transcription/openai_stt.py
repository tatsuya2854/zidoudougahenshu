"""OpenAI 文字起こしアダプタ。

- whisper-1: verbose_json + word timestamps（単語タイムスタンプが取れる。字幕に必須）
- gpt-4o-transcribe-diarize: diarized_json（話者分離。単語TSは無いので文字を等分配）
- gpt-4o-transcribe / gpt-4o-mini-transcribe: json のみ → セグメント推定

25MB 制限対策として音声を 15 分ごとに分割し、オフセットを足して結合する。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...schemas.transcript import Segment, TranscriptData, Word
from ..base import TranscriptionProvider, TranscriptionResult, Usage

CHUNK_SEC = 15 * 60


class OpenAITranscription(TranscriptionProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model

    # ---- public ----
    def transcribe(self, audio_path: Path, *, language="ja", vocabulary=None, progress=None) -> TranscriptionResult:
        duration = _audio_duration(audio_path)
        chunks = _split_audio(audio_path, CHUNK_SEC) if duration > CHUNK_SEC + 30 else [(0.0, audio_path)]
        prompt = _vocab_prompt(vocabulary or [])
        segments: list[Segment] = []
        seg_id = 0
        for i, (offset, chunk) in enumerate(chunks):
            if progress:
                progress(f"文字起こし {i + 1}/{len(chunks)}", 0.1 + 0.8 * i / len(chunks))
            for s in self._transcribe_chunk(chunk, language=language, prompt=prompt):
                s.id = seg_id
                seg_id += 1
                s.start += offset
                s.end += offset
                for w in s.words:
                    w.start += offset
                    w.end += offset
                segments.append(s)
        data = TranscriptData(
            language=language, duration=duration, segments=segments,
            provider=self.name, model=self.model, vocabulary_hint=vocabulary or [],
        )
        usage = Usage(provider=self.name, model=self.model, quantities={"minutes": duration / 60.0})
        return TranscriptionResult(transcript=data, usage=usage)

    # ---- internal ----
    def _transcribe_chunk(self, path: Path, *, language: str, prompt: str) -> list[Segment]:
        kwargs: dict[str, Any] = {"model": self.model, "language": language}
        if prompt:
            kwargs["prompt"] = prompt
        with open(path, "rb") as f:
            if self.model == "whisper-1":
                resp = self._client.audio.transcriptions.create(
                    file=f, response_format="verbose_json",
                    timestamp_granularities=["word", "segment"], **kwargs,
                )
                return _from_verbose(resp)
            if "diarize" in self.model:
                resp = self._client.audio.transcriptions.create(
                    file=f, response_format="diarized_json", chunking_strategy="auto", **kwargs,
                )
                return _from_diarized(resp)
            resp = self._client.audio.transcriptions.create(file=f, response_format="json", **kwargs)
            text = getattr(resp, "text", "") or ""
            dur = _audio_duration(path)
            return [Segment(id=0, start=0.0, end=dur, text=text)]


def _vocab_prompt(vocab: list[str]) -> str:
    """Whisper 系は prompt を「直前の文脈」として扱う。語彙を自然文に埋めると保持されやすい。"""
    if not vocab:
        return ""
    joined = "、".join(vocab[:120])
    return f"（方言や口癖はそのまま書き起こす。固有名詞: {joined}）"


def _from_verbose(resp: Any) -> list[Segment]:
    words = [Word(text=w.word, start=float(w.start), end=float(w.end)) for w in (getattr(resp, "words", None) or [])]
    segs: list[Segment] = []
    for i, s in enumerate(getattr(resp, "segments", None) or []):
        st, en = float(s.start), float(s.end)
        ws = [w for w in words if w.start >= st - 0.05 and w.end <= en + 0.05]
        segs.append(Segment(id=i, start=st, end=en, text=(s.text or "").strip(), words=ws))
    if not segs and getattr(resp, "text", None):
        segs.append(Segment(id=0, start=0.0, end=float(getattr(resp, "duration", 0.0) or 0.0), text=resp.text, words=words))
    return segs


def _from_diarized(resp: Any) -> list[Segment]:
    segs: list[Segment] = []
    for i, s in enumerate(getattr(resp, "segments", None) or []):
        segs.append(Segment(id=i, start=float(s.start), end=float(s.end), text=(s.text or "").strip(), speaker=getattr(s, "speaker", None)))
    return segs


def _audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out or 0.0)


def _split_audio(path: Path, chunk_sec: int) -> list[tuple[float, Path]]:
    tmp = Path(tempfile.mkdtemp(prefix="stt_chunks_"))
    pattern = tmp / "chunk_%03d.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-f", "segment", "-segment_time", str(chunk_sec),
         "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", "-ar", "16000", str(pattern)],
        check=True,
    )
    files = sorted(tmp.glob("chunk_*.mp3"))
    return [(i * chunk_sec, f) for i, f in enumerate(files)]
