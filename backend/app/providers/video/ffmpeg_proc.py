"""ffmpeg / ffprobe アダプタ（ローカル、LGPL ビルド前提）。"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ..base import CropKeyframe, MediaInfo, Usage, VideoProcessingProvider


def _run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True, check=False)


class FFmpegProcessor(VideoProcessingProvider):
    name = "ffmpeg"

    def probe(self, path: Path) -> MediaInfo:
        r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
        if r.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {r.stderr[-400:]}")
        info = json.loads(r.stdout)
        v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
        a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
        fps = 0.0
        if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
            n, d = v["avg_frame_rate"].split("/")
            fps = float(n) / float(d) if float(d) else 0.0
        dur = float(info.get("format", {}).get("duration") or (v or {}).get("duration") or 0.0)
        return MediaInfo(
            duration=dur, width=int((v or {}).get("width", 0)), height=int((v or {}).get("height", 0)), fps=fps,
            size_bytes=int(info.get("format", {}).get("size", 0) or 0), has_audio=a is not None,
        )

    def extract_audio(self, src: Path, dst: Path, *, sample_rate: int = 16000) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        codec = ["-c:a", "libmp3lame", "-b:a", "48k"] if dst.suffix == ".mp3" else ["-c:a", "pcm_s16le"]
        r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), *codec, str(dst)])
        if r.returncode != 0:
            raise RuntimeError(f"audio extract failed: {r.stderr[-400:]}")
        return dst

    def extract_frame(self, src: Path, t: float, dst: Path, *, width: int = 640) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                  "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)])
        if r.returncode != 0:
            raise RuntimeError(f"frame extract failed: {r.stderr[-400:]}")
        return dst

    def detect_silences(self, audio: Path, *, noise_db: float = -35.0, min_sec: float = 0.5) -> list[tuple[float, float]]:
        r = _run(["ffmpeg", "-v", "info", "-i", str(audio), "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"])
        starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
        ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
        out: list[tuple[float, float]] = []
        for i, s in enumerate(starts):
            e = ends[i] if i < len(ends) else None
            if e is None:
                break
            out.append((s, e))
        return out

    def render_vertical(self, src, dst, *, start, end, crop_w, crop_h, keyframes, out_w, out_h, ass_path, fonts_dir,
                        style="face_track", progress=None) -> Usage:
        dst.parent.mkdir(parents=True, exist_ok=True)
        work = dst.parent / f".{dst.stem}_work"
        work.mkdir(exist_ok=True)
        filters: list[str] = []
        if style == "blur_fit":
            # 全体を縦に収め、背景はボカした拡大映像
            filters.append(
                f"[0:v]split=2[bg][fg];[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},boxblur=luma_radius=30:luma_power=2[bgb];"
                f"[fg]scale={out_w}:-2[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            x0 = keyframes[0].x if keyframes else 0
            y0 = keyframes[0].y if keyframes else 0
            chain = ""
            if len(keyframes) > 1:
                cmd_file = work / "crop_cmds.txt"
                lines = []
                for kf in keyframes:
                    lines.append(f"{kf.t:.3f} [enter] crop x {kf.x}, crop y {kf.y};")
                cmd_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                chain += f"sendcmd=f='{_esc(cmd_file)}',"
            chain += f"crop={crop_w}:{crop_h}:{x0}:{y0},scale={out_w}:{out_h}:flags=lanczos"
            filters.append(f"[0:v]{chain}")
        vf = filters[0]
        if ass_path is not None:
            fonts = f":fontsdir='{_esc(fonts_dir)}'" if fonts_dir else ""
            vf += f",subtitles='{_esc(ass_path)}'{fonts}"
        vf += "[vout]"
        cmd = [
            "ffmpeg", "-v", "error", "-y", "-progress", "pipe:1", "-nostats",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
            "-filter_complex", vf, "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(dst),
        ]
        t0 = time.time()
        total = max(end - start, 0.01)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_us=") and progress:
                try:
                    done = int(line.split("=")[1]) / 1_000_000
                    progress(min(0.99, done / total))
                except ValueError:
                    pass
        _, err = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg render failed: {err[-800:]}")
        return Usage(provider=self.name, model="libx264", quantities={"cpu_minutes": (time.time() - t0) / 60.0})


def _esc(p: Path | str) -> str:
    """ffmpeg filter 引数用エスケープ（: と \\ と '）。"""
    s = str(p)
    return s.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
