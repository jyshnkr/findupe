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
from .paths import resolve_data_home

_SCHEMA_VERSION = 3  # v3: OCR fields (has_camera_exif, ocr_text, ocr_confidence)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    volume_uuid TEXT NOT NULL,
    exact_hash  TEXT,
    phash       INTEGER,
    dhash       INTEGER,
    width       INTEGER,
    height      INTEGER,
    capture_key TEXT,
    capture_subsec TEXT,
    has_camera_exif INTEGER,
    ocr_text        TEXT,
    ocr_confidence  REAL,
    last_seen   TEXT NOT NULL
);
"""


def _to_signed(v: int | None) -> int | None:
    """SQLite INTEGER is signed 64-bit; perceptual hashes are unsigned 64-bit."""
    if v is None:
        return None
    return v - (1 << 64) if v >= (1 << 63) else v


def _to_unsigned(v: int | None) -> int | None:
    if v is None:
        return None
    return v + (1 << 64) if v < 0 else v


@dataclass
class CachedInfo:
    exact_hash: str | None
    phash: int | None
    dhash: int | None
    width: int | None
    height: int | None
    capture_key: str | None
    capture_subsec: str | None
    has_camera_exif: bool
    ocr_text: str | None
    ocr_confidence: float | None


class Cache:
    def __init__(self, db_path: Path | None = None) -> None:
        db_path = db_path if db_path is not None else resolve_data_home() / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version != _SCHEMA_VERSION:
            # cheap-to-rebuild cache: recreate rather than migrate
            self.conn.execute("DROP TABLE IF EXISTS files")
            self.conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        self.conn.execute(_SCHEMA)
        self.conn.commit()
        self.hits = 0
        self.misses = 0

    def lookup(self, rec: FileRecord) -> CachedInfo | None:
        row = self.conn.execute(
            "SELECT exact_hash, phash, dhash, width, height, capture_key, capture_subsec, "
            "has_camera_exif, ocr_text, ocr_confidence "
            "FROM files WHERE path=? AND size=? AND mtime_ns=? AND volume_uuid=?",
            (norm_path(rec.path), rec.size, rec.mtime_ns, rec.volume_uuid),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        exact_hash, phash, dhash, width, height, capture_key, capture_subsec, \
            has_camera_exif, ocr_text, ocr_confidence = row
        return CachedInfo(
            exact_hash, _to_unsigned(phash), _to_unsigned(dhash),
            width, height, capture_key, capture_subsec,
            bool(has_camera_exif), ocr_text, ocr_confidence,
        )

    def store(self, records: list[FileRecord]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            # COALESCE merges pipeline stages (exact pass, then perceptual pass) for the
            # SAME file version. If size/mtime changed, the old values are stale and must
            # be replaced outright — hence the CASE guard on every merged column.
            "INSERT INTO files (path, size, mtime_ns, volume_uuid, exact_hash, phash, dhash,"
            " width, height, capture_key, capture_subsec, has_camera_exif, ocr_text,"
            " ocr_confidence, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET "
            + ", ".join(
                f"{col} = CASE WHEN files.size = excluded.size AND files.mtime_ns = excluded.mtime_ns"
                f" THEN COALESCE(excluded.{col}, files.{col}) ELSE excluded.{col} END"
                for col in ("exact_hash", "phash", "dhash", "width", "height",
                            "capture_key", "capture_subsec", "has_camera_exif", "ocr_text",
                            "ocr_confidence")
            )
            + ", size=excluded.size, mtime_ns=excluded.mtime_ns,"
            " volume_uuid=excluded.volume_uuid, last_seen=excluded.last_seen",
            [
                (
                    norm_path(r.path), r.size, r.mtime_ns, r.volume_uuid, r.exact_hash,
                    _to_signed(r.phash), _to_signed(r.dhash),
                    r.width, r.height, r.capture_key, r.capture_subsec,
                    r.has_camera_exif, r.ocr_text, r.ocr_confidence, now,
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
