"""ローカル文字起こし（faster-whisper / CTranslate2）。API費用ゼロ、GPU があれば速い。

- モデルは 1 プロセスで 1 回だけロード（クラス変数キャッシュ）。初回は HuggingFace から取得される
- ロードは transcribe 時に遅延実行。失敗時は日本語の RuntimeError にして job.error → GUI に出す
- hotwords / initial_prompt に Creator Dictionary を渡して方言・固有名詞を守る（標準語に直させない）
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ...schemas.transcript import Segment, TranscriptData, Word
from ..base import TranscriptionProvider, TranscriptionResult, Usage

log = logging.getLogger(__name__)


def _pick_device(device: str) -> tuple[str, str]:
    """(device, compute_type)。auto は CUDA が見えれば cuda/float16、無ければ cpu/int8。"""
    dev = (device or "auto").lower()
    if dev == "auto":
        try:
            import ctranslate2  # faster-whisper の依存

            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            dev = "cpu"
    return dev, ("float16" if dev == "cuda" else "int8")


class FasterWhisperTranscription(TranscriptionProvider):
    name = "faster_whisper"

    # プロセス内キャッシュ: (model_size, device) → WhisperModel
    _cache_key: tuple[str, str] | None = None
    _cache_model: Any = None
    _lock = threading.Lock()

    def __init__(self, model_size: str = "large-v3-turbo", device: str = "auto") -> None:
        try:
            import faster_whisper  # noqa: F401  任意依存。ここでは存在確認だけ
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper がインストールされていません。`pip install faster-whisper` するか "
                "TRANSCRIPTION_PROVIDER を openai / mock にしてください。"
            ) from e
        self.model = model_size
        self.device, self.compute_type = _pick_device(device)

    # ---- model ----
    def _load(self, progress: Any = None) -> Any:
        key = (self.model, self.device)
        cls = type(self)
        with cls._lock:
            if cls._cache_model is not None and cls._cache_key == key:
                return cls._cache_model
            if progress:
                progress(f"モデル読込中（{self.model} / {self.device}。初回はダウンロードで数分）", 0.0)
            from faster_whisper import WhisperModel

            t0 = time.monotonic()
            try:
                model = WhisperModel(self.model, device=self.device, compute_type=self.compute_type)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"faster-whisper のモデル読込に失敗しました（model={self.model}, device={self.device}）。"
                    "初回は HuggingFace からのダウンロードが必要です。ネットワーク・ディスク容量・"
                    "FASTER_WHISPER_MODEL の綴り（tiny / base / small / medium / large-v3 / large-v3-turbo）を確認してください。"
                    f" 詳細: {type(e).__name__}: {e}"
                ) from e
            log.info("faster-whisper model loaded: %s on %s (%s) in %.1fs", self.model, self.device, self.compute_type, time.monotonic() - t0)
            cls._cache_key, cls._cache_model = key, model
            return model

    # ---- public ----
    def transcribe(self, audio_path: Path, *, language="ja", vocabulary=None, progress=None) -> TranscriptionResult:
        vocab = vocabulary or []
        hot = " ".join(vocab[:100]) if vocab else None
        prompt = f"（方言や口癖はそのまま書き起こす。固有名詞: {'、'.join(vocab[:120])}）" if vocab else None
        model = self._load(progress)
        t0 = time.monotonic()
        try:
            segments_iter, info = model.transcribe(
                str(audio_path), language=language, word_timestamps=True, vad_filter=True,
                hotwords=hot, initial_prompt=prompt, condition_on_previous_text=False,
            )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"faster-whisper の文字起こしに失敗しました: {type(e).__name__}: {e}") from e
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        segs: list[Segment] = []
        for i, s in enumerate(segments_iter):
            text = (s.text or "").strip()
            if not text:
                continue
            words = [
                Word(text=w.word.strip(), start=float(w.start), end=float(w.end), confidence=float(w.probability))
                for w in (s.words or []) if w.word.strip()
            ]
            segs.append(Segment(id=len(segs), start=float(s.start), end=float(s.end), text=text, words=words))
            if progress and duration:
                progress(f"文字起こし（ローカル）{float(s.end) / 60:.1f}/{duration / 60:.1f}分", 0.1 + 0.8 * min(1.0, float(s.end) / duration))
        data = TranscriptData(language=language, duration=duration, segments=segs,
                              provider=self.name, model=self.model, vocabulary_hint=vocab)
        usage = Usage(provider=self.name, model=self.model, quantities={"minutes": duration / 60.0},
                      meta={"device": self.device, "compute_type": self.compute_type,
                            "wall_seconds": round(time.monotonic() - t0, 1)})
        return TranscriptionResult(transcript=data, usage=usage)
