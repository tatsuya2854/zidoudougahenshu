"""9:16 リフレーミング: 顔検出 → 追従クロップのキーフレーム列。

方針
- 4fps でサンプリング、最大顔の中心 x を追う。
- EMA で平滑化し、小さな揺れは無視（デッドゾーン）。急な移動（話者交代）はカット的に飛ぶ。
- 顔が取れないフレームは直前の位置を保持。全滅なら中央固定。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from ..providers.base import CropKeyframe, VisionProvider


@dataclass
class ReframeResult:
    crop_w: int
    crop_h: int
    keyframes: list[CropKeyframe]
    frames_analyzed: int
    faces_found: int
    mode: str  # face_track | center


def compute_crop_size(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[int, int]:
    target = out_w / out_h
    if src_w / src_h > target:  # 横長 → 幅を切る
        cw = int(src_h * target) // 2 * 2
        return cw, src_h
    ch = int(src_w / target) // 2 * 2
    return src_w, ch


def plan_reframe(video: Path, *, start: float, end: float, src_w: int, src_h: int, out_w: int, out_h: int,
                 vision: VisionProvider, sample_fps: float = 4.0, progress=None) -> ReframeResult:
    crop_w, crop_h = compute_crop_size(src_w, src_h, out_w, out_h)
    center_x = (src_w - crop_w) // 2
    center_y = (src_h - crop_h) // 2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return ReframeResult(crop_w, crop_h, [CropKeyframe(0.0, center_x, center_y)], 0, 0, "center")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / sample_fps)))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    samples: list[tuple[float, float | None, float | None]] = []
    frames = 0
    faces_found = 0
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t > end:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                frames += 1
                faces = vision.detect_faces(frame)
                if faces:
                    faces_found += 1
                    f = faces[0]
                    samples.append((t - start, f.cx, f.cy))
                else:
                    samples.append((t - start, None, None))
                if progress and end > start:
                    progress(min(0.99, (t - start) / (end - start)))
        idx += 1
    cap.release()
    if faces_found == 0:
        return ReframeResult(crop_w, crop_h, [CropKeyframe(0.0, center_x, center_y)], frames, 0, "center")

    # 平滑化
    kfs: list[CropKeyframe] = []
    ema_x: float | None = None
    ema_y: float | None = None
    last_x, last_y = center_x, center_y
    dead = crop_w * 0.04
    jump = crop_w * 0.35
    alpha = 0.25
    for t, cx, cy in samples:
        if cx is not None:
            if ema_x is None or abs(cx - ema_x) > jump:
                ema_x, ema_y = cx, cy  # 話者交代 → 即座に飛ぶ
            else:
                ema_x = ema_x + alpha * (cx - ema_x)
                ema_y = (ema_y or cy) + alpha * (cy - (ema_y or cy))  # type: ignore[arg-type]
        if ema_x is None:
            continue
        x = int(min(max(ema_x - crop_w / 2, 0), src_w - crop_w))
        # 縦は顔を上 1/3 に置く（縦動画の定石）
        y = int(min(max((ema_y or 0) - crop_h * 0.38, 0), src_h - crop_h)) if crop_h < src_h else 0
        x = x // 2 * 2
        y = y // 2 * 2
        if not kfs or abs(x - last_x) > dead or abs(y - last_y) > dead:
            kfs.append(CropKeyframe(t=round(max(0.0, t), 3), x=x, y=y))
            last_x, last_y = x, y
    if not kfs:
        kfs = [CropKeyframe(0.0, center_x, center_y)]
    if kfs[0].t > 0:
        kfs.insert(0, CropKeyframe(0.0, kfs[0].x, kfs[0].y))
    return ReframeResult(crop_w, crop_h, kfs, frames, faces_found, "face_track")
