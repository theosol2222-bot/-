"""Détection du visage / de la facecam pour un recadrage centré sur la réaction.

OpenCV (cascade de Haar livrée avec opencv, aucun modèle à télécharger). On
échantillonne quelques images ; une facecam est un visage qui reste au même
endroit, petit par rapport à l'image → layout « split ». Un grand visage
(just chatting, caméra plein écran) → layout « center » qui suit le visage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)
_cascade = None


def _detector():
    global _cascade
    if _cascade is None:
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _cascade


@dataclass
class FaceInfo:
    layout: str                      # split | center | fill
    cam: tuple[int, int, int, int] | None  # boîte facecam (x, y, w, h) en pixels source
    focus: float                     # 0..1 position horizontale du visage
    face_ratio: float                # hauteur visage / hauteur image
    detections: int
    samples: int


def sample_frames(path: Path | str, times: list[float]) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    frames = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append(frame)
    cap.release()
    return frames


def detect_faces(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = frame.shape[:2]
    scale = 640 / w if w > 640 else 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    found = _detector().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
    return [tuple(int(v / scale) for v in box) for box in found]  # type: ignore[misc]


def analyze(path: Path | str, start: float, end: float, samples: int = 12) -> FaceInfo:
    times = list(np.linspace(start + 0.5, max(start + 0.5, end - 0.5), samples))
    frames = sample_frames(path, times)
    if not frames:
        return FaceInfo("fill", None, 0.5, 0.0, 0, 0)
    H, W = frames[0].shape[:2]
    boxes = []
    for f in frames:
        faces = detect_faces(f)
        if faces:
            boxes.append(max(faces, key=lambda b: b[2] * b[3]))  # le plus grand visage
    if len(boxes) < max(2, len(frames) // 4):
        return FaceInfo("fill", None, 0.5, 0.0, len(boxes), len(frames))
    arr = np.array(boxes, dtype=float)
    med = np.median(arr, axis=0)
    # garder les détections proches de la médiane (élimine les faux positifs du jeu)
    keep = arr[np.all(np.abs(arr - med) <= np.maximum(40, med[2:4].repeat(2) * 0.6), axis=1)]
    if len(keep) < 2:
        keep = arr
    x, y, w, h = np.median(keep, axis=0)
    cx, cy = x + w / 2, y + h / 2
    ratio = h / H
    spread = float(np.std(keep[:, 0]) + np.std(keep[:, 1]))
    focus = float(np.clip(cx / W, 0, 1))
    if ratio >= 0.22:   # grand visage : caméra plein cadre
        return FaceInfo("center", None, focus, round(ratio, 3), len(boxes), len(frames))
    if spread > W * 0.08:  # visage qui bouge partout : sans doute dans le jeu
        return FaceInfo("fill", None, 0.5, round(ratio, 3), len(boxes), len(frames))
    # facecam : étendre la boîte du visage à un cadre ~16:10 autour de la tête
    cam_h = min(H, h * 3.0)
    cam_w = min(W, cam_h * 1.6)
    cam_x = float(np.clip(cx - cam_w / 2, 0, W - cam_w))
    cam_y = float(np.clip(cy - cam_h * 0.45, 0, H - cam_h))
    box = tuple(int(round(v / 2) * 2) for v in (cam_x, cam_y, cam_w, cam_h))
    return FaceInfo("split", box, focus, round(ratio, 3), len(boxes), len(frames))  # type: ignore[arg-type]
