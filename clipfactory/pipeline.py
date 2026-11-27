"""Pipeline d'un passage d'analyse : fenêtre d'enregistrement → clips montés.

Économie : les signaux bon marché (volume audio + chat) tournent sur tout le
live ; Whisper ne transcrit que les fenêtres candidates ; Claude ne lit que les
candidats qui ont passé le filtre ; FFmpeg ne monte que ce qui dépasse le seuil.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Settings, Streamer
from .db import DB
from .detect import signals as sig
from .detect.analyzer import Analysis, Candidate, ClaudeAnalyzer, heuristic_analysis, viral_score
from .detect.lexicon import normalize
from .media import ffmpeg
from .media.render import render_clip
from .media.transcribe import transcribe
from .publish.metadata import build_metadata
from .publish.scheduler import fingerprint, is_duplicate

log = logging.getLogger(__name__)


@dataclass
class StreamContext:
    stream_id: int | None
    streamer: Streamer
    game: str = ""
    title: str = ""
    all_streamers: list[Streamer] | None = None


def _mentions(words: list[sig.Word], chat: list[tuple[float, str]], ctx: StreamContext) -> list[str]:
    names = {}
    for s in ctx.all_streamers or []:
        if s.login != ctx.streamer.login:
            for alias in {normalize(s.nom), normalize(s.login)}:
                if len(alias) >= 4:
                    names[alias] = s.nom
    if not names:
        return []
    text = " " + " ".join(normalize(w.text) for w in words) + " " + " ".join(normalize(t) for _, t in chat) + " "
    return sorted({nom for alias, nom in names.items() if f" {alias} " in text})


def find_candidates(source: Path | str, t_from: float, t_to: float, chat: list[tuple[float, str]],
                    ctx: StreamContext, settings: Settings) -> tuple[list[sig.Peak], dict]:
    """Signaux bon marché sur [t_from, t_to] → pics candidats (temps absolus)."""
    span = t_to - t_from
    loud = ffmpeg.loudness_score(source, t_from, span)
    in_range = [(t - t_from, x) for t, x in chat if t_from <= t < t_to + 30]
    chat_res = sig.chat_signal(in_range, span, delay=float(settings.get("analysis.chat_delay", 6))) if in_range else None
    combined = sig.combine({"loud": loud, "chat": chat_res.score if chat_res else None})
    peaks = sig.pick_peaks(combined, count=int(settings.get("analysis.candidates_per_pass", 4)),
                           threshold=float(settings.get("analysis.min_signal", 1.5)), gap=40)
    for p in peaks:
        p.peak += t_from
    return peaks, {"loud": loud, "chat": chat_res, "combined": combined, "from": t_from}


def analyze_peak(source: Path | str, peak: sig.Peak, chat: list[tuple[float, str]], ctx: StreamContext,
                 settings: Settings, cheap: dict, analyzer: ClaudeAnalyzer | None,
                 recorded_until: float) -> tuple[Candidate, Analysis, int]:
    before = float(settings.get("analysis.context_before", 60))
    after = float(settings.get("analysis.context_after", 25))
    w_start = max(0.0, peak.peak - before)
    w_end = min(recorded_until, peak.peak + after)
    words = transcribe(source, w_start, w_end - w_start, model=str(settings.get("analysis.whisper_model", "small")),
                       prompt=f"Live Twitch de {ctx.streamer.nom}. Langage familier, rires, mdr.")
    speech = sig.speech_signal([sig.Word(w.text, w.start - w_start, w.end - w_start) for w in words],
                               w_end - w_start, keywords=ctx.streamer.mots_cles)
    rel = peak.peak - w_start
    phrases: list[str] = []
    for s in range(int(rel - 20), int(rel + 10)):
        for p in speech.hits.get(s, []):
            if p not in phrases:
                phrases.append(p)
    off = cheap["from"]
    chat_res = cheap["chat"]
    signals = {
        "loud": sig.peak_in(cheap["loud"], peak.peak - off - 5, peak.peak - off + 5),
        "chat": sig.peak_in(chat_res.score if chat_res else None, peak.peak - off - 5, peak.peak - off + 8),
        "speech": sig.peak_in(speech.score, rel - 8, rel + 5),
    }
    reaction = sig.dominant_reaction(chat_res, peak.peak - off - 5, peak.peak - off + 10, speech.laughs)
    window_chat = [(t, x) for t, x in chat if w_start <= t <= w_end + 10]
    cand = Candidate(
        login=ctx.streamer.login, streamer=ctx.streamer.nom, peak=peak.peak, window_start=w_start, window_end=w_end,
        signal=peak.value + 0.8 * signals["speech"], signals=signals, reaction=reaction, phrases=phrases,
        laughs=speech.laughs, words=words, chat_lines=window_chat, game=ctx.game, stream_title=ctx.title,
    )
    cand.mentions = _mentions(words, window_chat, ctx)
    # Pré-filtre gratuit : Claude ne lit que les candidats que l'heuristique juge plausibles.
    analysis = heuristic_analysis(cand)
    prefilter = int(settings.get("llm.min_prefilter_score", 40))
    if analyzer is not None and viral_score(cand, analysis) >= prefilter:
        analysis = analyzer.analyze(cand) or analysis
    # bornes de durée
    min_d = float(settings.get("render.min_duration", 20))
    max_d = float(settings.get("render.max_duration", 90))
    if analysis.end - analysis.start < min_d:
        pad = (min_d - (analysis.end - analysis.start)) / 2
        analysis.start = max(w_start, analysis.start - pad * 1.4)
        analysis.end = min(w_end, analysis.start + min_d)
    if analysis.end - analysis.start > max_d:
        analysis.end = analysis.start + max_d
    score = viral_score(cand, analysis, min_d, max_d)
    analysis.viral_score = score
    return cand, analysis, score


def process_window(db: DB, source: Path | str, t_from: float, t_to: float, chat: list[tuple[float, str]],
                   ctx: StreamContext, settings: Settings, analyzer: ClaudeAnalyzer | None = None,
                   recorded_until: float | None = None, render: bool = True, final: bool = True) -> list[int]:
    """Analyse une fenêtre et enregistre/monte les clips. Retourne les ids de clips créés.

    `final=False` (live en cours) : un pic trop proche de la fin de l'enregistrement
    est laissé à la passe suivante, pour ne pas couper la chute du moment."""
    recorded_until = recorded_until or t_to
    after = float(settings.get("analysis.context_after", 25))
    peaks, cheap = find_candidates(source, t_from, t_to, chat, ctx, settings)
    log.info("%s : %d pic(s) entre %.0f et %.0f s", ctx.streamer.login, len(peaks), t_from, t_to)
    created: list[int] = []
    for peak in peaks:
        if not final and peak.peak + after > recorded_until - 2:
            log.info("%s : pic à %.0fs reporté à la passe suivante (chute pas encore enregistrée)", ctx.streamer.login, peak.peak)
            continue
        if ctx.stream_id is not None and db.one(
                "SELECT id FROM clips WHERE stream_id = ? AND ? BETWEEN start - 5 AND end + 5", [ctx.stream_id, peak.peak]):
            continue  # déjà couvert par un clip (passe précédente)
        cand, analysis, score = analyze_peak(source, peak, chat, ctx, settings, cheap, analyzer, recorded_until)
        words_in = [w for w in cand.words if analysis.start <= w.start <= analysis.end]
        fp = fingerprint([w.text for w in words_in])
        dup = is_duplicate(db, fp, ctx.stream_id, analysis.start, analysis.end)
        meta = build_metadata(ctx.streamer, analysis, ctx.game, settings)
        status = "doublon" if dup else ("candidat" if score >= int(settings.get("analysis.min_viral_score", 55)) else "faible")
        if status == "candidat" and not analysis.interesting:
            status = "faible"
        clip_id = db.insert("clips", {
            "stream_id": ctx.stream_id, "login": ctx.streamer.login, "start": analysis.start, "end": analysis.end,
            "peak": cand.peak, "created_at": time.time(), "status": status, "score": score,
            "category": analysis.category, "reason": analysis.why, "quote": " ".join(w.text for w in words_in)[:600],
            "title": meta["title"], "hook": analysis.hook, "description": meta["description"],
            "hashtags": meta["hashtags"], "credit": meta["credit"], "best_time": meta["best_time"],
            "signals": {**cand.signals, "combined": peak.value, "reaction": cand.reaction, "phrases": cand.phrases,
                        "mentions": cand.mentions},
            "analysis": {"engine": analysis.engine, "standalone": analysis.standalone, "risks": analysis.risks,
                         "content_score": round(analysis.content_score, 2),
                         "words": [[w.text, w.start, w.end] for w in cand.words]},
            "fingerprint": fp, "duration": round(analysis.end - analysis.start, 2),
        })
        db.log(f"{ctx.streamer.nom} : moment {status} (score {score}) à {cand.peak:.0f}s — {analysis.why[:160]}")
        created.append(clip_id)
        if render and status == "candidat":
            render_stored_clip(db, clip_id, source, settings, ctx.streamer)
    return created


def clip_dir(settings: Settings, login: str) -> Path:
    d = settings.data_dir / "clips" / time.strftime("%Y-%m-%d") / login
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_stored_clip(db: DB, clip_id: int, source: Path | str, settings: Settings, streamer: Streamer) -> None:
    clip = db.one("SELECT * FROM clips WHERE id = ?", [clip_id])
    if not clip:
        return
    words = [sig.Word(t, s, e) for t, s, e in (clip["analysis"] or {}).get("words", [])]
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(clip["title"] or "clip"))[:40].strip("-") or "clip"
    out = clip_dir(settings, clip["login"]) / f"{clip_id:05d}-{slug}.mp4"
    try:
        res = render_clip(source, clip["start"], clip["end"], words, out, hook=clip["hook"],
                          credit=clip["credit"], title=clip["hook"] or clip["title"], peak=clip["peak"],
                          settings=settings, layout=streamer.layout, facecam=streamer.facecam)
    except Exception as exc:  # noqa: BLE001 — on garde le clip en erreur, le live continue
        log.exception("Rendu du clip %s impossible", clip_id)
        db.update("clips", clip_id, {"status": "erreur", "error": str(exc)[-500:]})
        return
    db.update("clips", clip_id, {
        "status": "en_attente", "file": str(res.file), "thumbnail": str(res.thumbnail) if res.thumbnail else None,
        "duration": round(res.duration, 2), "layout": res.layout,
    })
    db.log(f"Clip {clip_id} monté ({res.layout}, {res.duration:.0f}s, {res.removed_seconds:.1f}s de temps morts retirés)")


def load_chat_file(path: Path | str) -> list[tuple[float, str]]:
    """chat.jsonl ([{t,text}] par ligne) ou JSON [{t|content_offset_seconds, text|message}]."""
    import json

    raw = Path(path).read_text(encoding="utf-8").strip()
    items = json.loads(raw) if raw.startswith("[") else [json.loads(l) for l in raw.splitlines() if l.strip()]
    out = []
    for m in items:
        t = m.get("t", m.get("content_offset_seconds", m.get("time")))
        msg = m.get("message")
        text = m.get("text") or (msg.get("body") if isinstance(msg, dict) else msg)
        if t is not None and text:
            out.append((float(t), str(text)))
    return sorted(out)


def process_file(db: DB, source: Path | str, streamer: Streamer, settings: Settings, chat_path: Path | None = None,
                 analyzer: ClaudeAnalyzer | None = None, all_streamers: list[Streamer] | None = None,
                 window: float = 600) -> list[int]:
    """Traite un fichier complet (VOD autorisée, enregistrement local) par fenêtres."""
    info = ffmpeg.probe(source)
    chat = load_chat_file(chat_path) if chat_path else []
    stream_id = db.insert("streams", {"login": streamer.login, "twitch_id": f"file:{Path(source).name}:{time.time():.0f}",
                                      "title": Path(source).stem, "started_at": time.time(), "status": "fichier",
                                      "record_path": str(source), "recorded_seconds": info["duration"]})
    ctx = StreamContext(stream_id, streamer, all_streamers=all_streamers)
    created: list[int] = []
    t = 0.0
    while t < info["duration"] - 5:
        t_to = min(info["duration"], t + window)
        created += process_window(db, source, t, t_to, chat, ctx, settings, analyzer, info["duration"],
                                  final=t_to >= info["duration"])
        t = t_to - float(settings.get("analysis.overlap_seconds", 90)) if t_to < info["duration"] else t_to
    db.update("streams", stream_id, {"status": "termine", "ended_at": time.time()})
    return created


def np_tolist(a) -> list:
    return a.tolist() if isinstance(a, np.ndarray) else list(a)
