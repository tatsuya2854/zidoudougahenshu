"""APIキー無しで GUI を通すためのモック。

無音検出で「発話らしい区間」を切り、ダミー文を入れる。候補選定も heuristic に落ちる。
本番では使わない。開発・動作確認・E2E テスト用。
"""
from __future__ import annotations

from pathlib import Path

from ...schemas.transcript import Segment, TranscriptData, Word
from ..base import TranscriptionProvider, TranscriptionResult, Usage
from ..video.ffmpeg_proc import FFmpegProcessor


class MockTranscription(TranscriptionProvider):
    name = "mock"

    def __init__(self) -> None:
        self.model = "silence-based"

    def transcribe(self, audio_path: Path, *, language="ja", vocabulary=None, progress=None) -> TranscriptionResult:
        proc = FFmpegProcessor()
        info = proc.probe(audio_path)
        silences = proc.detect_silences(audio_path, noise_db=-35.0, min_sec=0.6)
        speech: list[tuple[float, float]] = []
        cur = 0.0
        for s, e in silences:
            if s - cur > 0.3:
                speech.append((cur, s))
            cur = e
        if info.duration - cur > 0.3:
            speech.append((cur, info.duration))
        if not speech:
            speech = [(0.0, info.duration)]
        # 長すぎる区間は 6 秒で刻む
        pieces: list[tuple[float, float]] = []
        for s, e in speech:
            t = s
            while e - t > 8.0:
                pieces.append((t, t + 6.0))
                t += 6.0
            pieces.append((t, e))
        segs: list[Segment] = []
        for i, (s, e) in enumerate(pieces):
            text = f"（発話{i + 1}：{s:.1f}秒から{e:.1f}秒までの音声）"
            n = len(text)
            words = [Word(text=ch, start=s + (e - s) * k / n, end=s + (e - s) * (k + 1) / n) for k, ch in enumerate(text)]
            segs.append(Segment(id=i, start=s, end=e, text=text, words=words))
        data = TranscriptData(language=language, duration=info.duration, segments=segs, provider=self.name, model=self.model,
                              vocabulary_hint=vocabulary or [])
        return TranscriptionResult(transcript=data, usage=Usage(provider=self.name, model=self.model, quantities={"minutes": info.duration / 60}))
