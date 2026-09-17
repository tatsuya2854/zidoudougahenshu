"""EditPlan → 縦動画 MP4。Phase 1 は「切る・9:16・字幕」だけ。Phase 4 でズーム/SE/BGM が乗る。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import get_settings
from ..models import Candidate, Creator, Video
from ..providers.base import Usage
from ..providers.registry import get_caption_provider, get_video_provider, get_vision_provider
from ..schemas.editing_dna import CaptionStyle
from ..schemas.transcript import TranscriptData
from .reframe import plan_reframe

FONTS_DIR = Path("/usr/share/fonts")


def caption_style_for(creator: Creator) -> CaptionStyle:
    s = get_settings()
    base: dict[str, Any] = {"font": s.caption_font}
    base.update(creator.caption_style or {})
    edna = creator.editing_dna or {}
    for st in edna.get("caption_styles", []) or []:
        if st.get("id") == "default":
            base.update(st)
    return CaptionStyle(**{k: v for k, v in base.items() if k in CaptionStyle.model_fields})


def render_candidate(video: Video, creator: Creator, cand: Candidate, transcript: TranscriptData, out_path: Path,
                     *, reframe_style: str | None = None, captions: bool = True, progress=None) -> tuple[dict[str, Any], list[tuple[str, Usage]]]:
    """戻り値: (edit_plan, [(category, usage)...])"""
    s = get_settings()
    vp = get_video_provider()
    vision = get_vision_provider()
    usages: list[tuple[str, Usage]] = []
    start, end = float(cand.start_sec), float(cand.end_sec)
    style = reframe_style or (creator.editing_dna or {}).get("shorts_policy", {}).get("reframe_style") or "face_track"

    if progress:
        progress("話者追従を計算", 0.05)
    rf = plan_reframe(Path(video.path), start=start, end=end, src_w=video.width, src_h=video.height,
                      out_w=s.output_width, out_h=s.output_height, vision=vision,
                      progress=lambda p: progress and progress("話者追従を計算", 0.05 + 0.25 * p))
    usages.append(("vision", vision.usage_for_frames(rf.frames_analyzed)))

    ass_path: Path | None = None
    cstyle = caption_style_for(creator)
    if captions:
        words = transcript.words_between(start, end)
        ass = get_caption_provider().build_ass(words, clip_start=start, clip_end=end, style=cstyle,
                                               out_w=s.output_width, out_h=s.output_height)
        ass_path = out_path.with_suffix(".ass")
        ass_path.write_text(ass, encoding="utf-8")

    if progress:
        progress("書き出し", 0.35)
    usage = vp.render_vertical(
        Path(video.path), out_path, start=start, end=end, crop_w=rf.crop_w, crop_h=rf.crop_h,
        keyframes=rf.keyframes, out_w=s.output_width, out_h=s.output_height, ass_path=ass_path,
        fonts_dir=FONTS_DIR if FONTS_DIR.exists() else None, style=style if rf.mode == "face_track" or style == "blur_fit" else "center",
        progress=lambda p: progress and progress("書き出し", 0.35 + 0.6 * p),
    )
    usages.append(("video_processing", usage))
    plan = {
        "candidate_id": cand.id, "source_video_id": video.id, "creator_id": creator.id,
        "keep_ranges": [[start, end]],
        "reframe_style": rf.mode if style == "face_track" else style,
        "crop": {"w": rf.crop_w, "h": rf.crop_h, "keyframes": [kf.__dict__ for kf in rf.keyframes]},
        "captions": captions, "caption_style": cstyle.model_dump(),
        "decisions": [
            {"id": "cut-0", "action": {"kind": "keep", "params": {}}, "time_start": start, "time_end": end,
             "rule_id": None, "reason": "候補区間をそのまま採用（Phase1）", "human_status": "pending"},
        ],
        "notes": f"faces_found={rf.faces_found}/{rf.frames_analyzed}",
    }
    return plan, usages
