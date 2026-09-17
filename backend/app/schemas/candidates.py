"""Shorts 候補。LLM の構造化出力 = この形。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateScores(BaseModel):
    hook_strength: int = Field(ge=0, le=100, description="冒頭3秒で止められるか")
    standalone: int = Field(ge=0, le=100, description="前後の文脈無しで成立するか")
    creator_likeness: int = Field(ge=0, le=100, description="このCreatorらしさ")
    retention: int = Field(ge=0, le=100, description="最後まで見られる構造か")
    payoff: int = Field(ge=0, le=100, description="オチ・落着点の強さ")
    past_shorts_similarity: int = Field(
        ge=0, le=100, description="このCreatorが過去にShorts化した部分との近さ(DNA無しなら50)"
    )


class ShortsCandidate(BaseModel):
    start_sec: float = Field(description="開始秒。発話の切れ目に合わせる")
    end_sec: float = Field(description="終了秒。オチの直後で切る")
    title: str = Field(description="Shortsタイトル案（20字以内）")
    title_alternatives: list[str] = Field(default_factory=list, description="別案 2つ")
    summary: str = Field(description="この区間で何が起きているか（内容）")
    hook: str = Field(description="冒頭フック: 最初の1〜2文をそのまま引用")
    hook_suggestion: str = Field(default="", description="冒頭に別の発言を持ってくるなら、その発言と秒数")
    why_clip: str = Field(description="なぜ切り抜く価値があるか")
    creator_likeness_reason: str = Field(description="どこがこのCreatorらしいか（口癖・方言・リアクション等を具体的に）")
    standalone_ok: bool = Field(description="単体で内容が成立するか")
    standalone_reason: str = Field(default="")
    retention_reason: str = Field(description="視聴維持を期待できる理由（構造・テンポ・引き）")
    scores: CandidateScores
    score: int = Field(ge=0, le=100, description="総合スコア")
    tags: list[str] = Field(default_factory=list, description="例: ツッコミ, 商品紹介, 失敗談")

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


class CandidateList(BaseModel):
    candidates: list[ShortsCandidate] = Field(min_length=1)
    overall_notes: str = Field(default="", description="動画全体の所感・迷った候補")
