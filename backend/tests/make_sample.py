"""テスト用の合成動画（16:9, 音声に発話っぽい区間と無音を混ぜる）。"""
from __future__ import annotations

import subprocess
from pathlib import Path


def make_sample(dst: Path, seconds: int = 100) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 音声: 3秒鳴って 1秒無音 を繰り返す
    audio = f"sine=frequency=440:duration={seconds},volume='if(lt(mod(t,4),3),1,0)':eval=frame"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", audio,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(dst),
    ], check=True)
    return dst


if __name__ == "__main__":
    import sys

    print(make_sample(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 100))
