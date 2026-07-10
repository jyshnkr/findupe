"""Persistent SQLite index so re-scans only hash new or changed files.

A cache row is valid only if (path, size, mtime_ns, volume) all match — any
change forces recompute. WAL mode + batched transactions keep the index
consistent if a scan is interrupted; resuming is just re-running the scan.
Written from the main thread only (workers return results; caller stores).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import FileRecord, norm_path

DEFAULT_DB = Path.home() / ".dupefinder" / "index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    volume     TEXT NOT NULL,
    exact_hash TEXT,
    phash      INTEGER,
    dhash      INTEGER,
    width      INTEGER,
    height     INTEGER,
    capture_key TEXT,
    last_seen  TEXT NOT NULL
);
"""


@dataclass
class CachedInfo:
    exact_hash: str | None
    phash: int | None
    dhash: int | None
    width: int | None
    height: int | None
    capture_key: str | None


class Cache:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(_SCHEMA)
        self.conn.commit()
        self.hits = 0
        self.misses = 0

    def lookup(self, rec: FileRecord) -> CachedInfo | None:
        row = self.conn.execute(
            "SELECT exact_hash, phash, dhash, width, height, capture_key "
            "FROM files WHERE path=? AND size=? AND mtime_ns=? AND volume=?",
            (norm_path(rec.path), rec.size, rec.mtime_ns, rec.volume),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return CachedInfo(*row)

    def store(self, records: list[FileRecord]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            # COALESCE merges pipeline stages (exact pass, then perceptual pass) for the
            # SAME file version. If size/mtime changed, the old values are stale and must
            # be replaced outright — hence the CASE guard on every merged column.
            "INSERT INTO files (path, size, mtime_ns, volume, exact_hash, phash, dhash,"
            " width, height, capture_key, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET "
            + ", ".join(
                f"{col} = CASE WHEN files.size = excluded.size AND files.mtime_ns = excluded.mtime_ns"
                f" THEN COALESCE(excluded.{col}, files.{col}) ELSE excluded.{col} END"
                for col in ("exact_hash", "phash", "dhash", "width", "height", "capture_key")
            )
            + ", size=excluded.size, mtime_ns=excluded.mtime_ns,"
            " volume=excluded.volume, last_seen=excluded.last_seen",
            [
                (
                    norm_path(r.path), r.size, r.mtime_ns, r.volume, r.exact_hash,
                    r.phash, r.dhash, r.width, r.height, r.capture_key, now,
                )
                for r in records
            ],
        )
        self.conn.commit()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM files")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
