"""Chargement de la configuration (YAML) et des secrets (.env / environnement)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
AUTHORIZATIONS = ("aucune", "programme_officiel", "accord_ecrit", "propre_chaine")


def load_dotenv(path: Path | None = None) -> None:
    """Lecture minimale d'un .env (KEY=VALUE) sans écraser l'environnement."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def secret(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass
class Streamer:
    login: str
    nom: str
    actif: bool = True
    autorisation: str = "aucune"
    preuve: str = ""
    mots_cles: list[str] = field(default_factory=list)
    layout: str = "auto"
    facecam: str | None = None

    @property
    def can_publish(self) -> bool:
        return self.autorisation in ("programme_officiel", "accord_ecrit", "propre_chaine")


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        out[key] = _deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


DEFAULTS: dict[str, Any] = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) \
    if (ROOT / "config" / "settings.yaml").exists() else {}


class Settings:
    """Accès en lecture aux réglages : settings.get("analysis.pass_seconds")."""

    def __init__(self, data: dict[str, Any], root: Path = ROOT):
        self.data = _deep_merge(DEFAULTS, data)
        self.root = root

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data_dir(self) -> Path:
        path = Path(self.get("data_dir", "data"))
        path = path if path.is_absolute() else self.root / path
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_settings(path: Path | None = None) -> Settings:
    load_dotenv()
    path = path or Path(os.environ.get("CLIPFACTORY_SETTINGS", ROOT / "config" / "settings.yaml"))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Settings(data or {})


def load_streamers(path: Path | None = None) -> list[Streamer]:
    path = path or Path(os.environ.get("CLIPFACTORY_STREAMERS", ROOT / "config" / "streamers.yaml"))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Streamer] = []
    seen: set[str] = set()
    for item in data.get("streamers", []):
        login = str(item.get("login", "")).strip().lower()
        if not login or login in seen:
            continue
        seen.add(login)
        auth = str(item.get("autorisation", "aucune"))
        if auth not in AUTHORIZATIONS:
            raise ValueError(f"{login}: autorisation '{auth}' inconnue ({', '.join(AUTHORIZATIONS)})")
        out.append(Streamer(
            login=login,
            nom=str(item.get("nom") or login),
            actif=bool(item.get("actif", True)),
            autorisation=auth,
            preuve=str(item.get("preuve", "")),
            mots_cles=[str(k) for k in item.get("mots_cles") or []],
            layout=str(item.get("layout", "auto")),
            facecam=item.get("facecam"),
        ))
    return out
