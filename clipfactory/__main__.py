"""Interface en ligne de commande.

    python -m clipfactory doctor              # vérifie l'installation
    python -m clipfactory verify              # vérifie les logins Twitch (API officielle)
    python -m clipfactory monitor             # démon 24/7 (lives → clips)
    python -m clipfactory dashboard           # tableau de bord web
    python -m clipfactory run                 # moniteur + tableau de bord dans un seul processus
    python -m clipfactory process FICHIER --login zerator [--chat chat.json]   # VOD/enregistrement autorisé
    python -m clipfactory replay FICHIER --login zerator [--chat chat.jsonl] [--speed 8]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

from .config import load_settings, load_streamers, secret


def cmd_doctor(_args) -> int:
    ok = True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  {'✔' if good else '✘'} {label}" + ("" if good else f"  → {hint}"))

    print("Installation :")
    for binary in ("ffmpeg", "ffprobe", "streamlink"):
        check(binary, shutil.which(binary) is not None, f"installez {binary}")
    for mod, hint in (("faster_whisper", "pip install faster-whisper"), ("cv2", "pip install 'opencv-python-headless<5'"),
                      ("anthropic", "pip install anthropic"), ("fastapi", "pip install fastapi uvicorn")):
        try:
            m = __import__(mod)
            check(mod, mod != "cv2" or hasattr(m, "CascadeClassifier"), hint)
        except ImportError:
            check(mod, False, hint)
    print("Secrets (.env) :")
    for name, need in (("TWITCH_CLIENT_ID", True), ("TWITCH_CLIENT_SECRET", True), ("ANTHROPIC_API_KEY", False),
                       ("TIKTOK_CLIENT_KEY", False), ("DASHBOARD_PASSWORD", False)):
        present = secret(name) is not None
        print(f"  {'✔' if present else ('✘' if need else '·')} {name}{'' if present else (' (requis)' if need else ' (optionnel)')}")
        ok &= present or not need
    streamers = load_streamers()
    allowed = [s.nom for s in streamers if s.can_publish]
    print(f"Streamers : {len(streamers)} configurés, {sum(s.actif for s in streamers)} actifs, "
          f"{len(allowed)} avec autorisation de publier ({', '.join(allowed) or 'aucun'})")
    return 0 if ok else 1


def cmd_verify(_args) -> int:
    from .twitch.helix import Helix

    streamers = load_streamers()
    helix = Helix(secret("TWITCH_CLIENT_ID") or "", secret("TWITCH_CLIENT_SECRET") or "")
    users = helix.users([s.login for s in streamers])
    lives = helix.live([s.login for s in streamers])
    bad = 0
    for s in streamers:
        u = users.get(s.login)
        state = "🔴 LIVE" if s.login in lives else ""
        print(f"  {'✔' if u else '✘'} {s.login:<14} {s.nom:<12} {(u or {}).get('display_name', 'INTROUVABLE'):<16} {state}")
        bad += 0 if u else 1
    return 1 if bad else 0


def cmd_monitor(_args) -> int:
    from .monitor import Monitor

    asyncio.run(Monitor().run())
    return 0


def cmd_dashboard(args) -> int:
    import uvicorn

    from .web.app import create_app

    s = load_settings()
    uvicorn.run(create_app(), host=args.host or s.get("dashboard.host", "127.0.0.1"),
                port=int(args.port or s.get("dashboard.port", 8080)), log_level="warning")
    return 0


def cmd_run(args) -> int:
    """Moniteur + tableau de bord dans la même boucle asyncio."""
    import uvicorn

    from .monitor import Monitor
    from .web.app import create_app

    async def main() -> None:
        mon = Monitor()
        s = mon.settings
        config = uvicorn.Config(create_app(mon.db, mon.settings), host=args.host or s.get("dashboard.host", "127.0.0.1"),
                                port=int(args.port or s.get("dashboard.port", 8080)), log_level="warning")
        await asyncio.gather(mon.run(), uvicorn.Server(config).serve())

    asyncio.run(main())
    return 0


def cmd_process(args) -> int:
    from .db import DB
    from .monitor import Monitor
    from .pipeline import process_file

    s = load_settings()
    streamers = load_streamers()
    st = next((x for x in streamers if x.login == args.login), None)
    if st is None:
        print(f"Streamer {args.login} absent de config/streamers.yaml", file=sys.stderr)
        return 2
    mon = Monitor(s, DB(s.data_dir / "clipfactory.db"))
    ids = process_file(mon.db, Path(args.file), st, s, Path(args.chat) if args.chat else None, mon.analyzer, streamers)
    for c in mon.db.query(f"SELECT id, status, score, title, file FROM clips WHERE id IN ({','.join('?' * len(ids)) or 'NULL'})", ids):
        print(f"  #{c['id']:<4} {c['status']:<10} {c['score']:>3}/100  {c['title']}\n        {c['file'] or ''}")
    return 0


def cmd_replay(args) -> int:
    from .monitor import replay

    sid = asyncio.run(replay(Path(args.file), Path(args.chat) if args.chat else None, args.login, args.speed))
    print(f"Replay terminé (stream #{sid}). Ouvrez le tableau de bord pour voir les clips.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "faster_whisper", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="clipfactory", description="Usine à clips Twitch → TikTok (FR)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("monitor").set_defaults(fn=cmd_monitor)
    for name, fn in (("dashboard", cmd_dashboard), ("run", cmd_run)):
        sp = sub.add_parser(name)
        sp.add_argument("--host")
        sp.add_argument("--port")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("process")
    sp.add_argument("file")
    sp.add_argument("--login", required=True)
    sp.add_argument("--chat")
    sp.set_defaults(fn=cmd_process)
    sp = sub.add_parser("replay")
    sp.add_argument("file")
    sp.add_argument("--login", required=True)
    sp.add_argument("--chat")
    sp.add_argument("--speed", type=float, default=8.0)
    sp.set_defaults(fn=cmd_replay)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
