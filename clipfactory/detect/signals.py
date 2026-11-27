"""Signaux par seconde et sélection des pics.

Port Python (numpy) de `clip-stream/lib/moments.mjs` (FavStash, Apache-2.0),
adapté au français et enrichi :
- normalisation sans perte des accents (bug d'origine) ;
- lexique FR streamer + chat (mdr, ptdr, jpp, emotes de chaîne…) ;
- signal de répétition (messages identiques / spam d'emotes) ;
- typologie de réaction (rire, choc, hype, malaise) pour expliquer le *pourquoi*.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from . import lexicon


# ---------------------------------------------------------------------------
# Outils numériques
# ---------------------------------------------------------------------------
def local_z(values: np.ndarray, window: int = 60, floor: float = 1.0) -> np.ndarray:
    """Z-score robuste local (médiane / MAD sur `window` secondes autour)."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return values
    half = max(1, window // 2)
    padded = np.pad(values, half, mode="edge")
    win = sliding_window_view(padded, 2 * half + 1)[:n]
    med = np.median(win, axis=1)
    mad = np.median(np.abs(win - med[:, None]), axis=1)
    return (values - med) / np.maximum(floor, 1.4826 * mad)


def smooth(values: np.ndarray, width: int = 3) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) == 0 or width <= 1:
        return values
    kernel = np.ones(width)
    num = np.convolve(values, kernel, mode="same")
    den = np.convolve(np.ones_like(values), kernel, mode="same")
    return num / den


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def parse_loudness(text: str, duration: float) -> np.ndarray:
    """Sortie ffmpeg astats/ametadata (RMS par seconde) → dB par seconde."""
    length = max(1, int(np.ceil(duration)))
    values = [np.nan] * length
    t = None
    for line in text.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"RMS_level=(-?inf|-?[\d.]+)", line, re.I)
        if m and t is not None:
            sec = int(t)
            if sec >= len(values):
                values.extend([np.nan] * (sec + 1 - len(values)))
            values[sec] = -100.0 if "inf" in m.group(1).lower() else max(-100.0, float(m.group(1)))
    out, last = [], -100.0
    for v in values:
        last = v if np.isfinite(v) else last
        out.append(last)
    return np.array(out[:length] if len(out) >= length else out + [last] * (length - len(out)))


def loudness_signal(dbs: np.ndarray, window: int = 60) -> np.ndarray:
    """0–8 : moitié « plus fort que la minute autour », moitié « plus fort que d'habitude »."""
    dbs = np.asarray(dbs, dtype=float)
    local = np.clip(local_z(dbs, window, 3.0), 0, 8)
    usual = np.percentile(dbs, 80) if len(dbs) else 0
    return 0.5 * local + 0.5 * np.clip((dbs - usual) / 2, 0, 8)


# ---------------------------------------------------------------------------
# Parole du streamer
# ---------------------------------------------------------------------------
@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class SpeechResult:
    score: np.ndarray
    hits: dict[int, list[str]] = field(default_factory=dict)
    laughs: int = 0


def speech_signal(words: list[Word], duration: float, keywords: list[str] | None = None,
                  window: int = 60) -> SpeechResult:
    length = max(1, int(np.ceil(duration)))
    score = np.zeros(length)
    rate = np.zeros(length)
    for w in words:
        s = int(w.start)
        if 0 <= s < length:
            rate[s] += 1
    score += np.clip(local_z(rate, window, 1.0), 0, 4) * 0.5

    phrases = dict(lexicon.STREAMER_PHRASES)
    for k in keywords or []:
        if lexicon.normalize(k):
            phrases[lexicon.normalize(k)] = 3.0
    by_len: dict[int, dict[str, float]] = defaultdict(dict)
    for p, wgt in phrases.items():
        by_len[len(p.split())][p] = wgt
    norm = [lexicon.normalize(w.text) for w in words]
    hits: dict[int, list[str]] = defaultdict(list)
    laughs = 0

    def mark(sec: int, label: str, weight: float) -> None:
        for d in range(-1, 3):
            s = sec + d
            if 0 <= s < length:
                score[s] += weight * (1 if d == 0 else 0.5)
        if 0 <= sec < length and label not in hits[sec]:
            hits[sec].append(label)

    for i, w in enumerate(words):
        if lexicon.STREAMER_LAUGH.search(w.text):
            laughs += 1
            mark(int(w.start), "rires", 1.5)
        for n, table in by_len.items():
            if i + n > len(words):
                continue
            # un « mot » Whisper peut contenir plusieurs tokens normalisés
            joined = " ".join(norm[i:i + n])
            for p, wgt in table.items():
                if joined == p or (n == 1 and p in joined.split()):
                    mark(int(w.start), p, wgt)
    return SpeechResult(score=score, hits=dict(hits), laughs=laughs)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@dataclass
class ChatResult:
    score: np.ndarray
    count: np.ndarray
    reactions: dict[str, np.ndarray]
    repeats: np.ndarray
    emotes: np.ndarray


