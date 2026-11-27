import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

import httpx
import pytest

from clipfactory.config import Settings, Streamer
from clipfactory.db import DB
from clipfactory.publish import workflow
from clipfactory.publish.scheduler import fingerprint, is_duplicate, jaccard, next_slot
from clipfactory.publish.tiktok import TikTokClient, chunk_plan

PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")


@pytest.fixture
def settings(tmp_path):
    return Settings({"data_dir": str(tmp_path), "publish": {"slots": ["12:15", "19:30", "21:15"], "min_gap_minutes": 90,
                                                            "max_per_day": 2, "same_streamer_gap_hours": 4, "mode": "manuel"}})


def _clip(db, tmp_path, login="zerator", **kw):
    f = tmp_path / f"{login}-{time.time()}.mp4"
    f.write_bytes(b"x" * 100)
    return db.insert("clips", {"login": login, "status": "en_attente", "file": str(f), "title": "t", "description": "d",
                               "created_at": time.time(), "start": 0, "end": 30, **kw})


def test_next_slot_respects_quota_and_streamer_gap(db, settings, tmp_path):
    now = dt.datetime(2026, 11, 27, 9, 0, tzinfo=PARIS).timestamp()
    streamers = {"zerator": Streamer("zerator", "ZeratoR", autorisation="programme_officiel"),
                 "gotaga": Streamer("gotaga", "Gotaga", autorisation="accord_ecrit")}
    times = []
    for login in ("zerator", "zerator", "gotaga"):
        cid = _clip(db, tmp_path, login)
        ts = next_slot(db, settings, login, now)
        workflow.approve(db, settings, cid, streamers, ts)
        times.append(dt.datetime.fromtimestamp(ts, PARIS).strftime("%d %H:%M"))
    # 2 par jour max, 4 h entre deux clips du même streamer
    assert times == ["27 12:15", "27 19:30", "28 12:15"]


def test_approve_blocked_without_authorization(db, settings, tmp_path):
    cid = _clip(db, tmp_path)
    with pytest.raises(workflow.PublishBlocked):
        workflow.approve(db, settings, cid, {"zerator": Streamer("zerator", "ZeratoR")})


def test_duplicates(db):
    a = fingerprint("Non c'est pas possible vous avez vu ce saut depuis la tour, il atterrit direct dans la rivière incroyable".split())
    b = fingerprint("c'est pas possible vous avez vu ce saut depuis la tour il atterrit dans la rivière".split())
    assert jaccard(a, b) > 0.6
    db.insert("clips", {"stream_id": 1, "start": 100, "end": 140, "fingerprint": a, "created_at": time.time(), "status": "en_attente"})
    assert is_duplicate(db, "x", 1, 110, 145)            # chevauchement dans le même live
    assert is_duplicate(db, b, 2, 0, 30)                 # même moment, autre POV
    assert not is_duplicate(db, fingerprint("rien à voir avec le reste du tout".split()), 2, 0, 30)


def test_publish_due_manual_mode(db, settings, tmp_path):
    cid = _clip(db, tmp_path)
    db.insert("publications", {"clip_id": cid, "scheduled_at": time.time() - 5, "status": "programme", "mode": "manuel"})
    assert workflow.publish_due(db, settings, None) == 1
    assert db.one("SELECT status FROM publications")["status"] == "a_publier"


def test_chunk_plan():
    assert chunk_plan(3_000_000) == (3_000_000, 1)
    size, n = chunk_plan(47 * 1024 * 1024)
    assert size == 10 * 1024 * 1024 and n == 4  # le dernier morceau absorbe le reste (17 Mo)


def test_tiktok_direct_upload_flow(tmp_path):
    token = tmp_path / "tk.json"
    token.write_text(json.dumps({"access_token": "A", "refresh_token": "R", "expires_in": 86400, "obtained_at": time.time()}))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 1000)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.url.path.endswith("/video/init/"):
            body = json.loads(req.content)
            assert body["post_info"]["privacy_level"] == "SELF_ONLY"
            assert body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000, "total_chunk_count": 1}
            return httpx.Response(200, json={"data": {"publish_id": "p1", "upload_url": "https://upload.example/u"},
                                             "error": {"code": "ok"}})
        if req.method == "PUT":
            assert req.headers["Content-Range"] == "bytes 0-999/1000"
            return httpx.Response(201)
        raise AssertionError(req.url)

    c = TikTokClient("k", "s", "http://localhost/cb", token, httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.upload(video, "titre", mode="direct", privacy="PUBLIC_TO_EVERYONE") == "p1"
    assert ("PUT", "/u") in seen


def test_tiktok_authorize_url():
    c = TikTokClient("key", "s", "http://localhost:8080/tiktok/callback", __import__("pathlib").Path("/nonexistent"))
    url, state = c.authorize_url()
    assert "client_key=key" in url and "video.publish" in url and state in url
