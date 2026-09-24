"""SRT 字幕書き出し（ZIP バンドル用）。

焼き込み ASS と同じ分割（ass_caption._chunk_words）で作るので、MP4 に焼かれた字幕と 1 対 1 で対応する。
編集ソフト（Premiere / CapCut / YouTube Studio）に持ち込んで手直しする前提のファイル。

- 時刻はクリップ相対（00:00:00,000 起点）
- 改行は ASS の ``\\N`` ではなく実改行
- 複数 keep_range を連結した完成品は、各区間の cue を累積尺でオフセットして 1 本にまとめる
"""
from __future__ import annotations

from typing import Any

from ...schemas.editing_dna import CaptionStyle
from ...schemas.transcript import TranscriptData, Word
from .ass_caption import _chunk_words, _wrap

Cue = tuple[float, float, str]


def build_srt(words: list[Word], *, clip_start: float, clip_end: float, style: CaptionStyle | dict[str, Any]) -> str:
    """単語列（絶対秒）→ クリップ相対の SRT 文字列。"""
    return cues_to_srt(build_cues(words, clip_start=clip_start, clip_end=clip_end, style=style))


def build_cues(words: list[Word], *, clip_start: float, clip_end: float, style: CaptionStyle | dict[str, Any]) -> list[Cue]:
    st = style if isinstance(style, CaptionStyle) else CaptionStyle(**{k: v for k, v in (style or {}).items() if k in CaptionStyle.model_fields})
    return _chunk_words(words, st, clip_start, clip_end)


def cues_from_ranges(transcript: TranscriptData, keep_ranges: list[tuple[float, float]] | list[list[float]],
                     style: CaptionStyle | dict[str, Any], overrides: list[dict[str, Any]] | None = None) -> list[Cue]:
    """EditPlan.keep_ranges（元動画の採用区間、順序付き）→ 連結後の完成品タイムラインでの cue 列。

    overrides（人が直した字幕 = edit_plan.overrides.captions、絶対秒）があれば分割し直さずそれを使う。
    """
    cues: list[Cue] = []
    offset = 0.0
    for rng in keep_ranges:
        a, b = float(rng[0]), float(rng[1])
        if b <= a:
            continue
        if overrides is not None:
            part = cues_from_overrides(overrides, clip_start=a, clip_end=b, style=style)
        else:
            part = build_cues(transcript.words_between(a, b), clip_start=a, clip_end=b, style=style)
        cues.extend((s + offset, e + offset, text) for s, e, text in part)
        offset += b - a
    return cues


def cues_from_overrides(captions: list[dict[str, Any]], *, clip_start: float, clip_end: float,
                        style: CaptionStyle | dict[str, Any]) -> list[Cue]:
    """人間が直した字幕（Candidate.overrides.captions → edit_plan.overrides.captions）→ クリップ相対 cue。

    契約は render 側と同じ: 元動画の絶対秒 {start,end,text}。区間外は切り落とし、空文は落とす。
    本文は分割し直さない（人の判断をそのまま出す）。改行を含まない長文だけ max_chars_per_line で折る。
    """
    st = style if isinstance(style, CaptionStyle) else CaptionStyle(**{k: v for k, v in (style or {}).items() if k in CaptionStyle.model_fields})
    cues: list[Cue] = []
    for c in sorted((c for c in captions or [] if isinstance(c, dict)), key=lambda c: float(c.get("start", 0.0))):
        text = str(c.get("text", "")).replace("\r", "").strip()
        if not text:
            continue
        s = max(float(c.get("start", 0.0)), clip_start) - clip_start
        e = min(float(c.get("end", 0.0)), clip_end) - clip_start
        if e <= s:
            continue
        cues.append((s, e, text if "\n" in text else _wrap(text, st.max_chars_per_line)))
    for i in range(1, len(cues)):  # 重なりは前を詰める（ASS と同じ）
        ps, pe, pt = cues[i - 1]
        if pe > cues[i][0]:
            cues[i - 1] = (ps, cues[i][0], pt)
    return cues


def cues_to_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, (s, e, text) in enumerate(cues, 1):
        text = text.replace(r"\N", "\n").replace("\r", "")
        blocks.append(f"{i}\n{_srt_ts(s)} --> {_srt_ts(e)}\n{text}\n")
    return "\n".join(blocks)


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
