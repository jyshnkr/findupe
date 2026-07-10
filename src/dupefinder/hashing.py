"""Exact-duplicate detection: size → partial hash → full BLAKE2b funnel.

Each stage only touches files that still collide in the previous stage, so the
expensive full-file read happens only for genuine same-size, same-edges files.
All reads stream in 4 MB chunks — a 100 GB video never loads into memory.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .cache import Cache
from .models import FileRecord

CHUNK = 4 * 1024 * 1024
EDGE = 64 * 1024


def full_hash(path: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def partial_hash(path: Path, size: int) -> str:
    """BLAKE2b of the first and last 64 KB — a cheap tiebreak for same-size files."""
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        h.update(f.read(EDGE))
        if size > 2 * EDGE:
            f.seek(size - EDGE)
            h.update(f.read(EDGE))
    return h.hexdigest()


def group_exact(
    records: list[FileRecord],
    cache: Cache | None = None,
    workers: int = 8,
) -> dict[str, list[FileRecord]]:
    """Return {full_hash: records} for every group of 2+ byte-identical files.

    Hardlinked duplicates (rec.hardlink_of set) are not candidates and are skipped.
    Unreadable files get rec.hash_error set and are dropped from grouping.
    """
    candidates = [r for r in records if r.hardlink_of is None]

    by_size: dict[int, list[FileRecord]] = {}
    for rec in candidates:
        by_size.setdefault(rec.size, []).append(rec)
    sized = [recs for recs in by_size.values() if len(recs) > 1]

    def _partial(rec: FileRecord) -> tuple[FileRecord, str | None]:
        try:
            return rec, partial_hash(rec.path, rec.size)
        except OSError as e:
            rec.hash_error = f"partial hash: {e}"
            return rec, None

    def _full(rec: FileRecord) -> tuple[FileRecord, str | None]:
        try:
            return rec, full_hash(rec.path)
        except OSError as e:
            rec.hash_error = f"full hash: {e}"
            return rec, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        by_partial: dict[tuple[int, str], list[FileRecord]] = {}
        partial_failed: list[FileRecord] = []
        for rec, ph in pool.map(_partial, [r for recs in sized for r in recs]):
            if ph is not None:
                by_partial.setdefault((rec.size, ph), []).append(rec)
            else:
                partial_failed.append(rec)

        finalists = [r for recs in by_partial.values() if len(recs) > 1 for r in recs]
        # a transient partial-hash failure must not silently exclude a file from
        # duplicate detection — give it the full hash directly
        for rec in partial_failed:
            rec.hash_error = None
            finalists.append(rec)

        # SQLite is main-thread-only by design: resolve cache hits here, pool
        # computes only the misses.
        to_compute = []
        computed = []
        for rec in finalists:
            cached = cache.lookup(rec) if cache is not None else None
            if cached is not None and cached.exact_hash:
                rec.exact_hash = cached.exact_hash
            else:
                to_compute.append(rec)
        for rec, fh in pool.map(_full, to_compute):
            if fh is not None:
                rec.exact_hash = fh
                computed.append(rec)

    if cache is not None and computed:
        cache.store(computed)

    groups: dict[str, list[FileRecord]] = {}
    for rec in finalists:
        if rec.exact_hash:
            groups.setdefault(rec.exact_hash, []).append(rec)
    return {h: recs for h, recs in groups.items() if len(recs) > 1}


def ensure_hashes(records: list[FileRecord], cache: Cache | None = None) -> None:
    """Guarantee exact_hash on the given records (family members).

    Visual-only matches skip the exact funnel, but the selection file must carry
    an expected BLAKE2b for every keeper and candidate so `apply` can re-verify.
    """
    for rec in records:
        if rec.exact_hash or rec.hash_error:
            continue
        cached = cache.lookup(rec) if cache is not None else None
        if cached is not None and cached.exact_hash:
            rec.exact_hash = cached.exact_hash
            continue
        try:
            rec.exact_hash = full_hash(rec.path)
        except OSError as e:
            rec.hash_error = f"full hash: {e}"
    if cache is not None:
        cache.store([r for r in records if r.exact_hash])
