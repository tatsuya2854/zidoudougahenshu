"""Anthropic Claude アダプタ。構造化出力（Pydantic）＋プロンプトキャッシュ。

system（Creator DNA / Editing DNA / 指示書）は固定文にして cache_control を付ける。
同じ Creator の動画を連続処理すると入力コストが大きく下がる。
"""
from __future__ import annotations

from typing import Any

import anthropic

from ..base import LLMProvider, LLMResult, Usage


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete_structured(self, *, system, user, schema, context=None, max_tokens=16000) -> LLMResult:
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
        )
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise RuntimeError(f"LLM refused: {getattr(detail, 'category', None)}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("LLM output truncated (max_tokens). 動画が長すぎる可能性。")
        u = response.usage
        usage = Usage(
            provider=self.name,
            model=self.model,
            quantities={
                "input_tokens": float(u.input_tokens or 0),
                "output_tokens": float(u.output_tokens or 0),
                "cache_read_tokens": float(getattr(u, "cache_read_input_tokens", 0) or 0),
                "cache_write_tokens": float(getattr(u, "cache_creation_input_tokens", 0) or 0),
            },
            meta={"request_id": getattr(response, "_request_id", None)},
        )
        raw = next((b.text for b in response.content if b.type == "text"), "")
        return LLMResult(parsed=response.parsed_output, usage=usage, raw_text=raw)
