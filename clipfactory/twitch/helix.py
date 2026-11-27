"""API officielle Twitch Helix : qui est en live ? (jeton d'application, gratuit).

Une seule requête toutes les 60 s couvre jusqu'à 100 chaînes : très loin des
limites (800 points/min). https://dev.twitch.tv/docs/api/reference/#get-streams
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.twitch.tv/helix"


@dataclass
class LiveInfo:
    login: str
    stream_id: str
    title: str
    game: str
    viewers: int
    started_at: str


class HelixError(RuntimeError):
    pass


class Helix:
    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        if not client_id or not client_secret:
            raise HelixError("TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET sont requis (https://dev.twitch.tv/console/apps)")
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http or httpx.Client(timeout=15)
        self._token: str | None = None
        self._expires = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires - 300:
            return self._token
        r = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                            "grant_type": "client_credentials"})
        if r.status_code != 200:
            raise HelixError(f"Jeton Twitch refusé ({r.status_code}) : vérifiez les identifiants")
        data = r.json()
        self._token = data["access_token"]
        self._expires = time.time() + float(data.get("expires_in", 3600))
        return self._token

    def _get(self, path: str, params: list[tuple[str, str]]) -> dict:
        for attempt in range(2):
            r = self.http.get(f"{API}/{path}", params=params,
                              headers={"Client-Id": self.client_id, "Authorization": f"Bearer {self.token()}"})
            if r.status_code == 401 and attempt == 0:
                self._token = None  # jeton expiré/révoqué : on en redemande un
                continue
            if r.status_code == 429:
                raise HelixError("Limite de débit Twitch atteinte")
            if r.status_code != 200:
                raise HelixError(f"Helix {path} : HTTP {r.status_code}")
            return r.json()
        raise HelixError("Authentification Twitch impossible")

    def live(self, logins: list[str]) -> dict[str, LiveInfo]:
        out: dict[str, LiveInfo] = {}
        for i in range(0, len(logins), 100):
            data = self._get("streams", [("user_login", l) for l in logins[i:i + 100]] + [("first", "100")])
            for s in data.get("data", []):
                if s.get("type") == "live":
                    out[s["user_login"].lower()] = LiveInfo(
                        login=s["user_login"].lower(), stream_id=s["id"], title=s.get("title", ""),
                        game=s.get("game_name", ""), viewers=int(s.get("viewer_count", 0)), started_at=s.get("started_at", ""))
        return out

    def users(self, logins: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(logins), 100):
            data = self._get("users", [("login", l) for l in logins[i:i + 100]])
            for u in data.get("data", []):
                out[u["login"].lower()] = u
        return out
