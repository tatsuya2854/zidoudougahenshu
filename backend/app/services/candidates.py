"""Shorts 候補選定。LLM（本番）と heuristic（キー無し）の 2 系統。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import BACKEND_DIR
from ..models import Creator
from ..providers.base import LLMProvider, LLMResult
from ..schemas.candidates import CandidateList, CandidateScores, ShortsCandidate
from ..schemas.transcript import TranscriptData

PROMPT_PATH = BACKEND_DIR / "app" / "prompts" / "candidate_selection.md"


def build_system_prompt(creator: Creator, n: int) -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8").replace("{n}", str(n))
    parts = [base, "", "## このCreatorについて", f"名前: {creator.name}"]
    if creator.notes:
        parts.append(f"メモ: {creator.notes}")
    if creator.dictionary:
        parts.append("\n### Creator Dictionary（方言・口癖・固有名詞。標準語に直さない）")
        for d in creator.dictionary[:200]:
            line = f"- {d.get('term')}"
            if d.get("category"):
                line += f" [{d['category']}]"
            if d.get("note"):
                line += f": {d['note']}"
            parts.append(line)
    dna = creator.creator_dna or {}
    if dna.get("summary_for_prompt"):
        parts.append("\n### Creator DNA（要約）\n" + dna["summary_for_prompt"])
    edna = creator.editing_dna or {}
    if edna.get("summary_for_prompt"):
        parts.append("\n### Editing DNA（この編集者の判断傾向）\n" + edna["summary_for_prompt"])
    return "\n".join(parts)


def build_user_prompt(transcript: TranscriptData, video_title: str) -> str:
    return (
        f"## 動画タイトル\n{video_title}\n\n"
        f"## 尺\n{transcript.duration / 60:.1f} 分\n\n"
        f"## 文字起こし（[開始-終了] 本文）\n{transcript.as_timed_lines()}\n"
    )


def select_candidates(llm: LLMProvider, creator: Creator, transcript: TranscriptData, video_title: str, n: int = 10) -> LLMResult:
    system = build_system_prompt(creator, n)
    user = build_user_prompt(transcript, video_title)
    result = llm.complete_structured(system=system, user=user, schema=CandidateList,
                                     context={"transcript": transcript, "n": n}, max_tokens=24000)
    cl: CandidateList = result.parsed  # type: ignore[assignment]
    cl.candidates = _postprocess(cl.candidates, transcript, n)
    result.parsed = cl
    return result


def _postprocess(cands: list[ShortsCandidate], tr: TranscriptData, n: int) -> list[ShortsCandidate]:
    """範囲の正規化、重複除去、スコア降順。"""
    out: list[ShortsCandidate] = []
    for c in cands:
        c.start_sec = max(0.0, min(c.start_sec, tr.duration))
        c.end_sec = max(c.start_sec + 5.0, min(c.end_sec, tr.duration))
        if c.end_sec - c.start_sec > 90:
            c.end_sec = c.start_sec + 90
        out.append(c)
    out.sort(key=lambda c: c.score, reverse=True)
    kept: list[ShortsCandidate] = []
    for c in out:
        overlap = any(_overlap(c, k) > 0.5 for k in kept)
        if not overlap:
            kept.append(c)
    if len(kept) < n:  # 重複除去で減った分は heuristic で補充
        extra = heuristic_candidates(tr, n=n * 2).candidates
        for c in extra:
            if len(kept) >= n:
                break
            if not any(_overlap(c, k) > 0.2 for k in kept):
                c.score = min(c.score, 40)
                c.tags = c.tags + ["補充"]
                kept.append(c)
    return kept


def _overlap(a: ShortsCandidate, b: ShortsCandidate) -> float:
    inter = max(0.0, min(a.end_sec, b.end_sec) - max(a.start_sec, b.start_sec))
    shorter = max(1e-6, min(a.duration, b.duration))
    return inter / shorter


def heuristic_candidates(tr: TranscriptData, n: int = 10) -> CandidateList:
    """LLM 無しの簡易版: 発話密度と区切りで 30〜50 秒窓を作る。品質は低い（開発用）。"""
    segs = tr.segments
    if not segs:
        return CandidateList(candidates=[_placeholder(0.0, min(30.0, tr.duration or 30.0), 0)], overall_notes="文字起こしが空")
    # 窓の長さ: 動画尺を n 分割した長さ（10〜45秒にクランプ）。短い動画でも n 本出す。
    win = max(10.0, min(45.0, (tr.duration or 60.0) / max(n, 1)))
    min_len = max(6.0, win * 0.5)
    windows: list[tuple[float, float, float]] = []  # (start, end, density)
    for i in range(len(segs)):
        start = segs[i].start
        j = i
        chars = 0
        while j < len(segs) and segs[j].end - start < win:
            chars += len(segs[j].text)
            j += 1
        end = segs[j - 1].end if j - 1 >= i else start + win
        if end - start >= min_len:
            windows.append((start, end, chars / max(end - start, 1.0)))
    windows.sort(key=lambda w: w[2], reverse=True)
    picked: list[tuple[float, float, float]] = []
    for w in windows:
        if all(min(w[1], p[1]) - max(w[0], p[0]) <= 0 for p in picked):
            picked.append(w)
        if len(picked) >= n:
            break
    if not picked:
        picked = [(0.0, min(40.0, tr.duration), 0.0)]
    picked.sort(key=lambda w: w[0])
    cands = [_placeholder(s, e, k, density=d, tr=tr) for k, (s, e, d) in enumerate(picked)]
    return CandidateList(candidates=cands, overall_notes="LLM 未設定のため発話密度ベースの仮候補。APIキーを設定すると本物の選定になる。")


def _placeholder(s: float, e: float, k: int, density: float = 0.0, tr: TranscriptData | None = None) -> ShortsCandidate:
    text = "".join(x.text for x in (tr.segments if tr else []) if x.start >= s and x.end <= e)
    hook = text[:40]
    score = int(min(95, 40 + density * 8))
    return ShortsCandidate(
        start_sec=round(s, 2), end_sec=round(e, 2), title=f"仮候補 {k + 1}", title_alternatives=[],
        summary=text[:120] or "（内容は文字起こし後に確定）", hook=hook or "（冒頭発話）", hook_suggestion="",
        why_clip="発話密度が高い区間（heuristic）", creator_likeness_reason="Creator DNA 未生成のため評価不能",
        standalone_ok=True, standalone_reason="未評価", retention_reason="未評価（LLM 未設定）",
        scores=CandidateScores(hook_strength=50, standalone=50, creator_likeness=50, retention=50, payoff=50, past_shorts_similarity=50),
        score=score, tags=["heuristic"],
    )
