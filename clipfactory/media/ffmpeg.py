"""Petits wrappers FFmpeg/FFprobe."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from ..detect.signals import loudness_signal, parse_loudness


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], capture: bool = True, timeout: float | None = None) -> str:
    proc = subprocess.run(args, capture_output=capture, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(f"{args[0]} a échoué ({proc.returncode}) : {(proc.stderr or '')[-800:]}")
    return proc.stdout or ""


def probe(path: Path | str) -> dict:
    data = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration:stream=codec_type,width,height,r_frame_rate",
                           "-of", "json", str(path)]))
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    rate = video.get("r_frame_rate", "30/1")
    try:
        n, d = rate.split("/")
        fps = float(n) / float(d or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def loudness(path: Path | str, start: float, duration: float) -> np.ndarray:
    """dB RMS par seconde sur [start, start+duration] (audio seul : rapide)."""
    text = run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{duration:.2f}",
                "-i", str(path), "-vn", "-af",
                "aresample=16000,asetnsamples=16000,astats=metadata=1:reset=1,"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
                "-f", "null", "-"])
    return parse_loudness(text, duration)


def loudness_score(path: Path | str, start: float, duration: float) -> np.ndarray:
    return loudness_signal(loudness(path, start, duration))


def extract_wav(path: Path | str, out: Path, start: float, duration: float) -> Path:
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{duration:.2f}",
         "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)])
    return out


def grab_frame(path: Path | str, t: float, out: Path, width: int | None = None) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path),
         "-frames:v", "1", *vf, "-q:v", "3", str(out)])
    return out
