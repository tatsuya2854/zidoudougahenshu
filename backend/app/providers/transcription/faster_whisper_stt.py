"""ローカル文字起こし（faster-whisper / CTranslate2）。API費用ゼロ、GPU があれば速い。

hotwords / initial_prompt に Creator Dictionary を渡して方言・固有名詞を守る。
"""
from __future__ import annotations

from pathlib import Path

from ...schemas.transcript import Segment, TranscriptData, Word
from ..base import TranscriptionProvider, TranscriptionResult, Usage


class FasterWhisperTranscription(TranscriptionProvider):
    name = "faster_whisper"

    def __init__(self, model_size: str = "large-v3-turbo", device: str = "auto") -> None:
        from faster_whisper import WhisperModel  # 任意依存

        compute = "float16" if device == "cuda" else "int8"
        self._model = WhisperModel(model_size, device=device, compute_type=compute)
        self.model = model_size

    def transcribe(self, audio_path: Path, *, language="ja", vocabulary=None, progress=None) -> TranscriptionResult:
        vocab = vocabulary or []
        hot = " ".join(vocab[:100]) if vocab else None
        prompt = f"（方言や口癖はそのまま書き起こす。固有名詞: {'、'.join(vocab[:120])}）" if vocab else None
        segments_iter, info = self._model.transcribe(
            str(audio_path), language=language, word_timestamps=True, vad_filter=True,
            hotwords=hot, initial_prompt=prompt, condition_on_previous_text=False,
        )
        segs: list[Segment] = []
        for i, s in enumerate(segments_iter):
            words = [Word(text=w.word.strip(), start=float(w.start), end=float(w.end), confidence=float(w.probability)) for w in (s.words or [])]
            segs.append(Segment(id=i, start=float(s.start), end=float(s.end), text=s.text.strip(), words=words))
            if progress and info.duration:
                progress("文字起こし（ローカル）", 0.1 + 0.8 * min(1.0, s.end / info.duration))
        data = TranscriptData(language=language, duration=float(info.duration), segments=segs,
                              provider=self.name, model=self.model, vocabulary_hint=vocab)
        usage = Usage(provider=self.name, model=self.model, quantities={"minutes": float(info.duration) / 60.0})
        return TranscriptionResult(transcript=data, usage=usage)
