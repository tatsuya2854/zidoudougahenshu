"""ASS 字幕生成（libass で焼き込む）。

Phase 1: Creator の caption_style（手動）で文単位の字幕。
Phase 4: Editing DNA の CaptionStyle / emphasize_word ルールで語単位の強調へ。
"""
from __future__ import annotations

import re
from typing import Any

from ...schemas.editing_dna import CaptionStyle
from ...schemas.transcript import Word
from ..base import CaptionProvider

_PUNCT_BREAK = re.compile(r"[。！？!?…」』）)]$")
_SOFT_BREAK = re.compile(r"[、,]$")


class AssCaptionProvider(CaptionProvider):
    name = "ass"

    def build_ass(self, words: list[Word], *, clip_start: float, clip_end: float, style: CaptionStyle | dict[str, Any], out_w: int, out_h: int) -> str:
        st = style if isinstance(style, CaptionStyle) else CaptionStyle(**{k: v for k, v in (style or {}).items() if k in CaptionStyle.model_fields})
        cues = _chunk_words(words, st, clip_start, clip_end)
        font_size = int(out_h * st.font_size_ratio)
        margin_v = int(out_h * st.margin_v_ratio)
        align = {"bottom": 2, "center": 5, "top": 8, "custom": 2}[st.position]
        bold = -1 if st.font_weight != "regular" else 0
        border = 3 if st.background_box else 1
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {out_w}
PlayResY: {out_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{st.font},{font_size},{_ass_color(st.primary_color)},{_ass_color(st.primary_color)},{_ass_color(st.outline_color)},&H80000000,{bold},0,0,0,100,100,0,0,{border},{st.outline_width:.1f},0,{align},60,60,{margin_v},1
Style: Emph,{st.font},{int(font_size * st.emphasis_scale)},{_ass_color(st.emphasis_color)},{_ass_color(st.emphasis_color)},{_ass_color(st.outline_color)},&H80000000,-1,0,0,0,100,100,0,0,{border},{st.outline_width:.1f},0,{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines = []
        for s, e, text in cues:
            fx = ""
            if st.animation == "pop":
                fx = r"{\fscx80\fscy80\t(0,80,\fscx100\fscy100)}"
            elif st.animation == "fade":
                fx = r"{\fad(80,80)}"
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Default,,0,0,0,,{fx}{text}")
        return header + "\n".join(lines) + "\n"


def _chunk_words(words: list[Word], st: CaptionStyle, clip_start: float, clip_end: float) -> list[tuple[float, float, str]]:
    """単語列 → 表示単位。日本語は空白が無いので文字数と句読点で切る。"""
    cues: list[tuple[float, float, str]] = []
    buf: list[Word] = []
    buf_len = 0
    max_chars = st.max_chars_per_line * st.max_lines

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        s = max(buf[0].start, clip_start) - clip_start
        e = min(buf[-1].end, clip_end) - clip_start
        if e - s < st.min_duration_sec:
            e = s + st.min_duration_sec
        text = "".join(w.text for w in buf).strip()
        text = _wrap(text, st.max_chars_per_line)
        if text:
            cues.append((s, e, text))
        buf, buf_len = [], 0

    for w in words:
        if w.end <= clip_start or w.start >= clip_end:
            continue
        if buf and (w.start - buf[-1].end > 0.8):  # 間が空いたら区切る
            flush()
        buf.append(w)
        buf_len += len(w.text)
        dur = buf[-1].end - buf[0].start
        if _PUNCT_BREAK.search(w.text) or buf_len >= max_chars or dur >= st.max_duration_sec:
            flush()
        elif _SOFT_BREAK.search(w.text) and buf_len >= st.max_chars_per_line:
            flush()
    flush()
    # 重なり解消
    for i in range(1, len(cues)):
        ps, pe, pt = cues[i - 1]
        s, e, t = cues[i]
        if pe > s:
            cues[i - 1] = (ps, s, pt)
    return cues


def _wrap(text: str, n: int) -> str:
    text = text.replace("\n", "")
    if len(text) <= n:
        return text
    # 句読点優先で折る
    cut = -1
    for i in range(min(n, len(text) - 1), max(0, n // 2), -1):
        if text[i - 1] in "、。，,！？!?":
            cut = i
            break
    if cut < 0:
        cut = n
    return text[:cut] + r"\N" + text[cut:]


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()
