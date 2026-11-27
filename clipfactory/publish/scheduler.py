"""Anti-doublons et planification des publications (espacement, quotas)."""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

from ..config import Settings
from ..db import DB
from ..detect.lexicon import normalize

STOP = set("le la les un une des de du et a à en que qui je tu il on nous vous ils c'est ca ça est pas ne se sa son "
           "mais ou donc or ni car y au aux ce cet cette mon ma mes ton ta tes".split())


def fingerprint(words: list[str]) -> str:
    """Empreinte textuelle (mots significatifs triés) pour repérer un même moment."""
    toks = {normalize(w) for w in words}
    return " ".join(sorted(t for t in toks if t and t not in STOP and len(t) > 2))[:2000]


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def is_duplicate(db: DB, fp: str, stream_id: int | None, start: float, end: float, hours: float = 48) -> bool:
    """Même stream et chevauchement > 50 %, ou même transcription (autre POV / re-détection)."""
    if stream_id is not None:
        for c in db.query("SELECT start, end FROM clips WHERE stream_id = ? AND status != 'doublon'", [stream_id]):
            inter = min(end, c["end"]) - max(start, c["start"])
            if inter > 0.5 * min(end - start, c["end"] - c["start"]):
                return True
    if len(fp.split()) < 6:
        return False
    since = time.time() - hours * 3600
    for c in db.query("SELECT fingerprint FROM clips WHERE created_at > ? AND status != 'doublon' AND fingerprint IS NOT NULL", [since]):
        if jaccard(fp, c["fingerprint"]) >= 0.6:
            return True
    return False


def next_slot(db: DB, settings: Settings, login: str, now: float | None = None) -> float:
    """Prochain créneau libre : respecte l'écart minimal, le quota quotidien et
    l'espacement entre deux clips du même streamer. Créneaux d'audience FR."""
    tz = ZoneInfo(str(settings.get("publish.timezone", "Europe/Paris")))
    now = now or time.time()
    gap = float(settings.get("publish.min_gap_minutes", 90)) * 60
    same_gap = float(settings.get("publish.same_streamer_gap_hours", 4)) * 3600
    per_day = int(settings.get("publish.max_per_day", 6))
    slots = sorted(settings.get("publish.slots", ["12:15", "18:00", "21:00"]))
    taken = db.query("SELECT p.scheduled_at, c.login FROM publications p JOIN clips c ON c.id = p.clip_id "
                     "WHERE p.status IN ('programme', 'publie', 'envoi') AND p.scheduled_at > ?", [now - 86400 * 2])
    start_day = dt.datetime.fromtimestamp(now, tz).date()
    for day in range(0, 30):
        date = start_day + dt.timedelta(days=day)
        day_start = dt.datetime(date.year, date.month, date.day, tzinfo=tz).timestamp()
        count = sum(1 for p in taken if day_start <= p["scheduled_at"] < day_start + 86400)
        if count >= per_day:
            continue
        for s in slots:
            h, m = (int(x) for x in s.split(":"))
            ts = dt.datetime(date.year, date.month, date.day, h, m, tzinfo=tz).timestamp()
            if ts < now + 600:
                continue
            if any(abs(p["scheduled_at"] - ts) < gap for p in taken):
                continue
            if any(p["login"] == login and abs(p["scheduled_at"] - ts) < same_gap for p in taken):
                continue
            return ts
    return now + 86400
