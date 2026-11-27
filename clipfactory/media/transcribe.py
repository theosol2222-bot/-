"""Transcription française horodatée au mot (faster-whisper, CPU int8, local et gratuit).

On ne transcrit que les fenêtres candidates (≈ 1 min 30 autour d'un pic), pas le
live entier : c'est ce qui rend le système économique sur un petit serveur.
"""
from __future__ import annotations

import logging
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

from ..detect.signals import Word
from .ffmpeg import extract_wav

log = logging.getLogger(__name__)
_models: dict[str, object] = {}
_lock = threading.Lock()


def get_model(name: str = "small"):
    from faster_whisper import WhisperModel

    with _lock:
        if name not in _models:
            log.info("Chargement du modèle Whisper %s…", name)
            _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


def transcribe(source: Path | str, start: float, duration: float, model: str = "small",
               prompt: str | None = None) -> list[Word]:
    """Mots avec temps ABSOLUS dans `source`."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_wav(source, Path(tmp) / "a.wav", start, duration)
        with wave.open(str(wav), "rb") as fh:  # PCM 16 bits mono 16 kHz → float32 (évite PyAV)
            audio = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = get_model(model).transcribe(
            audio, language="fr", word_timestamps=True, vad_filter=True, beam_size=3,
            initial_prompt=prompt, condition_on_previous_text=False,
        )
        words: list[Word] = []
        with _lock:  # le modèle CTranslate2 est partagé : une transcription à la fois
            for seg in segments:
                for w in seg.words or []:
                    text = w.word.strip()
                    if not text:
                        continue
                    # élisions / ponctuation détachées par Whisper : « C » + « 'est » → « C'est »
                    if words and (text[0] in "'’-" or text in {".", ",", "!", "?", "…", "...", "?!"}
                                  or (words[-1].text.endswith(("'", "’")) and not w.word.startswith(" "))):
                        prev = words[-1]
                        words[-1] = Word(text=prev.text + text, start=prev.start, end=round(start + w.end, 3))
                    else:
                        words.append(Word(text=text, start=round(start + w.start, 3), end=round(start + w.end, 3)))
    return words
