"""Stockage SQLite (WAL) : streams, statistiques de chat, clips, publications, coûts."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS streams (
  id INTEGER PRIMARY KEY, login TEXT NOT NULL, twitch_id TEXT, title TEXT, game TEXT,
  started_at REAL, ended_at REAL, status TEXT DEFAULT 'live', record_path TEXT,
  viewers INTEGER DEFAULT 0, viewers_peak INTEGER DEFAULT 0, recorded_seconds REAL DEFAULT 0,
  UNIQUE(login, twitch_id)
);
CREATE TABLE IF NOT EXISTS chat_stats (
  stream_id INTEGER, t REAL, wall REAL, messages INTEGER, hype INTEGER, repeats INTEGER,
  PRIMARY KEY(stream_id, t)
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY, stream_id INTEGER, login TEXT, start REAL, end REAL, peak REAL,
  created_at REAL, status TEXT, score INTEGER, category TEXT, reason TEXT, quote TEXT,
  title TEXT, hook TEXT, description TEXT, hashtags TEXT, credit TEXT, signals TEXT,
  analysis TEXT, file TEXT, thumbnail TEXT, duration REAL, layout TEXT, fingerprint TEXT,
  error TEXT, best_time TEXT
);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY, clip_id INTEGER, scheduled_at REAL, status TEXT, mode TEXT,
  publish_id TEXT, video_id TEXT, error TEXT, posted_at REAL, stats TEXT, stats_at REAL
);
CREATE TABLE IF NOT EXISTS costs (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, usd REAL, detail TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts REAL, level TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

JSON_FIELDS = {"signals", "analysis", "hashtags", "stats"}


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # -- bas niveau -------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [self._decode(dict(r)) for r in rows]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _decode(row: dict) -> dict:
        for key in JSON_FIELDS & row.keys():
            if isinstance(row[key], str):
                try:
                    row[key] = json.loads(row[key])
                except ValueError:
                    pass
        return row

    @staticmethod
    def _encode(values: dict) -> dict:
        return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and not isinstance(v, str) and v is not None else v)
                for k, v in values.items()}

    def insert(self, table: str, values: dict) -> int:
        values = self._encode(values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", values.values()).lastrowid

    def update(self, table: str, row_id: int, values: dict) -> None:
        if not values:
            return
        values = self._encode(values)
        sets = ", ".join(f"{k} = ?" for k in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])

    # -- raccourcis ---------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self.insert("events", {"ts": time.time(), "level": level, "message": message[:2000]})

    def add_cost(self, kind: str, usd: float, detail: str = "") -> None:
        self.insert("costs", {"ts": time.time(), "kind": kind, "usd": round(usd, 6), "detail": detail})

    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", [key])
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     [key, json.dumps(value)])

    def add_chat_stats(self, stream_id: int, rows: list[tuple[float, float, int, int, int]]) -> None:
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO chat_stats(stream_id, t, wall, messages, hype, repeats) VALUES (?, ?, ?, ?, ?, ?)",
                [(stream_id, *r) for r in rows])
            self.conn.commit()
