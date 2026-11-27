"""Test de bout en bout sur le faux live (lent : ~1 min, nécessite Whisper + Piper).

    python tests/fixtures/make_fake_stream.py data/test
    pytest -m e2e
"""
from pathlib import Path

import pytest

from clipfactory.config import ROOT, Settings, Streamer
from clipfactory.db import DB
from clipfactory.media.ffmpeg import probe
from clipfactory.pipeline import process_file

FIX = ROOT / "data" / "test"


@pytest.mark.e2e
@pytest.mark.skipif(not (FIX / "stream.mp4").exists(), reason="lancer tests/fixtures/make_fake_stream.py d'abord")
def test_fake_stream_produces_one_good_clip(tmp_path):
    settings = Settings({"data_dir": str(tmp_path)})
    db = DB(tmp_path / "e2e.db")
    streamer = Streamer("zerator", "ZeratoR")
    process_file(db, FIX / "stream.mp4", streamer, settings, FIX / "chat.jsonl")
    clips = db.query("SELECT * FROM clips ORDER BY score DESC")
    best = clips[0]
    # le vrai moment (cri à 146 s, rires et « clippez ça » jusqu'à ~162 s) est en tête, bien noté, monté
    assert best["start"] <= 146 and best["end"] >= 160 and best["score"] >= 65
    assert best["status"] == "en_attente" and best["layout"] == "split"
    assert all(c["score"] < 55 for c in clips[1:])  # les phrases calmes ne sont pas montées
    info = probe(best["file"])
    assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"]
    assert 20 <= info["duration"] <= 90
    assert Path(best["thumbnail"]).exists()
