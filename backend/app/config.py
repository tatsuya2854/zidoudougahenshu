"""アプリ設定。すべて .env / 環境変数から読む。APIキーはここ以外で参照しない。"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def faster_whisper_available() -> bool:
    """faster-whisper が import 可能か（実 import はしない。重い依存を設定読込で引き込まない）。"""
    return importlib.util.find_spec("faster_whisper") is not None


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
    # auto | openai | faster_whisper | mock
    # auto: OPENAI_API_KEY があれば openai、無ければ faster_whisper（ローカル）、それも無ければ mock
    transcription_provider: str = "auto"
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

    def resolve_transcription_provider(self) -> tuple[str, str]:
        """実際に使う文字起こし provider 名と、その理由（GUI の status 表示用）。

        優先順: 明示指定 > auto（openai キー → faster_whisper → mock）。
        明示指定でも前提（キー / インストール）が無ければ mock に落として理由を返す。
        """
        cfg = (self.transcription_provider or "auto").lower()
        has_key = bool(self.openai_api_key)
        has_fw = faster_whisper_available()
        if cfg == "auto":
            if has_key:
                return "openai", "auto: OPENAI_API_KEY あり"
            if has_fw:
                return "faster_whisper", "auto: OPENAI_API_KEY 無し → ローカル faster-whisper"
            return "mock", "auto: OPENAI_API_KEY も faster-whisper も無い（pip install faster-whisper で実文字起こし可）"
        if cfg == "openai" and not has_key:
            return "mock", "TRANSCRIPTION_PROVIDER=openai だが OPENAI_API_KEY 未設定"
        if cfg == "faster_whisper" and not has_fw:
            return "mock", "TRANSCRIPTION_PROVIDER=faster_whisper だが未インストール（pip install faster-whisper）"
        return cfg, f"TRANSCRIPTION_PROVIDER={cfg} を明示"

    def effective_transcription_provider(self) -> str:
        return self.resolve_transcription_provider()[0]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
