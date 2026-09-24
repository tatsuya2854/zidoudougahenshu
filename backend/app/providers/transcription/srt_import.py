"""SRT 字幕 → TranscriptData。自前字幕や YouTube の字幕を文字起こしの代わりに使う人向け。

- UTF-8（BOM 可）、CRLF 可、番号行は省略可、時刻は `HH:MM:SS,mmm`（`.` も可、時間省略も可）
- 1 cue = 1 Segment。単語タイムスタンプは無いので words は空（TranscriptData.words_between が文字を等分配する）
- 重複（前 cue の終わりより早く始まる）は開始を前 cue の終わりに寄せて補正。逆転（前 cue より前に始まる）は拒否
- テキストはそのまま。方言・口癖・表記の正規化はしない
"""
from __future__ import annotations

import re

from ...schemas.transcript import Segment, TranscriptData

PROVIDER_NAME = "srt_import"

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
_TIMELINE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}(?:\s.*)?$")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}")  # <i>…</i> / {\an8} 等の表示タグ
_INDEX = re.compile(r"^\s*\d+\s*$")


def _to_sec(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _join_lines(lines: list[str]) -> str:
    """表示用の改行を取り除く。日本語同士はそのまま連結、英数字同士はスペースで繋ぐ。"""
    out = ""
    for ln in lines:
        ln = _TAG.sub("", ln).strip()
        if not ln:
            continue
        if out and out[-1].isascii() and ln[0].isascii():
            out += " "
        out += ln
    return out


def parse_srt(text: str, *, duration: float | None = None) -> TranscriptData:
    """SRT 文字列を TranscriptData にする。壊れていれば ValueError（日本語メッセージ）。"""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if not blocks:
        raise ValueError("SRT が空です。")

    segs: list[Segment] = []
    for bi, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if lines and _INDEX.match(lines[0]):
            lines = lines[1:]
        if not lines:
            raise ValueError(f"SRT の {bi} 番目のブロックに時刻行がありません。")
        m = _TIMELINE.match(lines[0])
        if not m:
            raise ValueError(f"SRT の {bi} 番目のブロックの時刻行を読めません: {lines[0].strip()[:40]!r}（例: 00:01:02,500 --> 00:01:05,000）")
        g = m.groups()
        start, end = _to_sec(*g[:4]), _to_sec(*g[4:])
        if end <= start:
            raise ValueError(f"SRT の {bi} 番目の cue は終了が開始以下です（{start:.3f} → {end:.3f}）。")
        body = _join_lines(lines[1:])
        if not body:
            continue  # 空 cue は飛ばす
        if segs:
            prev = segs[-1]
            if start < prev.start:
                raise ValueError(f"SRT の {bi} 番目の cue が前の cue より前に始まっています（{start:.3f} < {prev.start:.3f}）。時刻順に並べてください。")
            if start < prev.end:
                start = prev.end  # 重複は前 cue の終わりに寄せる
                if end <= start:
                    raise ValueError(f"SRT の {bi} 番目の cue が前の cue に完全に含まれています。")
        if duration and start > duration + 1.0:
            raise ValueError(f"SRT の {bi} 番目の cue（{start:.1f}秒）が動画の長さ（{duration:.1f}秒）を超えています。別の動画の字幕ではありませんか。")
        if duration and end > duration:
            end = float(duration)
            if end <= start:
                continue
        segs.append(Segment(id=len(segs), start=round(start, 3), end=round(end, 3), text=body))

    if not segs:
        raise ValueError("SRT に本文のある cue がありません。")
    return TranscriptData(
        language="ja", duration=float(duration or segs[-1].end), segments=segs,
        provider=PROVIDER_NAME, model="",
    )
