"""Tableau de bord : API JSON + page unique (static/index.html)."""
from __future__ import annotations

import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from ..config import Settings, load_settings, load_streamers, secret
from ..db import DB
from ..publish import workflow

STATIC = Path(__file__).parent / "static"
security = HTTPBasic(auto_error=False)


class ClipEdit(BaseModel):
    title: str | None = None
    hook: str | None = None
    description: str | None = None


class ApproveBody(BaseModel):
    when: float | None = None


def create_app(db: DB | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    db = db or DB(settings.data_dir / "clipfactory.db")
    app = FastAPI(title="Clip Factory", docs_url=None, redoc_url=None)
    oauth_states: dict[str, float] = {}

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        password = secret("DASHBOARD_PASSWORD")
        if not password:
            return  # pas de mot de passe : écoute locale uniquement (127.0.0.1 par défaut)
        if creds is None or not (secrets.compare_digest(creds.username, "admin")
                                 and secrets.compare_digest(creds.password, password)):
            raise HTTPException(401, "Authentification requise", headers={"WWW-Authenticate": "Basic"})

    def clip_or_404(clip_id: int) -> dict:
        clip = db.one("SELECT * FROM clips WHERE id = ?", [clip_id])
        if not clip:
            raise HTTPException(404, "Clip introuvable")
        return clip

    def public_clip(c: dict) -> dict:
        a = c.get("analysis") or {}
        return {k: c.get(k) for k in ("id", "login", "start", "end", "peak", "created_at", "status", "score", "category",
                                       "reason", "quote", "title", "hook", "description", "hashtags", "credit",
                                       "duration", "layout", "error", "best_time", "signals")} | {
            "engine": a.get("engine"), "risks": a.get("risks", []), "standalone": a.get("standalone"),
            "has_video": bool(c.get("file")) and Path(c["file"]).exists(),
            "has_thumb": bool(c.get("thumbnail")) and Path(c["thumbnail"]).exists()}

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/overview", dependencies=[Depends(auth)])
    def overview() -> dict:
        streamers = load_streamers()
        lives = db.kv_get("lives", {}) or {}
        live_streams = db.query("SELECT * FROM streams WHERE status = 'live' ORDER BY started_at DESC")
        counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
        now = time.time()
        day0 = now - (now % 86400)
        llm_today = db.one("SELECT COALESCE(SUM(usd),0) s FROM costs WHERE ts > ?", [day0])["s"]
        llm_30 = db.one("SELECT COALESCE(SUM(usd),0) s FROM costs WHERE ts > ?", [now - 30 * 86400])["s"]
        per_day = db.query("SELECT CAST(ts/86400 AS INT)*86400 d, SUM(usd) s, COUNT(*) n FROM costs "
                           "WHERE ts > ? GROUP BY d ORDER BY d", [now - 30 * 86400])
        usage = shutil.disk_usage(settings.data_dir)
        data_bytes = sum(f.stat().st_size for f in settings.data_dir.rglob("*") if f.is_file())
        return {
            "now": now, "last_poll": db.kv_get("last_poll"),
            "streamers": [{"login": s.login, "nom": s.nom, "actif": s.actif, "autorisation": s.autorisation,
                           "can_publish": s.can_publish, "live": lives.get(s.login)} for s in streamers],
            "live_streams": live_streams, "clip_counts": counts,
            "costs": {"llm_today_usd": round(llm_today, 4), "llm_30d_usd": round(llm_30, 4), "per_day": per_day,
                      "server_monthly_eur": settings.get("costs.server_monthly_eur", 0),
                      "other_monthly_eur": settings.get("costs.other_monthly_eur", 0)},
            "disk": {"data_gb": round(data_bytes / 1e9, 2), "free_gb": round(usage.free / 1e9, 1)},
            "tiktok_connected": bool((c := workflow.tiktok_client(settings)) and c.connected),
            "publish_mode": settings.get("publish.mode", "manuel"),
            "engine": "claude" if secret("ANTHROPIC_API_KEY") and settings.get("llm.enabled", True) else "heuristique",
        }

    @app.get("/api/chat/{stream_id}", dependencies=[Depends(auth)])
    def chat_activity(stream_id: int, minutes: int = 120) -> list[dict]:
        since = time.time() - minutes * 60
        rows = db.query("SELECT wall, messages, hype, repeats FROM chat_stats WHERE stream_id = ? AND wall > ? ORDER BY wall",
                        [stream_id, since])
        if not rows:  # stream rejoué/fichier : derniers points connus
            rows = db.query("SELECT wall, messages, hype, repeats FROM chat_stats WHERE stream_id = ? ORDER BY wall DESC LIMIT 720",
                            [stream_id])[::-1]
        return rows

    @app.get("/api/clips", dependencies=[Depends(auth)])
    def clips(status: str | None = None, limit: int = 120) -> list[dict]:
        if status:
            rows = db.query("SELECT * FROM clips WHERE status = ? ORDER BY score DESC, created_at DESC LIMIT ?", [status, limit])
        else:
            rows = db.query("SELECT * FROM clips ORDER BY created_at DESC LIMIT ?", [limit])
        return [public_clip(c) for c in rows]

    @app.get("/media/clip/{clip_id}", dependencies=[Depends(auth)])
    def clip_video(clip_id: int) -> FileResponse:
        c = clip_or_404(clip_id)
        if not c.get("file") or not Path(c["file"]).exists():
            raise HTTPException(404, "Vidéo absente")
        return FileResponse(c["file"], media_type="video/mp4", filename=Path(c["file"]).name)

    @app.get("/media/thumb/{clip_id}", dependencies=[Depends(auth)])
    def clip_thumb(clip_id: int) -> FileResponse:
        c = clip_or_404(clip_id)
        if not c.get("thumbnail") or not Path(c["thumbnail"]).exists():
            raise HTTPException(404, "Miniature absente")
        return FileResponse(c["thumbnail"], media_type="image/jpeg")

    @app.post("/api/clips/{clip_id}/approve", dependencies=[Depends(auth)])
    def approve(clip_id: int, body: ApproveBody | None = None) -> dict:
        streamers = {s.login: s for s in load_streamers()}
        try:
            pub = workflow.approve(db, settings, clip_id, streamers, body.when if body else None)
        except workflow.PublishBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        return db.one("SELECT * FROM publications WHERE id = ?", [pub]) or {}

    @app.post("/api/clips/{clip_id}/reject", dependencies=[Depends(auth)])
    def reject(clip_id: int) -> dict:
        clip_or_404(clip_id)
        workflow.reject(db, clip_id)
        return {"ok": True}

    @app.post("/api/clips/{clip_id}/edit", dependencies=[Depends(auth)])
    def edit(clip_id: int, body: ClipEdit) -> dict:
        clip_or_404(clip_id)
        db.update("clips", clip_id, {k: v.strip() for k, v in body.model_dump().items() if v is not None})
        return public_clip(clip_or_404(clip_id))

    @app.post("/api/clips/{clip_id}/rerender", dependencies=[Depends(auth)])
    def rerender(clip_id: int) -> dict:
        from ..pipeline import render_stored_clip

        c = clip_or_404(clip_id)
        st = db.one("SELECT record_path FROM streams WHERE id = ?", [c["stream_id"]])
        if not st or not st.get("record_path") or not Path(st["record_path"]).exists():
            raise HTTPException(409, "Enregistrement source supprimé (voir recording.keep_raw_hours)")
        streamer = next((s for s in load_streamers() if s.login == c["login"]), None)
        if streamer is None:
            raise HTTPException(409, "Streamer absent de la configuration")
        render_stored_clip(db, clip_id, st["record_path"], settings, streamer)
        return public_clip(clip_or_404(clip_id))

    @app.get("/api/publications", dependencies=[Depends(auth)])
    def publications() -> list[dict]:
        return db.query("SELECT p.*, c.title, c.login, c.score, c.duration FROM publications p "
                        "JOIN clips c ON c.id = p.clip_id WHERE p.status != 'annule' ORDER BY p.scheduled_at DESC LIMIT 200")

    @app.post("/api/publications/{pub_id}/cancel", dependencies=[Depends(auth)])
    def cancel(pub_id: int) -> dict:
        p = db.one("SELECT * FROM publications WHERE id = ?", [pub_id])
        if not p:
            raise HTTPException(404)
        db.update("publications", pub_id, {"status": "annule"})
        db.update("clips", p["clip_id"], {"status": "en_attente"})
        return {"ok": True}

    @app.post("/api/publications/{pub_id}/done", dependencies=[Depends(auth)])
    def mark_done(pub_id: int) -> dict:
        p = db.one("SELECT * FROM publications WHERE id = ?", [pub_id])
        if not p:
            raise HTTPException(404)
        db.update("publications", pub_id, {"status": "publie", "posted_at": time.time()})
        db.update("clips", p["clip_id"], {"status": "publie"})
        return {"ok": True}

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events(limit: int = 60) -> list[dict]:
        return db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", [limit])

    @app.get("/tiktok/connect", dependencies=[Depends(auth)])
    def tiktok_connect() -> RedirectResponse:
        client = workflow.tiktok_client(settings)
        if client is None:
            raise HTTPException(409, "Renseignez TIKTOK_CLIENT_KEY et TIKTOK_CLIENT_SECRET dans .env")
        url, state = client.authorize_url()
        oauth_states[state] = time.time()
        return RedirectResponse(url)

    @app.get("/tiktok/callback")
    def tiktok_callback(request: Request) -> RedirectResponse:
        state, code = request.query_params.get("state"), request.query_params.get("code")
        if not state or state not in oauth_states or time.time() - oauth_states.pop(state) > 900 or not code:
            raise HTTPException(400, "Réponse OAuth TikTok invalide ou expirée")
        client = workflow.tiktok_client(settings)
        if client is None:
            raise HTTPException(409, "TikTok non configuré")
        client.exchange_code(code)
        db.log("Compte TikTok connecté")
        return RedirectResponse("/#planning")

    app.state.db = db
    app.state.settings = settings
    os.environ.setdefault("CLIPFACTORY_DASHBOARD", "1")
    return app
