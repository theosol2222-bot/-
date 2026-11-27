"""Lecture du chat Twitch en anonyme (lecture seule, documenté par Twitch).

Une seule connexion WebSocket pour toutes les chaînes. Aucun pseudo n'est
conservé : seulement l'heure d'arrivée et le texte, le temps de l'analyse.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from typing import Callable

log = logging.getLogger(__name__)
WS_URL = "wss://irc-ws.chat.twitch.tv:443"


def parse_privmsg(line: str) -> tuple[str, str] | None:
    """`:nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :texte` → (chan, texte)."""
    at = line.find(" PRIVMSG #")
    if at < 0:
        return None
    rest = line[at + 10:]
    chan, sep, text = rest.partition(" :")
    if not sep:
        return None
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    return chan.strip().lower(), text


class ChatHub:
    """Garde en mémoire les messages récents (wall, texte) par chaîne."""

    def __init__(self, keep_seconds: float = 4 * 3600, on_message: Callable[[str, float, str], None] | None = None):
        self.channels: set[str] = set()
        self.messages: dict[str, deque[tuple[float, str]]] = defaultdict(deque)
        self.keep = keep_seconds
        self.on_message = on_message
        self._ws = None
        self._stop = asyncio.Event()
        self.connected = False

    def window(self, channel: str, wall_from: float, wall_to: float) -> list[tuple[float, str]]:
        return [(w, t) for w, t in self.messages.get(channel, ()) if wall_from <= w <= wall_to]

    def rate(self, channel: str, seconds: float = 60) -> float:
        now = time.time()
        return sum(1 for w, _ in reversed(self.messages.get(channel, ())) if now - w <= seconds) / seconds * 60

    async def join(self, channel: str) -> None:
        channel = channel.lower()
        if channel in self.channels:
            return
        self.channels.add(channel)
        if self._ws is not None:
            await self._ws.send(f"JOIN #{channel}")

    async def part(self, channel: str) -> None:
        channel = channel.lower()
        self.channels.discard(channel)
        self.messages.pop(channel, None)
        if self._ws is not None:
            await self._ws.send(f"PART #{channel}")

    def _store(self, chan: str, text: str) -> None:
        now = time.time()
        q = self.messages[chan]
        q.append((now, text))
        while q and now - q[0][0] > self.keep:
            q.popleft()
        if self.on_message:
            self.on_message(chan, now, text)

    async def run(self) -> None:
        import websockets

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(WS_URL, ping_interval=60, max_size=2 ** 20) as ws:
                    self._ws = ws
                    await ws.send("PASS SCHMOOPIIE")  # mot de passe anonyme documenté par Twitch
                    await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                    for chan in list(self.channels):
                        await ws.send(f"JOIN #{chan}")
                    self.connected, backoff = True, 1.0
                    log.info("Chat Twitch connecté (%d chaînes)", len(self.channels))
                    async for data in ws:
                        for line in str(data).split("\r\n"):
                            if line.startswith("PING"):
                                await ws.send("PONG :tmi.twitch.tv")
                            elif " RECONNECT" in line:
                                raise ConnectionError("RECONNECT demandé par Twitch")
                            else:
                                parsed = parse_privmsg(line)
                                if parsed and parsed[0] in self.channels:
                                    self._store(*parsed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — on se reconnecte toujours
                log.warning("Chat Twitch déconnecté (%s), reconnexion dans %.0fs", exc, backoff)
            finally:
                self._ws, self.connected = None, False
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    def stop(self) -> None:
        self._stop.set()
