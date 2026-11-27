"""Titre, description, hashtags, crédit et heure de publication suggérée."""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

from ..config import Settings, Streamer
from ..detect.lexicon import normalize

CATEGORY_TAGS = {
    "drole": ["drole", "humour", "fou rire"], "choc": ["choc", "incroyable"], "exploit": ["clutch", "masterclass"],
    "fail": ["fail"], "rage": ["rage"], "emotion": ["emotion"], "clash": ["clash"],
    "interaction": ["duo"], "insolite": ["insolite", "wtf"], "malaise": ["malaise", "genant"], "autre": [],
}
BASE_TAGS = ["twitchfr", "streamerfr", "clip", "pourtoi"]


def _tag(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text))


def hashtags_for(streamer: Streamer, category: str, game: str, extra: list[str]) -> list[str]:
    tags = [_tag(streamer.nom), *(_tag(t) for t in extra), *(_tag(t) for t in CATEGORY_TAGS.get(category, []))]
    if game:
        tags.append(_tag(game))
    tags += BASE_TAGS
    seen, out = set(), []
    for t in tags:
        if t and t not in seen and len(t) <= 30:
            seen.add(t)
            out.append(t)
    return out[:8]


def best_time(settings: Settings, now: dt.datetime | None = None) -> str:
    """Prochain créneau d'audience FR (heure de Paris) au moins 20 min dans le futur."""
    tz = ZoneInfo(str(settings.get("publish.timezone", "Europe/Paris")))
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    slots = sorted(settings.get("publish.slots", ["12:15", "18:00", "21:00"]))
    for day in range(0, 3):
        date = (now + dt.timedelta(days=day)).date()
        for s in slots:
            h, m = (int(x) for x in s.split(":"))
            cand = dt.datetime(date.year, date.month, date.day, h, m, tzinfo=tz)
            if cand >= now + dt.timedelta(minutes=20):
                return cand.isoformat(timespec="minutes")
    return (now + dt.timedelta(hours=2)).isoformat(timespec="minutes")


def build_metadata(streamer: Streamer, analysis, game: str, settings: Settings) -> dict:
    credit = f"twitch.tv/{streamer.login}"
    title = analysis.title or analysis.hook
    if streamer.nom.lower() not in title.lower():
        title = f"{streamer.nom} — {title}"
    tags = hashtags_for(streamer, analysis.category, game, analysis.hashtags)
    desc = analysis.description.strip()
    if credit not in desc:
        desc = f"{desc}\n🎥 Live de {streamer.nom} : {credit}".strip()
    if streamer.can_publish and streamer.preuve:
        desc += "\nClip publié dans le cadre de son programme de clipping."
    desc += "\n" + " ".join(f"#{t}" for t in tags)
    return {"title": title[:100], "description": desc[:2000], "hashtags": tags, "credit": credit,
            "best_time": best_time(settings)}