def chat_signal(messages: list[tuple[float, str]], duration: float, delay: float = 6,
                window: int = 120) -> ChatResult:
    """messages = [(t_secondes, texte)] sur la timeline de l'enregistrement."""
    length = max(1, int(np.ceil(duration)))
    count = np.zeros(length)
    repeats = np.zeros(length)
    emotes = np.zeros(length)
    reactions = {r: np.zeros(length) for r in lexicon.REACTIONS}
    recent: deque[tuple[float, str]] = deque()
    recent_counts: Counter[str] = Counter()
    for t, text in sorted(messages):
        while recent and recent[0][0] < t - 10:
            recent_counts[recent.popleft()[1]] -= 1
        key = lexicon.chat_key(text)
        sec = int(t - delay)
        if 0 <= sec < length:
            count[sec] += 1
            if key and recent_counts[key] >= 1:
                repeats[sec] += 1
            emotes[sec] += min(3, lexicon.emote_count(text))
            for r in lexicon.classify_chat(text):
                reactions[r][sec] += 1
        recent.append((t, key))
        recent_counts[key] += 1

    rate_z = local_z(smooth(count, 5), window, 0.5)
    reaction_total = sum(reactions.values())
    s5 = lambda a: smooth(a, 5) * 5  # noqa: E731 - somme glissante ~5 s
    hype_share = np.divide(s5(reaction_total), np.maximum(1, s5(count)))
    score = (np.clip(rate_z, 0, 6)
             + np.minimum(3, s5(reaction_total) * 0.25)
             + 2 * np.clip(hype_share, 0, 1)
             + np.minimum(2, s5(repeats) * 0.2)
             + np.minimum(1.5, s5(reactions["clip"]) * 0.75))
    return ChatResult(score=score, count=count, reactions=reactions, repeats=repeats, emotes=emotes)


def scene_signal(scenes: list[tuple[float, float]], duration: float) -> np.ndarray:
    length = max(1, int(np.ceil(duration)))
    out = np.zeros(length)
    for t, s in scenes:
        if 0 <= int(t) < length:
            out[int(t)] = max(out[int(t)], min(3, s * 4))
    return out


DEFAULT_WEIGHTS = {"loud": 1.0, "speech": 0.8, "chat": 1.1, "scene": 0.3}


def combine(signals: dict[str, np.ndarray | None], weights: dict[str, float] | None = None) -> np.ndarray:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    present = [v for v in signals.values() if v is not None and len(v)]
    length = max((len(v) for v in present), default=1)
    total = np.zeros(length)
    for name, values in signals.items():
        if values is None or not len(values):
            continue
        total[:len(values)] += weights.get(name, 1.0) * np.asarray(values)[:length]
    return smooth(total, 3)


# ---------------------------------------------------------------------------
# Sélection des pics
# ---------------------------------------------------------------------------
@dataclass
class Peak:
    peak: float
    value: float


def pick_peaks(score: np.ndarray, count: int = 5, threshold: float = 1.5, gap: float = 30) -> list[Peak]:
    """Pics non chevauchants (au moins `gap` s d'écart), du plus fort au plus faible."""
    order = np.argsort(-score, kind="stable")
    peaks: list[Peak] = []
    for sec in order:
        value = float(score[sec])
        if len(peaks) >= count or value < threshold:
            break
        if any(abs(sec + 0.5 - p.peak) < gap for p in peaks):
            continue
        peaks.append(Peak(peak=float(sec) + 0.5, value=round(value, 2)))
    return peaks


def peak_in(values: np.ndarray | None, start: float, end: float) -> float:
    if values is None or not len(values):
        return 0.0
    a, b = max(0, int(start)), min(len(values) - 1, int(end))
    return round(float(np.max(values[a:b + 1])), 2) if b >= a else 0.0


def dominant_reaction(chat: ChatResult | None, start: float, end: float, laughs: int = 0) -> str:
    """Catégorie émotionnelle dominante d'une fenêtre (rire, choc, hype, malaise)."""
    totals: Counter[str] = Counter()
    if chat is not None:
        a, b = max(0, int(start)), int(end) + 1
        for r in ("rire", "choc", "hype", "malaise"):
            totals[r] = float(chat.reactions[r][a:b].sum())
    totals["rire"] += laughs * 2
    if not totals or max(totals.values()) < 3:  # quelques « lol » isolés ne font pas une réaction
        return "intense"
    return totals.most_common(1)[0][0]


def snap_window(peak: float, words: list[Word], before: float, after: float,
                lo: float, hi: float) -> tuple[float, float]:
    """Fenêtre autour du pic, bords recalés sur des fins de phrase/pauses."""
    start, end = max(lo, peak - before), min(hi, peak + after)
    if words:
        starts, ends = [], []
        for i, w in enumerate(words):
            prev = words[i - 1] if i else None
            if prev is None or re.search(r"[.!?…]$", prev.text) or w.start - prev.end >= 0.6:
                starts.append(w.start)
            nxt = words[i + 1] if i + 1 < len(words) else None
            if nxt is None or re.search(r"[.!?…]$", w.text) or nxt.start - w.end >= 0.6:
                ends.append(w.end)
        cand = [s for s in starts if start - 4 <= s <= min(start + 3, peak - 2)]
        if cand:
            start = max(lo, min(cand, key=lambda s: abs(s - start)) - 0.15)
        cand = [e for e in ends if max(end - 3, peak + 1) <= e <= end + 4]
        if cand:
            end = min(hi, min(cand, key=lambda e: abs(e - end)) + 0.3)
    return round(start, 2), round(end, 2)
