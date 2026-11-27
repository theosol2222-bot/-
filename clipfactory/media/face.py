"""Visage, facecam et cadrage stabilisé pour un recadrage centré sur la réaction.

- Détection : YuNet (zoo officiel OpenCV, Apache-2.0, 230 Ko, livré dans
  `media/models/`), repli sur la cascade de Haar si le modèle est absent.
- Facecam : un visage petit et immobile → layout « split » (cam en haut).
  Grand visage (caméra plein cadre) → « center » avec suivi. Aucun → « fill ».
- Suivi : caméra « trépied lourd » reprise d'openshorts (mutonby/openshorts, MIT) :
  zone morte, un grand saut doit être confirmé plusieurs fois, panoramique lent,
  et recadrage net (sans panoramique) à chaque changement de plan détecté par
  PySceneDetect (Breakthrough/PySceneDetect, BSD-3).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)
YUNET = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
_lock = threading.Lock()
_yunet = None
_cascade = None


def _detectors():
    global _yunet, _cascade
    with _lock:
        if _yunet is None and YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                _yunet = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.75, 0.3, 50)
            except cv2.error as exc:  # modèle illisible : repli Haar
                log.warning("YuNet indisponible (%s), repli sur Haar", exc)
                _yunet = False
        if not _yunet and _cascade is None:
            _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _yunet, _cascade


@dataclass
class FaceInfo:
    layout: str                              # split | center | fill
    cam: tuple[int, int, int, int] | None    # boîte facecam (x, y, w, h) en pixels source
    focus: float                             # 0..1 position horizontale du visage
    face_ratio: float                        # hauteur visage / hauteur image
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
    """Boîtes (x, y, w, h) en pixels de l'image d'origine, plus grande en premier."""
    h, w = frame.shape[:2]
    scale = 640 / w if w > 640 else 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale != 1.0 else frame
    yunet, cascade = _detectors()
    boxes: list[tuple[int, int, int, int]] = []
    if yunet:
        with _lock:
            yunet.setInputSize((small.shape[1], small.shape[0]))
            _n, faces = yunet.detect(small)
        if faces is not None:
            boxes = [tuple(int(v / scale) for v in f[:4]) for f in faces]  # type: ignore[misc]
    else:
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
        boxes = [tuple(int(v / scale) for v in b) for b in found]  # type: ignore[misc]
    boxes = [b for b in boxes if b[2] > 8 and b[3] > 8]
    return sorted(boxes, key=lambda b: -b[2] * b[3])


def scene_cuts(path: Path | str, start: float, end: float, threshold: float = 27.0) -> list[float]:
    """Changements de plan (temps absolus) dans [start, end] via PySceneDetect."""
    try:
        from scenedetect import ContentDetector, FrameTimecode, SceneManager, open_video
    except ImportError:
        return []
    try:
        video = open_video(str(path))
        fps = video.frame_rate
        video.seek(FrameTimecode(start, fps))
        manager = SceneManager()
        manager.auto_downscale = True
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=int(fps)))
        manager.detect_scenes(video, end_time=FrameTimecode(end, fps))
        return [s[0].get_seconds() for s in manager.get_scene_list()[1:]]
    except Exception as exc:  # noqa: BLE001 — la détection de plans est un bonus
        log.info("PySceneDetect ignoré : %s", exc)
        return []


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
            boxes.append(faces[0])
    if len(boxes) < max(2, len(frames) // 4):
        return FaceInfo("fill", None, 0.5, 0.0, len(boxes), len(frames))
    arr = np.array(boxes, dtype=float)
    med = np.median(arr, axis=0)
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
    cam_h = min(H, h * 3.0)
    cam_w = min(W, cam_h * 1.6)
    cam_x = float(np.clip(cx - cam_w / 2, 0, W - cam_w))
    cam_y = float(np.clip(cy - cam_h * 0.45, 0, H - cam_h))
    box = tuple(int(round(v / 2) * 2) for v in (cam_x, cam_y, cam_w, cam_h))
    return FaceInfo("split", box, focus, round(ratio, 3), len(boxes), len(frames))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Caméra stabilisée (d'après SmoothedCameraman d'openshorts)
# ---------------------------------------------------------------------------
class TripodCamera:
    """Centre horizontal de recadrage, en pixels source.

    - Zone morte : tant que le visage reste à moins de `dead_zone` du centre, on ne bouge pas.
    - Un grand saut doit être vu `confirm` fois d'affilée (sinon faux positif ignoré).
    - Panoramique lent (`pan_speed` px/s), plus rapide si l'écart est énorme.
    - `cut()` : au changement de plan, la prochaine détection est cadrée net.
    """

    def __init__(self, crop_width: float, video_width: float, confirm: int = 3):
        self.crop_w, self.video_w = crop_width, video_width
        self.dead_zone = crop_width * 0.25
        self.confirm = confirm
        self.current = self.target = video_width / 2
        self._pending: float | None = None
        self._count = 0
        self._snap = True

    def cut(self) -> None:
        self._pending, self._count, self._snap = None, 0, True

    def observe(self, center: float | None) -> None:
        if center is None:
            return
        if self._snap:
            self.current = self.target = center
            self._snap, self._pending, self._count = False, None, 0
            return
        if abs(center - self.target) > self.dead_zone:
            if self._pending is not None and abs(center - self._pending) <= self.dead_zone:
                self._count += 1
            else:
                self._pending, self._count = center, 1
            if self._count < self.confirm:
                return
        self._pending, self._count = None, 0
        self.target = center

    def step(self, dt: float) -> float:
        diff = self.target - self.current
        if abs(diff) > self.dead_zone * 0.2:
            speed = (450.0 if abs(diff) > self.crop_w * 0.5 else 90.0) * dt
            self.current += max(-speed, min(speed, diff))
        half = self.crop_w / 2
        self.current = min(max(self.current, half), self.video_w - half)
        return self.current


def simplify(points: list[tuple[float, float]], tol: float = 4.0) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker : garde les points où la trajectoire change vraiment."""
    if len(points) < 3:
        return points
    (t0, x0), (t1, x1) = points[0], points[-1]
    best, idx = 0.0, 0
    for i, (t, x) in enumerate(points[1:-1], 1):
        expected = x0 + (x1 - x0) * (t - t0) / max(1e-9, t1 - t0)
        if abs(x - expected) > best:
            best, idx = abs(x - expected), i
    if best <= tol:
        return [points[0], points[-1]]
    return simplify(points[:idx + 1], tol)[:-1] + simplify(points[idx:], tol)


def track(path: Path | str, start: float, end: float, crop_w_src: float, video_w: int,
          cuts: list[float] | None = None, fps: float = 4.0) -> list[tuple[float, float]]:
    """Trajectoire (t_source, centre_x_source) simplifiée, échantillonnée à `fps` images/s."""
    times = list(np.arange(start, end, 1.0 / fps))
    cam = TripodCamera(crop_w_src, video_w)
    cuts = sorted(cuts or [])
    traj = []
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    ci = 0
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        while ci < len(cuts) and cuts[ci] <= t:
            cam.cut()
            ci += 1
        if ok and frame is not None:
            faces = detect_faces(frame)
            cam.observe(faces[0][0] + faces[0][2] / 2 if faces else None)
        traj.append((t, cam.step(1.0 / fps)))
    cap.release()
    return simplify(traj)
