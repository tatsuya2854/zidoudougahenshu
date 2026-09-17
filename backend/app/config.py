"""アプリ設定。すべて .env / 環境変数から読む。APIキーはここ以外で参照しない。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # 文字起こし
    transcription_provider: str = "openai"
    transcription_model: str = "whisper-1"
    faster_whisper_model: str = "large-v3-turbo"
    faster_whisper_device: str = "auto"

    # 映像解析
    vision_provider: str = "yunet"

    # サーバ / 保存先
    data_dir: Path = Field(default=REPO_DIR / "data")
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True

    # 字幕
    caption_font: str = "Noto Sans CJK JP"

    # 出力
    output_width: int = 1080
    output_height: int = 1920
    video_crf: int = 20
    video_preset: str = "medium"

    @property
    def data_path(self) -> Path:
        p = self.data_dir
        if not p.is_absolute():
            p = (BACKEND_DIR / p).resolve()
        return p

    @property
    def videos_dir(self) -> Path:
        return self.data_path / "videos"

    @property
    def work_dir(self) -> Path:
        return self.data_path / "work"

    @property
    def exports_dir(self) -> Path:
        return self.data_path / "exports"

    @property
    def db_path(self) -> Path:
        return self.data_path / "app.db"

    @property
    def models_dir(self) -> Path:
        return BACKEND_DIR / "models"

    @property
    def pricing_path(self) -> Path:
        return BACKEND_DIR / "config" / "pricing.yaml"

    def ensure_dirs(self) -> None:
        for d in (self.videos_dir, self.work_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)

    def has_llm_key(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return True

    def effective_llm_provider(self) -> str:
        """キー未設定なら mock に落とす（GUIが止まらないように）。"""
        return self.llm_provider if self.has_llm_key() else "mock"

    def effective_transcription_provider(self) -> str:
        if self.transcription_provider == "openai" and not self.openai_api_key:
            return "mock"
        return self.transcription_provider


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
