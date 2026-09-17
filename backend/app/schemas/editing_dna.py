"""Editing DNA — 「この編集者の判断」の構造化表現（Phase 3 で生成、Phase 4 で適用、Phase 5 で更新）。

設計方針
- 「○秒ごとにカット」のような固定ルールにしない。
  すべての操作は Condition（文脈・感情・テンション・発話内容）→ Action（編集操作）の Rule として持つ。
- Rule には confidence と observed_count と evidence を持たせ、Phase 5 の人間修正で強化/減衰できる。
- 元動画(A)と公開動画(B)の差分は EditDecision の列（EDL）として保存し、Rule はそこから抽出する。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .creator_dna import Evidence

# ────────────────────────── 観測（A と B の差分） ──────────────────────────

CutReason = Literal[
    "silence", "filler", "repetition", "mistake", "off_topic", "low_energy", "pacing", "unknown"
]


class ObservedCut(BaseModel):
    """A にあって B に無い区間。"""

    src_start: float
    src_end: float
    transcript: str = ""
    inferred_reason: CutReason = "unknown"
    reason_detail: str = ""
    confidence: int = Field(default=50, ge=0, le=100)


class ObservedKeptSilence(BaseModel):
    """残された沈黙。なぜ残したか。"""

    src_start: float
    src_end: float
    duration: float
    context_before: str = ""
    context_after: str = ""
    inferred_reason: str = ""  # 例: オチ前の溜め / リアクション待ち


class ObservedCaption(BaseModel):
    time_start: float
    time_end: float
    text: str
    emphasized_words: list[str] = Field(default_factory=list)
    style_id: str | None = None  # CaptionStyle への参照
    position: Literal["top", "center", "bottom", "custom"] = "bottom"
    inferred_purpose: str = ""  # 例: 聞き取りにくい語の補助 / 笑いどころの強調


class ObservedEffect(BaseModel):
    kind: Literal["zoom_in", "zoom_out", "pan", "shake", "flash", "freeze", "speed_up", "slow", "image_insert", "other"]
    time_start: float
    time_end: float
    magnitude: float | None = None
    trigger_text: str = ""  # 何の発言に反応した効果か
    inferred_purpose: str = ""


class ObservedAudioCue(BaseModel):
    kind: Literal["se", "bgm_start", "bgm_change", "bgm_stop", "ducking", "silence"]
    time: float
    label: str = ""  # SE の種類（ドン / ピコン / チーン…）
    trigger_text: str = ""
    inferred_purpose: str = ""


class SourceComparison(BaseModel):
    """1本分の A/B 比較結果。"""

    creator_id: str
    source_video_id: str
    published_video_id: str
    published_is_short: bool = False
    # B のどの区間が A のどこから来たか（順序入れ替えも表現できる）
    alignment: list[dict] = Field(default_factory=list, description="[{pub_start,pub_end,src_start,src_end}]")
    cuts: list[ObservedCut] = Field(default_factory=list)
    kept_silences: list[ObservedKeptSilence] = Field(default_factory=list)
    captions: list[ObservedCaption] = Field(default_factory=list)
    effects: list[ObservedEffect] = Field(default_factory=list)
    audio_cues: list[ObservedAudioCue] = Field(default_factory=list)
    opening_choice: str = Field(default="", description="Shorts の冒頭に何を持ってきたか（元動画のどこか）")
    ending_choice: str = ""
    retained_ratio: float | None = Field(default=None, description="B/A の尺比率")


# ────────────────────────── ルール（文脈 → 操作） ──────────────────────────


class Condition(BaseModel):
    """編集操作が発火する文脈。全部 optional。複数指定は AND。"""

    content_type: list[str] = Field(default_factory=list, description="例: ツッコミ / 商品紹介 / 失敗談 / 説明")
    emotion: list[str] = Field(default_factory=list, description="例: 驚き / 怒り / 照れ")
    tension_min: int | None = Field(default=None, ge=0, le=100)
    tension_max: int | None = Field(default=None, ge=0, le=100)
    speaker: str | None = None
    keyword_patterns: list[str] = Field(default_factory=list, description="発話に含まれる語（正規表現可）")
    silence_min_sec: float | None = None
    silence_max_sec: float | None = None
    position_in_clip: Literal["opening", "middle", "ending", "any"] = "any"
    after_event: str | None = Field(default=None, description="直前の出来事（例: ボケの直後）")
    note: str = ""


class CaptionStyle(BaseModel):
    id: str = "default"
    font: str = "Noto Sans CJK JP"
    font_weight: Literal["regular", "bold", "black"] = "bold"
    font_size_ratio: float = Field(default=0.035, description="出力高さに対する比率")
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 3.0
    emphasis_color: str = "#FFD400"
    emphasis_scale: float = 1.25
    background_box: bool = False
    position: Literal["top", "center", "bottom", "custom"] = "bottom"
    margin_v_ratio: float = 0.18
    max_chars_per_line: int = 14
    max_lines: int = 2
    min_duration_sec: float = 0.5
    max_duration_sec: float = 3.5
    animation: Literal["none", "pop", "fade", "typewriter"] = "none"


class Action(BaseModel):
    kind: Literal[
        "cut", "keep", "tighten_silence", "keep_silence",
        "caption", "emphasize_word", "zoom_in", "zoom_out", "pan",
        "se", "bgm_change", "bgm_stop", "image_insert", "freeze", "speed_up",
        "move_to_opening", "end_here",
    ]
    params: dict = Field(default_factory=dict, description="kind 固有: 例 tighten_silence→{target_sec:0.25}, zoom_in→{scale:1.15,duration:0.3}, se→{label:'ドン'}")
    caption_style_id: str | None = None


class EditingRule(BaseModel):
    id: str
    description: str = Field(description="人が読める説明。例: 『ボケの直後の 0.8 秒以上の沈黙は残す（リアクション待ち）』")
    condition: Condition
    action: Action
    confidence: int = Field(default=50, ge=0, le=100)
    observed_count: int = 0
    counter_count: int = Field(default=0, description="このルールに反する観測数")
    evidence: list[Evidence] = Field(default_factory=list)
    # Phase5: 人間の修正で強化/減衰した履歴
    feedback_log: list[dict] = Field(default_factory=list)
    active: bool = True


class PacingProfile(BaseModel):
    """テンポの「分布」。固定値ではなく文脈別。"""

    silence_target_sec_default: float = 0.3
    silence_target_by_context: dict[str, float] = Field(default_factory=dict, description="例: {'オチ前':0.9,'説明':0.2}")
    jump_cut_tolerance: Literal["low", "medium", "high"] = "medium"
    median_shot_length_sec: float | None = None
    shot_length_by_tension: dict[str, float] = Field(default_factory=dict, description="例: {'high':1.4,'low':4.0}")
    filler_removal: Literal["aggressive", "moderate", "keep"] = "moderate"
    reaction_keep_policy: str = Field(default="", description="リアクションを残す基準の言語化")
    time_to_punchline_sec_median: float | None = None


class ShortsPolicy(BaseModel):
    opening_strategy: list[str] = Field(default_factory=list, description="冒頭に何を置くか（結論先出し / 一番の絵 / 質問）")
    ending_strategy: list[str] = Field(default_factory=list)
    target_duration_sec: tuple[float, float] = (25.0, 55.0)
    reframe_style: Literal["face_track", "center", "split", "blur_fit"] = "face_track"
    caption_density: Literal["every_word", "every_sentence", "key_lines_only"] = "every_sentence"


class EditingDNA(BaseModel):
    version: int = 1
    creator_id: str
    compared_pairs: list[str] = Field(default_factory=list, description="SourceComparison の ID")
    rules: list[EditingRule] = Field(default_factory=list)
    pacing: PacingProfile = Field(default_factory=PacingProfile)
    caption_styles: list[CaptionStyle] = Field(default_factory=lambda: [CaptionStyle()])
    shorts_policy: ShortsPolicy = Field(default_factory=ShortsPolicy)
    se_library: list[dict] = Field(default_factory=list, description="[{label, file, typical_trigger}]")
    bgm_library: list[dict] = Field(default_factory=list)
    summary_for_prompt: str = ""
    confidence: int = Field(default=0, ge=0, le=100)


# ────────────────────────── 適用結果（EDL） ──────────────────────────


class EditDecision(BaseModel):
    """自動編集が実際に下した 1 判断。人間の修正はこの列との diff で取る。"""

    id: str
    action: Action
    time_start: float
    time_end: float
    rule_id: str | None = None
    reason: str = ""
    # 人間の修正で: accepted / modified / removed
    human_status: Literal["pending", "accepted", "modified", "removed"] = "pending"


class EditPlan(BaseModel):
    candidate_id: str
    source_video_id: str
    creator_id: str
    keep_ranges: list[tuple[float, float]] = Field(default_factory=list, description="元動画の採用区間（順序付き）")
    decisions: list[EditDecision] = Field(default_factory=list)
    caption_style_id: str = "default"
    reframe_style: str = "face_track"
    notes: str = ""
