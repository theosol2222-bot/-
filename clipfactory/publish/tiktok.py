"""TikTok Content Posting API (officielle) + Display API pour les statistiques.

Deux modes :
- `brouillon_tiktok` : la vidéo arrive dans la boîte de réception TikTok de
  l'appli mobile, vous ajoutez musique/texte et publiez (scope video.upload) ;
- `direct` : publication directe (scope video.publish). Tant que l'application
  n'a pas passé l'audit TikTok, les posts directs sont forcés en privé
  (SELF_ONLY) et limités en nombre d'utilisateurs.

Docs : https://developers.tiktok.com/doc/content-posting-api-get-started
Les jetons sont stockés dans data/secrets/tiktok.json (permissions 600).
"""
from __future__ import annotations

import json
import math
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import httpx

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload,video.publish,video.list"
MIN_CHUNK, MAX_CHUNK = 5 * 1024 * 1024, 64 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


def chunk_plan(size: int, chunk: int = 10 * 1024 * 1024) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) selon les règles TikTok : morceaux de 5 à
    64 Mo, le dernier absorbe le reste ; un fichier < 5 Mo part en un seul morceau."""
    if size <= MIN_CHUNK or size <= chunk:
        return size, 1
    chunk = max(MIN_CHUNK, min(MAX_CHUNK, chunk))
    return chunk, max(1, math.floor(size / chunk))


class TikTokClient:
    def __init__(self, client_key: str, client_secret: str, redirect_uri: str, token_path: Path,
                 http: httpx.Client | None = None):
        if not client_key or not client_secret:
            raise TikTokError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET manquants")
        self.key, self.secret, self.redirect = client_key, client_secret, redirect_uri
        self.token_path = token_path
        self.http = http or httpx.Client(timeout=60)

    # -- OAuth ------------------------------------------------------------
    def authorize_url(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(16)
        q = urllib.parse.urlencode({"client_key": self.key, "scope": SCOPES, "response_type": "code",
                                    "redirect_uri": self.redirect, "state": state})
        return f"{AUTH_URL}?{q}", state

    def _save(self, data: dict) -> None:
        data["obtained_at"] = time.time()
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.token_path)

    def _token_request(self, form: dict) -> dict:
        r = self.http.post(f"{API}/oauth/token/", data={"client_key": self.key, "client_secret": self.secret, **form},
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = r.json()
        if r.status_code != 200 or "access_token" not in data:
            raise TikTokError(f"OAuth TikTok : {data.get('error_description') or data.get('error') or r.status_code}")
        self._save(data)
        return data

    def exchange_code(self, code: str) -> dict:
        return self._token_request({"code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect})

    @property
    def connected(self) -> bool:
        return self.token_path.exists()

    def access_token(self) -> str:
        if not self.token_path.exists():
            raise TikTokError("Compte TikTok non connecté : ouvrez /tiktok/connect dans le tableau de bord")
        data = json.loads(self.token_path.read_text(encoding="utf-8"))
        if time.time() > data["obtained_at"] + float(data.get("expires_in", 86400)) - 600:
            data = self._token_request({"grant_type": "refresh_token", "refresh_token": data["refresh_token"]})
        return data["access_token"]

    def _post(self, path: str, body: dict, params: dict | None = None) -> dict:
        r = self.http.post(f"{API}{path}", json=body, params=params,
                           headers={"Authorization": f"Bearer {self.access_token()}",
                                    "Content-Type": "application/json; charset=UTF-8"})
        data = r.json() if r.content else {}
        err = data.get("error", {})
        if r.status_code != 200 or (err.get("code") not in (None, "ok")):
            raise TikTokError(f"TikTok {path} : {err.get('code')} {err.get('message', '')}".strip())
        return data.get("data", {})

    # -- Publication --------------------------------------------------------
    def creator_info(self) -> dict:
        return self._post("/post/publish/creator_info/query/", {})

    def upload(self, file: Path, title: str, mode: str = "brouillon_tiktok", privacy: str = "SELF_ONLY",
               cover_ms: int = 1000) -> str:
        """Initialise l'envoi, téléverse par morceaux, renvoie le publish_id."""
        size = file.stat().st_size
        chunk, count = chunk_plan(size)
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        if mode == "direct":
            info = self.creator_info()
            options = info.get("privacy_level_options") or ["SELF_ONLY"]
            if privacy not in options:
                privacy = "SELF_ONLY" if "SELF_ONLY" in options else options[0]
            body = {"post_info": {"title": title[:2200], "privacy_level": privacy, "disable_duet": False,
                                  "disable_comment": False, "disable_stitch": False,
                                  "video_cover_timestamp_ms": cover_ms, "brand_content_toggle": False,
                                  "brand_organic_toggle": False},
                    "source_info": source}
            data = self._post("/post/publish/video/init/", body)
        else:
            data = self._post("/post/publish/inbox/video/init/", {"source_info": source})
        upload_url, publish_id = data["upload_url"], data["publish_id"]
        with file.open("rb") as fh:
            for i in range(count):
                start = i * chunk
                length = size - start if i == count - 1 else chunk
                fh.seek(start)
                payload = fh.read(length)
                r = self.http.put(upload_url, content=payload, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(length),
                    "Content-Range": f"bytes {start}-{start + length - 1}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise TikTokError(f"Téléversement morceau {i + 1}/{count} : HTTP {r.status_code}")
        return publish_id

    def status(self, publish_id: str) -> dict:
        return self._post("/post/publish/status/fetch/", {"publish_id": publish_id})

    def video_stats(self, video_ids: list[str]) -> list[dict]:
        if not video_ids:
            return []
        data = self._post("/video/query/", {"filters": {"video_ids": video_ids[:20]}},
                          params={"fields": "id,title,create_time,view_count,like_count,comment_count,share_count"})
        return data.get("videos", [])

    def list_videos(self, max_count: int = 20) -> list[dict]:
        data = self._post("/video/list/", {"max_count": max_count},
                          params={"fields": "id,title,create_time,view_count,like_count,comment_count,share_count"})
        return data.get("videos", [])
