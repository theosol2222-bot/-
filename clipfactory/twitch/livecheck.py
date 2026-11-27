"""Détection de live sans identifiants Twitch, via streamlink (repli).

Moins riche que Helix (ni titre, ni jeu, ni spectateurs) et une requête par
chaîne : à n'utiliser qu'en attendant les identifiants de l'application Twitch.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from .helix import LiveInfo

log = logging.getLogger(__name__)


class StreamlinkLiveChecker:
    def __init__(self, workers: int = 4):
        import streamlink

        self.session = streamlink.Streamlink()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="livecheck")

    def _check(self, login: str) -> LiveInfo | None:
        try:
            streams = self.session.streams(f"https://twitch.tv/{login}")
        except Exception as exc:  # noqa: BLE001 — une chaîne en erreur ne bloque pas les autres
            log.debug("livecheck %s : %s", login, exc)
            return None
        if not streams:
            return None
        return LiveInfo(login=login, stream_id=f"sl-{login}-{time.strftime('%Y%m%d')}", title="", game="",
                        viewers=0, started_at="")

    def live(self, logins: list[str]) -> dict[str, LiveInfo]:
        results = self.pool.map(self._check, logins)
        return {info.login: info for info in results if info}
