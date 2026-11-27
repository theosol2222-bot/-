"""Enregistrement d'un live avec streamlink + correspondance temps réel ↔ vidéo.

streamlink retire les publicités Twitch ; on lit son journal debug pour savoir,
pour chaque segment écrit, sa position dans le fichier et son heure de
diffusion (EXT-X-PROGRAM-DATE-TIME). Le chat (heure d'arrivée) est ainsi placé
au bon endroit de la vidéo, même après une coupure pub. Port de
`clip-stream/lib/live.mjs` (FavStash, Apache-2.0), vérifié sur streamlink 8.
"""
from __future__ import annotations

import asyncio
import bisect
import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

QUEUED = re.compile(r"Queuing \w*HLSSegment\(num=(\d+),.*?duration=([\d.]+),.*?date=([0-9T:.\-+Z]+|None)")
WRITTEN = re.compile(r"Writing segment (\d+) to output")


@dataclass
class Segment:
    num: int
    pos: float          # début dans le fichier (s)
    dur: float
    date: float | None  # heure de diffusion (epoch)


class SegmentTracker:
    def __init__(self) -> None:
        self.queued: dict[int, tuple[float, float | None]] = {}
        self.segments: list[Segment] = []
        self.pos = 0.0

    def feed(self, line: str) -> Segment | None:
        q = QUEUED.search(line)
        if q:
            date = None
            if q.group(3) != "None":
                try:
                    date = dt.datetime.fromisoformat(q.group(3).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    date = None
            self.queued[int(q.group(1))] = (float(q.group(2)), date)
            return None
        w = WRITTEN.search(line)
        if not w:
            return None
        num = int(w.group(1))
        dur, date = self.queued.pop(num, (2.0, None))
        seg = Segment(num, round(self.pos, 3), dur, date)
        self.pos += dur
        self.segments.append(seg)
        return seg

    @property
    def recorded(self) -> float:
        return self.pos


def map_wall_to_recording(messages: list[tuple[float, str]], segments: list[Segment], start_wall: float | None = None,
                          gap_tolerance: float = 20) -> list[tuple[float, str]]:
    """[(heure_arrivée, texte)] → [(t_dans_la_vidéo, texte)]. Messages pendant une pub : ignorés."""
    dated = [s for s in segments if s.date is not None]
    dates = [s.date for s in dated]
    out = []
    for wall, text in messages:
        if dated:
            k = bisect.bisect_right(dates, wall) - 1
            if k < 0:
                continue
            s = dated[k]
            off = wall - s.date  # type: ignore[operator]
            if off > s.dur + gap_tolerance:
                continue
            out.append((round(s.pos + off, 2), text))
        elif start_wall is not None and wall >= start_wall:
            out.append((round(wall - start_wall, 2), text))
    return out


class Recorder:
    """Lance streamlink en tâche de fond et suit la timeline."""

    def __init__(self, login: str, out_dir: Path, quality: str = "720p60,720p,best", streamlink: str = "streamlink"):
        self.login = login
        self.out_dir = out_dir
        self.video = out_dir / "stream.ts"
        self.quality = quality
        self.bin = streamlink
        self.tracker = SegmentTracker()
        self.proc: asyncio.subprocess.Process | None = None
        self.start_wall: float | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.proc = await asyncio.create_subprocess_exec(
            self.bin, "--loglevel", "debug", "--retry-streams", "10", "--retry-max", "30", "--retry-open", "5",
            "-o", str(self.video), "-f", f"twitch.tv/{self.login}", self.quality,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        self._task = asyncio.create_task(self._read())
        log.info("Enregistrement de twitch.tv/%s → %s", self.login, self.video)

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        seg_log = (self.out_dir / "segments.jsonl").open("a", encoding="utf-8")
        try:
            async for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                seg = self.tracker.feed(line)
                if seg:
                    if self.start_wall is None:
                        self.start_wall = time.time() - seg.dur
                    seg_log.write(json.dumps(seg.__dict__) + "\n")
                elif "[cli][info]" in line or "error" in line.lower():
                    log.info("[streamlink %s] %s", self.login, line[:200])
        finally:
            seg_log.close()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    @property
    def recorded(self) -> float:
        return self.tracker.recorded

    def chat_on_timeline(self, messages: list[tuple[float, str]]) -> list[tuple[float, str]]:
        return map_wall_to_recording(messages, self.tracker.segments, self.start_wall)

    async def stop(self) -> None:
        if self.running and self.proc:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 15)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
