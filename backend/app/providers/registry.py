"""設定 → Provider インスタンス。アプリ本体はここ経由でしか Provider を作らない。"""
from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .base import CaptionProvider, LLMProvider, TranscriptionProvider, VideoProcessingProvider, VisionProvider


@lru_cache
def get_transcription_provider() -> TranscriptionProvider:
    s = get_settings()
    name = s.effective_transcription_provider()
    if name == "openai":
        from .transcription.openai_stt import OpenAITranscription

        return OpenAITranscription(api_key=s.openai_api_key or "", model=s.transcription_model)
    if name == "faster_whisper":
        from .transcription.faster_whisper_stt import FasterWhisperTranscription

        return FasterWhisperTranscription(model_size=s.faster_whisper_model, device=s.faster_whisper_device)
    from .transcription.mock_stt import MockTranscription

    return MockTranscription()


@lru_cache
def get_llm_provider() -> LLMProvider:
    s = get_settings()
    name = s.effective_llm_provider()
    if name == "anthropic":
        from .llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(api_key=s.anthropic_api_key or "", model=s.llm_model)
    if name == "openai":
        from .llm.openai_llm import OpenAILLM

        return OpenAILLM(api_key=s.openai_api_key or "", model=s.llm_model)
    from .llm.mock_llm import MockLLM

    return MockLLM()


@lru_cache
def get_vision_provider() -> VisionProvider:
    s = get_settings()
    if s.vision_provider == "yunet":
        from .vision.yunet import YuNetVision

        path = s.models_dir / "face_detection_yunet_2023mar.onnx"
        if path.exists():
            return YuNetVision(path)
    from .vision.yunet import NoVision

    return NoVision()


@lru_cache
def get_video_provider() -> VideoProcessingProvider:
    from .video.ffmpeg_proc import FFmpegProcessor

    return FFmpegProcessor()


@lru_cache
def get_caption_provider() -> CaptionProvider:
    from .caption.ass_caption import AssCaptionProvider

    return AssCaptionProvider()


def provider_status() -> dict:
    s = get_settings()
    return {
        "llm": {"configured": s.llm_provider, "effective": s.effective_llm_provider(), "model": s.llm_model},
        "transcription": {"configured": s.transcription_provider, "effective": s.effective_transcription_provider(), "model": s.transcription_model},
        "vision": {"effective": get_vision_provider().name},
        "video": {"effective": "ffmpeg"},
    }
