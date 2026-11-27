"""Validation → programmation → publication. Partagé par le tableau de bord et le moniteur."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import Settings, Streamer, secret
from ..db import DB
from .scheduler import next_slot
from .tiktok import TikTokClient, TikTokError

log = logging.getLogger(__name__)


class PublishBlocked(Exception):
    pass


def tiktok_client(settings: Settings) -> TikTokClient | None:
    key, sec = secret("TIKTOK_CLIENT_KEY"), secret("TIKTOK_CLIENT_SECRET")
    if not key or not sec:
        return None
    return TikTokClient(key, sec, secret("TIKTOK_REDIRECT_URI") or "http://localhost:8080/tiktok/callback",
                        settings.data_dir / "secrets" / "tiktok.json")


def approve(db: DB, settings: Settings, clip_id: int, streamers: dict[str, Streamer], when: float | None = None) -> int:
    """Valide un clip et le programme au prochain créneau libre."""
    clip = db.one("SELECT * FROM clips WHERE id = ?", [clip_id])
    if not clip or not clip.get("file"):
        raise PublishBlocked("Clip introuvable ou pas encore monté")
    st = streamers.get(clip["login"])
    if not st or not st.can_publish:
        raise PublishBlocked(f"Publication bloquée : aucune autorisation enregistrée pour {clip['login']} "
                             "(champ `autorisation` dans config/streamers.yaml).")
    if db.one("SELECT id FROM publications WHERE clip_id = ? AND status != 'annule'", [clip_id]):
        raise PublishBlocked("Ce clip est déjà programmé")
    ts = when or next_slot(db, settings, clip["login"])
    pub_id = db.insert("publications", {"clip_id": clip_id, "scheduled_at": ts, "status": "programme",
                                        "mode": settings.get("publish.mode", "manuel")})
    db.update("clips", clip_id, {"status": "programme"})
    db.log(f"Clip {clip_id} programmé le {time.strftime('%d/%m %H:%M', time.localtime(ts))}")
    return pub_id


def reject(db: DB, clip_id: int) -> None:
    db.update("clips", clip_id, {"status": "rejete"})
    db.execute("UPDATE publications SET status = 'annule' WHERE clip_id = ? AND status = 'programme'", [clip_id])


def publish_due(db: DB, settings: Settings, client: TikTokClient | None, now: float | None = None) -> int:
    """Traite les publications arrivées à échéance. Retourne le nombre traité."""
    now = now or time.time()
    done = 0
    for pub in db.query("SELECT * FROM publications WHERE status = 'programme' AND scheduled_at <= ?", [now]):
        clip = db.one("SELECT * FROM clips WHERE id = ?", [pub["clip_id"]])
        if not clip:
            continue
        if pub["mode"] == "manuel" or client is None or not client.connected:
            db.update("publications", pub["id"], {"status": "a_publier"})
            db.log(f"Clip {clip['id']} prêt à publier manuellement (télécharger depuis le tableau de bord)")
            done += 1
            continue
        try:
            db.update("publications", pub["id"], {"status": "envoi"})
            caption = f"{clip['title']}\n\n{clip['description']}"
            publish_id = client.upload(Path(clip["file"]), caption, mode=pub["mode"],
                                       privacy=str(settings.get("publish.privacy_level", "SELF_ONLY")))
            db.update("publications", pub["id"], {"status": "envoye", "publish_id": publish_id, "posted_at": time.time()})
            db.update("clips", clip["id"], {"status": "publie"})
            db.log(f"Clip {clip['id']} envoyé à TikTok ({pub['mode']}, {publish_id})")
        except (TikTokError, OSError) as exc:
            db.update("publications", pub["id"], {"status": "erreur", "error": str(exc)[:500]})
            db.log(f"Échec publication clip {clip['id']} : {exc}", "error")
        done += 1
    return done


def refresh_status_and_stats(db: DB, client: TikTokClient | None) -> None:
    if client is None or not client.connected:
        return
    for pub in db.query("SELECT * FROM publications WHERE status = 'envoye' AND publish_id IS NOT NULL"):
        try:
            st = client.status(pub["publish_id"])
        except TikTokError as exc:
            log.warning("Statut TikTok %s : %s", pub["publish_id"], exc)
            continue
        ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
        if st.get("status") == "PUBLISH_COMPLETE" or ids:
            db.update("publications", pub["id"], {"status": "publie", "video_id": str(ids[0]) if ids else None})
        elif st.get("status") == "FAILED":
            db.update("publications", pub["id"], {"status": "erreur", "error": st.get("fail_reason", "FAILED")})
    rows = db.query("SELECT id, video_id FROM publications WHERE video_id IS NOT NULL")
    if rows:
        try:
            stats = {v["id"]: v for v in client.video_stats([r["video_id"] for r in rows])}
        except TikTokError as exc:
            log.warning("Stats TikTok : %s", exc)
            return
        for r in rows:
            if r["video_id"] in stats:
                db.update("publications", r["id"], {"stats": stats[r["video_id"]], "stats_at": time.time()})
