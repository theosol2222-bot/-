"""Démon 24/7 : surveille les streamers, enregistre les lives, analyse en continu.

    python -m clipfactory monitor

Boucles asyncio :
- sondage Helix (qui est en live ?) → un StreamWorker par live ;
- chat Twitch (une connexion pour toutes les chaînes) ;
- publication des clips arrivés à échéance + statistiques TikTok ;
- statistiques de chat pour le tableau de bord ; nettoyage du disque.
Le travail lourd (Whisper, FFmpeg) passe par un pool de threads borné.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Settings, Streamer, load_settings, load_streamers, secret
from .db import DB
from .detect.analyzer import ClaudeAnalyzer
from .detect.lexicon import chat_key, classify_chat
from .pipeline import StreamContext, load_chat_file, process_window
from .publish import workflow
from .twitch.chat import ChatHub
from .twitch.helix import Helix, HelixError, LiveInfo
from .twitch.recorder import Recorder

log = logging.getLogger(__name__)


def next_window(last_to: float, recorded: float, overlap: float = 90, margin: float = 8, fresh: float = 60,
                minimum: float = 120) -> tuple[float, float] | None:
    to = recorded - margin
    frm = max(0.0, last_to - overlap)
    return (frm, to) if to - last_to >= fresh and to - frm >= minimum else None


class ReplayRecorder:
    """Rejoue un fichier comme un live (tests, démo) : la durée « enregistrée »
    avance avec le temps × vitesse, le chat est lu depuis un fichier."""

    def __init__(self, video: Path, chat: Path | None, speed: float = 1.0):
        from .media.ffmpeg import probe

        self.video = video
        self.total = probe(video)["duration"]
        self.chat = load_chat_file(chat) if chat else []
        self.speed = speed
        self.t0 = 0.0

    async def start(self) -> None:
        self.t0 = time.time()

    @property
    def recorded(self) -> float:
        return min(self.total, (time.time() - self.t0) * self.speed)

    @property
    def running(self) -> bool:
        return self.recorded < self.total

    def chat_on_timeline(self, _messages) -> list[tuple[float, str]]:
        return [(t, x) for t, x in self.chat if t <= self.recorded]

    async def stop(self) -> None:
        return None


def replay_chat_buckets(chat: list[tuple[float, str]], t0: float, frm: float, to: float) -> list[tuple]:
    """Statistiques de chat par tranche de 10 s pour un replay (le live réel passe par le ChatHub)."""
    buckets: dict[int, list[int]] = {}
    for t, text in chat:
        if frm <= t < to:
            b = buckets.setdefault(int(t // 10) * 10, [0, 0, 0])
            b[0] += 1
            b[1] += 1 if classify_chat(text) else 0
    return [(float(b), t0 + b, *v) for b, v in sorted(buckets.items())]


class StreamWorker:
    def __init__(self, mon: "Monitor", streamer: Streamer, live: LiveInfo, recorder):
        self.mon, self.streamer, self.live, self.recorder = mon, streamer, live, recorder
        self.stream_id: int | None = None
        self.stop_event = asyncio.Event()
        self.last_to = 0.0
        self.passes = 0

    async def run(self) -> None:
        db, s = self.mon.db, self.mon.settings
        row = db.one("SELECT id FROM streams WHERE login = ? AND twitch_id = ?", [self.streamer.login, self.live.stream_id])
        self.stream_id = row["id"] if row else db.insert("streams", {
            "login": self.streamer.login, "twitch_id": self.live.stream_id, "title": self.live.title,
            "game": self.live.game, "started_at": time.time(), "status": "live", "viewers": self.live.viewers,
            "viewers_peak": self.live.viewers, "record_path": str(self.recorder.video)})
        db.update("streams", self.stream_id, {"status": "live", "ended_at": None})
        db.log(f"🔴 {self.streamer.nom} est en live : {self.live.title} ({self.live.game})")
        await self.recorder.start()
        await self.mon.hub.join(self.streamer.login)
        every = float(s.get("analysis.pass_seconds", 300))
        try:
            while True:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=min(every, 30 if self.passes == 0 else every))
                except asyncio.TimeoutError:
                    pass
                final = self.stop_event.is_set() or not self.recorder.running
                await self.analysis_pass(final)
                db.update("streams", self.stream_id, {"recorded_seconds": round(self.recorder.recorded, 1)})
                if final:
                    break
        finally:
            await self.recorder.stop()
            await self.mon.hub.part(self.streamer.login)
            db.update("streams", self.stream_id, {"status": "termine", "ended_at": time.time()})
            db.log(f"⚫ Fin du live de {self.streamer.nom} ({self.passes} passes d'analyse)")

    async def analysis_pass(self, final: bool = False) -> None:
        s = self.mon.settings
        recorded = self.recorder.recorded
        win = next_window(self.last_to, recorded, float(s.get("analysis.overlap_seconds", 90)),
                          **({"margin": 1, "fresh": 10, "minimum": 30} if final else {}))
        if not win:
            return
        chat = self.recorder.chat_on_timeline(list(self.mon.hub.messages.get(self.streamer.login, ())))
        if isinstance(self.recorder, ReplayRecorder) and self.stream_id is not None:
            self.mon.db.add_chat_stats(self.stream_id, replay_chat_buckets(chat, self.recorder.t0, self.last_to, win[1]))
        ctx = StreamContext(self.stream_id, self.streamer, self.live.game, self.live.title, self.mon.streamers)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self.mon.pool, lambda: process_window(
                self.mon.db, self.recorder.video, win[0], win[1], chat, ctx, s, self.mon.analyzer, recorded, final=final))
        except Exception as exc:  # noqa: BLE001 — une passe ratée ne doit pas tuer le worker
            log.exception("Passe d'analyse %s échouée", self.streamer.login)
            self.mon.db.log(f"Passe d'analyse {self.streamer.nom} échouée : {exc}", "error")
        self.last_to = win[1]
        self.passes += 1


class Monitor:
    def __init__(self, settings: Settings | None = None, db: DB | None = None):
        self.settings = settings or load_settings()
        self.db = db or DB(self.settings.data_dir / "clipfactory.db")
        self.streamers: list[Streamer] = load_streamers()
        self.hub = ChatHub()
        self.workers: dict[str, tuple[StreamWorker, asyncio.Task]] = {}
        self.pool = ThreadPoolExecutor(max_workers=int(self.settings.get("render.concurrency", 1)),
                                       thread_name_prefix="analyse")
        self.analyzer = self._make_analyzer()
        self.helix: Helix | None = None
        self._chat_mark: dict[str, float] = {}

    def _make_analyzer(self) -> ClaudeAnalyzer | None:
        if not self.settings.get("llm.enabled", True) or not secret("ANTHROPIC_API_KEY"):
            log.info("Analyse Claude désactivée (pas de clé) : moteur heuristique")
            return None
        return ClaudeAnalyzer(model=str(self.settings.get("llm.model", "claude-opus-5-5")),
                              effort=str(self.settings.get("llm.effort", "low")),
                              max_calls_per_hour=int(self.settings.get("llm.max_calls_per_hour", 30)),
                              cost_hook=lambda usd, detail: self.db.add_cost("llm", usd, detail))

    # -- lives ---------------------------------------------------------------
    def start_worker(self, streamer: Streamer, live: LiveInfo, recorder=None) -> StreamWorker:
        out = self.settings.data_dir / "streams" / f"{streamer.login}-{time.strftime('%Y%m%d-%H%M')}"
        recorder = recorder or Recorder(streamer.login, out, str(self.settings.get("recording.quality", "best")))
        worker = StreamWorker(self, streamer, live, recorder)
        task = asyncio.create_task(worker.run(), name=f"worker-{streamer.login}")
        self.workers[streamer.login] = (worker, task)
        task.add_done_callback(lambda _t, login=streamer.login: self.workers.pop(login, None))
        return worker

    async def poll_lives(self) -> None:
        self.streamers = load_streamers()  # la liste est modifiable à chaud
        active = [s for s in self.streamers if s.actif]
        if self.helix is None:
            if secret("TWITCH_CLIENT_ID") and secret("TWITCH_CLIENT_SECRET"):
                self.helix = Helix(secret("TWITCH_CLIENT_ID") or "", secret("TWITCH_CLIENT_SECRET") or "")
            else:
                from .twitch.livecheck import StreamlinkLiveChecker

                self.db.log("Identifiants Twitch absents : détection des lives via streamlink (mode dégradé)", "warning")
                self.helix = StreamlinkLiveChecker()  # type: ignore[assignment]
        lives = await asyncio.to_thread(self.helix.live, [s.login for s in active])
        self.db.kv_set("lives", {k: v.__dict__ for k, v in lives.items()})
        self.db.kv_set("last_poll", time.time())
        for login, info in lives.items():
            if login in self.workers:
                row = self.workers[login][0].stream_id
                if row:
                    self.db.execute("UPDATE streams SET viewers = ?, viewers_peak = MAX(viewers_peak, ?) WHERE id = ?",
                                    [info.viewers, info.viewers, row])
                continue
            if len(self.workers) >= int(self.settings.get("monitor.max_parallel_streams", 4)):
                self.db.log(f"{login} en live mais limite de lives simultanés atteinte", "warning")
                continue
            streamer = next(s for s in active if s.login == login)
            self.start_worker(streamer, info)
        for login, (worker, _task) in list(self.workers.items()):
            if login not in lives and not isinstance(worker.recorder, ReplayRecorder):
                worker.stop_event.set()

    async def poll_loop(self) -> None:
        while True:
            try:
                await self.poll_lives()
            except HelixError as exc:
                self.db.log(f"Twitch : {exc}", "error")
                log.error("Twitch : %s", exc)
            await asyncio.sleep(float(self.settings.get("monitor.poll_seconds", 60)))

    # -- tableau de bord : activité du chat ----------------------------------
    def flush_chat_stats(self) -> None:
        for login, (worker, _t) in list(self.workers.items()):
            if worker.stream_id is None:
                continue
            since = self._chat_mark.get(login, time.time() - 60)
            now = time.time()
            buckets: dict[int, list[int]] = {}
            seen: Counter[str] = Counter()
            for wall, text in self.hub.messages.get(login, ()):
                if wall < since - 10:
                    continue
                key = chat_key(text)
                if wall >= since:
                    b = buckets.setdefault(int(wall // 10) * 10, [0, 0, 0])
                    b[0] += 1
                    b[1] += 1 if classify_chat(text) else 0
                    b[2] += 1 if seen[key] else 0
                seen[key] += 1
            rows = [(float(b), float(b), *v) for b, v in buckets.items()]
            if rows:
                self.db.add_chat_stats(worker.stream_id, rows)
            self._chat_mark[login] = now

    async def housekeeping_loop(self) -> None:
        client = workflow.tiktok_client(self.settings)
        tick = 0
        while True:
            try:
                self.flush_chat_stats()
                await asyncio.to_thread(workflow.publish_due, self.db, self.settings, client)
                if tick % 60 == 0:
                    await asyncio.to_thread(workflow.refresh_status_and_stats, self.db, client)
                    self.cleanup()
            except Exception:  # noqa: BLE001
                log.exception("Tâche de fond en erreur")
            tick += 1
            await asyncio.sleep(30)

    def cleanup(self) -> None:
        """Supprime les enregistrements bruts des lives terminés depuis > keep_raw_hours."""
        limit = time.time() - float(self.settings.get("recording.keep_raw_hours", 6)) * 3600
        for st in self.db.query("SELECT id, record_path FROM streams WHERE status = 'termine' AND ended_at < ? "
                                "AND record_path IS NOT NULL", [limit]):
            path = Path(st["record_path"])
            streams_root = self.settings.data_dir / "streams"
            if path.exists() and streams_root in path.parents:
                shutil.rmtree(path.parent, ignore_errors=True)
                self.db.log(f"Enregistrement brut supprimé : {path.parent.name}")
            self.db.update("streams", st["id"], {"record_path": None})

    async def run(self) -> None:
        self.db.log("Moniteur démarré")
        await asyncio.gather(self.hub.run(), self.poll_loop(), self.housekeeping_loop())


async def replay(video: Path, chat: Path | None, login: str, speed: float = 8.0, settings: Settings | None = None,
                 db: DB | None = None) -> int:
    """Rejoue un fichier comme un live et exécute la vraie boucle du worker."""
    mon = Monitor(settings, db)
    mon.settings.data["analysis"]["pass_seconds"] = max(5.0, 300 / speed)
    streamer = next((s for s in mon.streamers if s.login == login), Streamer(login=login, nom=login))
    rec = ReplayRecorder(video, chat, speed)
    live = LiveInfo(login, f"replay-{int(time.time())}", f"Replay {video.name}", "Replay", 0, "")
    worker = mon.start_worker(streamer, live, rec)
    await mon.workers[login][1]
    return worker.stream_id or 0
