"""キー無し動作用。候補選定は services.candidates.heuristic_candidates に委譲する。"""
from __future__ import annotations

from ..base import LLMProvider, LLMResult, Usage


class MockLLM(LLMProvider):
    name = "mock"
    model = "heuristic"

    def complete_structured(self, *, system, user, schema, context=None, max_tokens=16000) -> LLMResult:
        from ...schemas.candidates import CandidateList
        from ...services.candidates import heuristic_candidates

        if schema is CandidateList and context and "transcript" in context:
            parsed = heuristic_candidates(context["transcript"], n=context.get("n", 10))
            return LLMResult(parsed=parsed, usage=Usage(provider=self.name, model=self.model))
        raise NotImplementedError("MockLLM は候補選定以外に対応していません。APIキーを設定してください。")
