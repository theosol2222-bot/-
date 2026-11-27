"""Petits wrappers FFmpeg/FFprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from ..detect.signals import loudness_signal, parse_loudness


class FFmpegError(RuntimeError):
    pass


OUTDATED_HINT = ("FFmpeg trop ancien dans le PATH ({path}) : installez une version récente (≥ 6) "
                 "et placez-la avant dans le PATH")


def run(args: list[str], capture: bool = True, timeout: float | None = None) -> str:
    proc = subprocess.run(args, capture_output=capture, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or "")[-800:]
        if "Unrecognized option" in err:
            err += "\n→ " + OUTDATED_HINT.format(path=shutil.which(args[0]))
        raise FFmpegError(f"{args[0]} a échoué ({proc.returncode}) : {err}")
    return proc.stdout or ""


def check_binary(name: str) -> tuple[bool, str]:
    """(ok, détail) : présent dans le PATH et assez récent pour les options utilisées ici."""
    path = shutil.which(name)
    if path is None:
        return False, f"installez {name}"
    try:
        proc = subprocess.run([path, "-hide_banner", "-version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{path} ne démarre pas ({exc})"
    if proc.returncode != 0:
        return False, OUTDATED_HINT.format(path=path)
    return True, (proc.stdout.splitlines() or [""])[0]


def real_duration(fmt: dict) -> float:
    """Durée réelle d'après la section `format` de ffprobe.

    Twitch sert désormais du MP4 fragmenté (même enregistré en .ts par streamlink) : le démultiplexeur
    mov y renvoie une durée qui inclut le décalage de départ (~heures de live) au lieu de la seule
    longueur du fichier. Pour MPEG-TS, ffprobe la retranche déjà.
    """
    duration = float(fmt.get("duration") or 0)
    start = float(fmt.get("start_time") or 0)
    if "mov" in str(fmt.get("format_name", "")) and 0 < start < duration:
        duration -= start
    return duration


def probe(path: Path | str) -> dict:
    data = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration,start_time,format_name:stream=codec_type,width,height,r_frame_rate",
                           "-of", "json", str(path)]))
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    rate = video.get("r_frame_rate", "30/1")
    try:
        n, d = rate.split("/")
        fps = float(n) / float(d or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "duration": real_duration(data.get("format", {})),
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
