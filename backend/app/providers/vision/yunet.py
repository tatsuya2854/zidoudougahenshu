"""顔検出: OpenCV YuNet（Apache-2.0、ローカル、無料）。

9:16 リフレーミングの「話者追従」に使う。Phase 2 以降は表情/テンション推定にも拡張。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..base import Face, VisionProvider


class YuNetVision(VisionProvider):
    name = "yunet"

    def __init__(self, model_path: Path, score_threshold: float = 0.7) -> None:
        self._model_path = str(model_path)
        self._det = cv2.FaceDetectorYN.create(self._model_path, "", (320, 320), score_threshold, 0.3, 5000)
        self._size = (320, 320)

    def detect_faces(self, frame_bgr: np.ndarray) -> list[Face]:
        h, w = frame_bgr.shape[:2]
        # 速度のため長辺 640 に縮小して検出し、座標を戻す
        scale = min(1.0, 640.0 / max(h, w))
        small = cv2.resize(frame_bgr, (int(w * scale), int(h * scale))) if scale < 1.0 else frame_bgr
        sh, sw = small.shape[:2]
        if (sw, sh) != self._size:
            self._det.setInputSize((sw, sh))
            self._size = (sw, sh)
        _, faces = self._det.detect(small)
        out: list[Face] = []
        if faces is None:
            return out
        for f in faces:
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
            out.append(Face(x=x / scale, y=y / scale, w=fw / scale, h=fh / scale, score=score))
        out.sort(key=lambda f: f.w * f.h, reverse=True)
        return out


class NoVision(VisionProvider):
    name = "none"

    def detect_faces(self, frame_bgr: np.ndarray) -> list[Face]:
        return []
