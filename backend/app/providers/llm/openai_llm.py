"""OpenAI アダプタ（差し替え可能であることの証明用）。"""
from __future__ import annotations

from ..base import LLMProvider, LLMResult, Usage


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-5") -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model

    def complete_structured(self, *, system, user, schema, context=None, max_tokens=16000) -> LLMResult:
        resp = self._client.responses.parse(
            model=self.model, instructions=system, input=user, text_format=schema,
        )
        u = resp.usage
        cached = float(getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0)
        usage = Usage(provider=self.name, model=self.model, quantities={
            "input_tokens": float(u.input_tokens or 0) - cached,
            "cache_read_tokens": cached,
            "output_tokens": float(u.output_tokens or 0),
        })
        return LLMResult(parsed=resp.output_parsed, usage=usage, raw_text=resp.output_text or "")
