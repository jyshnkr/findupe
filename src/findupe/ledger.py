"""Persistent scan history: one JSON record + archived report copies per scan.

Every `scan` run is archived here, unconditionally — this is what makes a
reviewed-but-never-applied scan recoverable after the next scan overwrites
report.html. Deliberately NOT a SQLite table: cache.py's index.db drops its
whole table on any schema-version mismatch (fine for a disposable hash
cache, wrong for durable history), so each scan gets its own JSON file with
its own schema_version instead — no shared migration machinery, ever.

Write order matters: both report copies are written before meta.json, and
meta.json is written last (atomically, via trash._write_json_atomic). A
crash mid-archive leaves a directory with no meta.json, which list_scans
treats as "skip" — no separate crash-recovery bookkeeping needed.

"applied" status is deliberately NOT stored here — a ledger entry is
write-once and never touched again after record_scan. Whether a scan was
later applied is derived by the caller (stats.py) from the undo manifests.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Family, ScanResult
from .paths import resolve_data_home
from .trash import _write_json_atomic

SCAN_SCHEMA_VERSION = "1"


@dataclass
class ScanRecord:
    scan_id: str
    created_at: str
    roots: list[str]
    duplicate_families: int
    possible_matches: int
    surplus_count: int
    surplus_bytes: int
    categories: dict
    problems: dict
    scan_dir: Path = field(default=None)          # filled on read, not stored in JSON
    report_paths: dict = field(default_factory=dict)  # {"images": Path|None, "other": Path|None}


def _category_summary(fams: list[Family]) -> dict:
    return {
        "families": len(fams),
        "surplus_count": sum(f.surplus_count for f in fams),
        "surplus_bytes": sum(f.surplus_bytes for f in fams),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_scan(
    scan: ScanResult,
    possible: list[Family],
    img_families: list[Family],
    other_families: list[Family],
    report_paths: tuple[Path, Path],
    scans_dir: Path | None = None,
) -> Path:
    """Archive one scan: copy both reports, then write meta.json last.

    Raises on any I/O failure (unwritable scans_dir, missing source report,
    etc.) — the caller (cmd_scan) owns the best-effort catch; this stays
    strict so it's independently testable.
    """
    scans_dir = scans_dir if scans_dir is not None else resolve_data_home() / "scans"
    scan_dir = scans_dir / scan.scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)

    img_path, other_path = report_paths
    shutil.copy2(img_path, scan_dir / "report-images.html")
    shutil.copy2(other_path, scan_dir / "report-other.html")

    meta = {
        "schema_version": SCAN_SCHEMA_VERSION,
        "scan_id": scan.scan_id,
        "created_at": _now_iso(),
        "roots": [str(r) for r in scan.roots],
        "duplicate_families": len(scan.families),
        "possible_matches": len(possible),
        "surplus_count": sum(f.surplus_count for f in scan.families),
        "surplus_bytes": sum(f.surplus_bytes for f in scan.families),
        "categories": {
            "images": _category_summary(img_families),
            "other": _category_summary(other_families),
        },
        "problems": {
            "skipped_stubs": len(scan.skipped_stubs),
            "skipped_managed": len(scan.skipped_managed),
            "hardlinks": len(scan.hardlink_notes),
            "zero_byte": len(scan.zero_byte),
            "read_errors": len(scan.errors),
            "hash_errors": len(scan.hash_errors),
        },
    }
    _write_json_atomic(scan_dir / "meta.json", meta)
    return scan_dir


def _read_record(scan_dir: Path) -> ScanRecord | None:
    """Parse one scan directory's meta.json. None if missing, unparseable, or
    a schema_version this code doesn't understand — never raises, so a single
    corrupt/partial directory can't crash a caller iterating many of them. A
    missing archived report copy does NOT invalidate the record — it just
    shows up as report_paths[x] = None."""
    meta_path = scan_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text())
        if meta.get("schema_version") != SCAN_SCHEMA_VERSION:
            return None

        def _report_path(category: str) -> Path | None:
            p = scan_dir / f"report-{category}.html"
            return p if p.is_file() else None

        return ScanRecord(
            scan_id=meta["scan_id"],
            created_at=meta["created_at"],
            roots=meta["roots"],
            duplicate_families=meta["duplicate_families"],
            possible_matches=meta["possible_matches"],
            surplus_count=meta["surplus_count"],
            surplus_bytes=meta["surplus_bytes"],
            categories=meta["categories"],
            problems=meta["problems"],
            scan_dir=scan_dir,
            report_paths={"images": _report_path("images"), "other": _report_path("other")},
        )
    except (OSError, json.JSONDecodeError, KeyError):
        # covers: unreadable file, invalid JSON, AND schema-valid-but-missing-field
        # (e.g. a write truncated despite the atomic-write guard) — any of these
        # must be skip-eligible, not a crash for the whole listing.
        return None


def list_scans(scans_dir: Path | None = None) -> list[ScanRecord]:
    """All valid scan records, sorted by scan_id. Skips any directory whose
    meta.json is missing, unparseable, or schema-mismatched."""
    scans_dir = scans_dir if scans_dir is not None else resolve_data_home() / "scans"
    if not scans_dir.is_dir():
        return []
    records = [_read_record(d) for d in scans_dir.iterdir() if d.is_dir()]
    return sorted((r for r in records if r is not None), key=lambda r: r.scan_id)


def load_scan(scan_id: str, scans_dir: Path | None = None) -> ScanRecord | None:
    """One record by exact scan_id; None if absent or corrupt."""
    scans_dir = scans_dir if scans_dir is not None else resolve_data_home() / "scans"
    return _read_record(scans_dir / scan_id)
