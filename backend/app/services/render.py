"""EditPlan → 縦動画 MP4。Phase 1 は「切る・9:16・字幕」だけ。Phase 4 でズーム/SE/BGM が乗る。

Candidate.overrides（GUI での人の手直し）はここで上乗せする。元の候補値は Candidate 側に残し、
何をどう変えたかは edit_plan.overrides と HumanEdit に残る（Phase5 の学習材料）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import get_settings
from ..models import Candidate, Creator, Video
from ..providers.base import CropKeyframe, Usage
from ..providers.registry import get_caption_provider, get_video_provider, get_vision_provider
from ..schemas.editing_dna import CaptionStyle
from ..schemas.transcript import TranscriptData
from .reframe import ReframeResult, compute_crop_size, plan_reframe

FONTS_DIR = Path("/usr/share/fonts")

# overrides.crop_mode の取りうる値。face_track 以外は顔検出を回さない。
CROP_MODES = ("face_track", "center", "blur_fit", "manual")


def caption_style_for(creator: Creator, overrides: dict[str, Any] | None = None) -> CaptionStyle:
    """Creator の字幕スタイル（手動設定 → Editing DNA default）に、候補単位の手直しを上乗せ。"""
    s = get_settings()
    base: dict[str, Any] = {"font": s.caption_font}
    base.update(creator.caption_style or {})
    edna = creator.editing_dna or {}
    for st in edna.get("caption_styles", []) or []:
        if st.get("id") == "default":
            base.update(st)
    ov = overrides or {}
    if ov.get("font_size_ratio") is not None:
        base["font_size_ratio"] = float(ov["font_size_ratio"])
    if ov.get("caption_position"):
        base["position"] = ov["caption_position"]
    return CaptionStyle(**{k: v for k, v in base.items() if k in CaptionStyle.model_fields})


def effective_range(cand: Candidate) -> tuple[float, float, str]:
    """overrides を反映した (start, end, title)。GUI 表示・字幕 cue 取得・書き出しで共通に使う。"""
    ov = cand.overrides or {}
    start = float(ov.get("start_sec", cand.start_sec))
    end = float(ov.get("end_sec", cand.end_sec))
    title = str(ov.get("title") or cand.title)
    return start, end, title


def static_reframe(src_w: int, src_h: int, out_w: int, out_h: int, *, crop_x: float | None = None, mode: str = "center") -> ReframeResult:
    """顔検出なしの固定クロップ。crop_x=0 で左端、0.5 で中央、1 で右端（縦は中央）。"""
    crop_w, crop_h = compute_crop_size(src_w, src_h, out_w, out_h)
    fx = 0.5 if crop_x is None else min(max(float(crop_x), 0.0), 1.0)
    x = int((src_w - crop_w) * fx) // 2 * 2
    y = int((src_h - crop_h) / 2) // 2 * 2
    return ReframeResult(crop_w, crop_h, [CropKeyframe(0.0, x, y)], 0, 0, mode)


def render_candidate(video: Video, creator: Creator, cand: Candidate, transcript: TranscriptData, out_path: Path,
                     *, reframe_style: str | None = None, captions: bool = True, progress=None) -> tuple[dict[str, Any], list[tuple[str, Usage]]]:
    """戻り値: (edit_plan, [(category, usage)...])"""
    s = get_settings()
    vp = get_video_provider()
    vision = get_vision_provider()
    usages: list[tuple[str, Usage]] = []
    ov: dict[str, Any] = dict(cand.overrides or {})
    start, end, title = effective_range(cand)
    # 構図: 候補単位の手直し > 書き出しオプション > Editing DNA > face_track
    style = ov.get("crop_mode") or reframe_style or (creator.editing_dna or {}).get("shorts_policy", {}).get("reframe_style") or "face_track"
    if style not in CROP_MODES:
        style = "face_track"

    if style == "face_track":
        if progress:
            progress("話者追従を計算", 0.05)
        rf = plan_reframe(Path(video.path), start=start, end=end, src_w=video.width, src_h=video.height,
                          out_w=s.output_width, out_h=s.output_height, vision=vision,
                          progress=lambda p: progress and progress("話者追従を計算", 0.05 + 0.25 * p))
        usages.append(("vision", vision.usage_for_frames(rf.frames_analyzed)))
    else:
        # center / manual / blur_fit は固定クロップ。顔検出は回さない（原価ゼロ）
        rf = static_reframe(video.width, video.height, s.output_width, s.output_height,
                            crop_x=ov.get("crop_x") if style == "manual" else None, mode=style)

    ass_path: Path | None = None
    cstyle = caption_style_for(creator, ov)
    cap = get_caption_provider()
    caption_cues: list[dict[str, Any]] = []
    if captions:
        if isinstance(ov.get("captions"), list):
            # 人が直した字幕本文（絶対秒）をそのまま焼く
            ass = cap.build_ass_from_cues(ov["captions"], clip_start=start, clip_end=end, style=cstyle,
                                          out_w=s.output_width, out_h=s.output_height)
            caption_cues = [{"start": round(c["start"] - start, 3), "end": round(c["end"] - start, 3), "text": c["text"]}
                            for c in ov["captions"] if str(c.get("text", "")).strip() and c["end"] > start and c["start"] < end]
        else:
            words = transcript.words_between(start, end)
            ass = cap.build_ass(words, clip_start=start, clip_end=end, style=cstyle, out_w=s.output_width, out_h=s.output_height)
            caption_cues = [{"start": round(c["start"] - start, 3), "end": round(c["end"] - start, 3), "text": c["text"]}
                            for c in cap.split_cues(words, clip_start=start, clip_end=end, style=cstyle)]
        ass_path = out_path.with_suffix(".ass")
        ass_path.write_text(ass, encoding="utf-8")

    if progress:
        progress("書き出し", 0.35)
    usage = vp.render_vertical(
        Path(video.path), out_path, start=start, end=end, crop_w=rf.crop_w, crop_h=rf.crop_h,
        keyframes=rf.keyframes, out_w=s.output_width, out_h=s.output_height, ass_path=ass_path,
        fonts_dir=FONTS_DIR if FONTS_DIR.exists() else None, style="blur_fit" if style == "blur_fit" else "center" if rf.mode != "face_track" else "face_track",
        progress=lambda p: progress and progress("書き出し", 0.35 + 0.6 * p),
    )
    usages.append(("video_processing", usage))
    changed = sorted(ov.keys())
    plan = {
        "candidate_id": cand.id, "source_video_id": video.id, "creator_id": creator.id,
        "title": title,
        "keep_ranges": [[start, end]],
        "reframe_style": rf.mode if style == "face_track" else style,
        "crop": {"w": rf.crop_w, "h": rf.crop_h, "keyframes": [kf.__dict__ for kf in rf.keyframes]},
        "captions": captions, "caption_style": cstyle.model_dump(),
        # 実際に焼いた字幕（クリップ相対秒）。SRT 書き出し・Phase5 の差分用
        "caption_cues": caption_cues,
        # 人の手直し（絶対秒）。空なら AI 案そのまま
        "overrides": ov,
        "decisions": [
            {"id": "cut-0", "action": {"kind": "keep", "params": {}}, "time_start": start, "time_end": end,
             "rule_id": None,
             "reason": f"人が手直し（{', '.join(changed)}）" if changed else "候補区間をそのまま採用（Phase1）",
             "human_status": "modified" if changed else "pending",
             "original": {"time_start": float(cand.start_sec), "time_end": float(cand.end_sec), "title": cand.title} if changed else None},
        ],
        "notes": f"faces_found={rf.faces_found}/{rf.frames_analyzed}",
    }
    return plan, usages
