"""Creator DNA — 「この人らしさ」の構造化表現（Phase 2 で生成、Phase 1 では Dictionary のみ運用）。

設計方針
- すべて「証拠付き」: 各特徴は evidence（動画ID＋秒＋引用）を持つ。LLM の妄想を後から検証できる。
- 数値は分布で持つ（平均だけにしない）。テンションの「上がり方」は時系列で持つ。
- Dictionary は文字起こしプロンプトに直結する（方言・口癖を標準語に直させない）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    video_id: str
    start_sec: float
    end_sec: float
    quote: str = ""


class DictionaryEntry(BaseModel):
    term: str = Field(description="表記そのまま（例: 〜やねん / なんぼ / ○○さん）")
    reading: str | None = Field(default=None, description="読み。ASR のヒント用")
    category: Literal[
        "dialect", "catchphrase", "sentence_ending", "person", "brand", "product", "place", "jargon", "other"
    ]
    standard_form: str | None = Field(default=None, description="標準語に直すとどうなるか（直さないための記録）")
    note: str = ""
    frequency_per_10min: float | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class SpeechStyle(BaseModel):
    dialect: str = Field(default="", description="例: 関西弁（大阪寄り）")
    first_person: list[str] = Field(default_factory=list, description="一人称")
    sentence_endings: list[str] = Field(default_factory=list, description="語尾の傾向")
    catchphrases: list[DictionaryEntry] = Field(default_factory=list)
    phrasing_patterns: list[str] = Field(default_factory=list, description="言い回しの癖")
    address_to_viewer: list[str] = Field(default_factory=list, description="視聴者への呼びかけ方（例: みんな / 君たち）")
    filler_words: list[str] = Field(default_factory=list, description="えー、あの、など。カット判断の材料")


class ProsodyProfile(BaseModel):
    """話し方の物理量。音声解析から算出。"""

    speaking_rate_cps_mean: float | None = Field(default=None, description="文字/秒 平均")
    speaking_rate_cps_p10: float | None = None
    speaking_rate_cps_p90: float | None = None
    loudness_lufs_mean: float | None = None
    loudness_range_lu: float | None = None
    pause_median_sec: float | None = None
    pause_p90_sec: float | None = None
    # 「間」の使い方: どんな文脈で長い沈黙を置くか
    intentional_pause_contexts: list[str] = Field(default_factory=list)
    laugh_style: str = Field(default="", description="笑い方の特徴（例: 息漏れの笑い / 大声）")
    laugh_rate_per_10min: float | None = None


class EmotionProfile(BaseModel):
    baseline_tension: int = Field(default=50, ge=0, le=100)
    tension_triggers: list[str] = Field(default_factory=list, description="テンションが上がる話題/状況")
    emotion_transition_patterns: list[str] = Field(default_factory=list, description="感情の変化の典型パターン")
    reaction_patterns: list[str] = Field(default_factory=list, description="リアクションの型（驚き→ツッコミ など）")


class HumorProfile(BaseModel):
    tsukkomi_patterns: list[str] = Field(default_factory=list)
    boke_patterns: list[str] = Field(default_factory=list)
    running_gags: list[str] = Field(default_factory=list)
    self_deprecation_level: int = Field(default=50, ge=0, le=100)


class StructureProfile(BaseModel):
    opening_hook_patterns: list[str] = Field(default_factory=list, description="冒頭フックの型")
    topic_transition_patterns: list[str] = Field(default_factory=list, description="話題転換の型")
    climax_patterns: list[str] = Field(default_factory=list, description="盛り上がりの作り方")
    punchline_patterns: list[str] = Field(default_factory=list, description="オチの型")
    closing_patterns: list[str] = Field(default_factory=list, description="締めの型")


class SignatureMoment(BaseModel):
    """その人らしい発言・瞬間。候補選定の類似度計算に使う。"""

    description: str
    quote: str
    why_signature: str
    evidence: Evidence


class PastShortsPattern(BaseModel):
    """過去に実際に Shorts 化された部分の傾向（Editing DNA と連携）。"""

    typical_duration_sec: float | None = None
    common_content_types: list[str] = Field(default_factory=list)
    common_hook_types: list[str] = Field(default_factory=list)
    examples: list[SignatureMoment] = Field(default_factory=list)


class CreatorDNA(BaseModel):
    version: int = 1
    creator_id: str
    analyzed_video_ids: list[str] = Field(default_factory=list)
    analyzed_minutes: float = 0.0
    dictionary: list[DictionaryEntry] = Field(default_factory=list)
    speech: SpeechStyle = Field(default_factory=SpeechStyle)
    prosody: ProsodyProfile = Field(default_factory=ProsodyProfile)
    emotion: EmotionProfile = Field(default_factory=EmotionProfile)
    humor: HumorProfile = Field(default_factory=HumorProfile)
    structure: StructureProfile = Field(default_factory=StructureProfile)
    signature_moments: list[SignatureMoment] = Field(default_factory=list)
    past_shorts: PastShortsPattern = Field(default_factory=PastShortsPattern)
    # LLM に渡す 300〜600字の要約（プロンプトキャッシュ前提で固定文にする）
    summary_for_prompt: str = ""
    confidence: int = Field(default=0, ge=0, le=100, description="解析量に応じた信頼度")

    def transcription_hints(self) -> list[str]:
        """ASR に渡す語彙ヒント（方言・固有名詞を守る）。"""
        terms = [d.term for d in self.dictionary]
        terms += [c.term for c in self.speech.catchphrases]
        seen: set[str] = set()
        out: list[str] = []
        for t in terms:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out
