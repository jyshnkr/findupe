"""Aggregation + plain-text rendering for `findupe stats`/`history`.

Two distinct quantities, never conflated:
- "reclaimable-found" = Family.surplus_bytes/surplus_count from the scan
  ledger (ledger.py) — what a scan flagged as deletable, whether or not it
  was ever applied.
- "reclaimed-actual" = bytes/files really moved to Trash, from the existing
  undo manifests (trash.py) — this module's aggregate_undo_totals is the
  only source of truth for it. The ledger never records what was actually
  deleted (no write-back step), so this module must not derive
  reclaimed-actual from ledger data.

No HTML here — dashboard.py owns rendering; this module stays pure data so
totals/timelines are testable without parsing HTML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .ledger import ScanRecord, list_scans
from .paths import resolve_data_home
from .report import _fmt_bytes
from .trash import list_manifests


@dataclass
class Totals:
    scans_recorded: int = 0
    applies: int = 0
    files_trashed_net: int = 0     # entries currently status=="trashed"
    bytes_reclaimed_net: int = 0   # sum(size) of those
    files_restored: int = 0
    files_failed: int = 0
    duplicates_found_total: int = 0  # cumulative surplus_count across all scans — NOT deduplicated


def _load_manifests(undo_dir: Path) -> list[dict]:
    manifests = []
    for path in list_manifests(undo_dir):
        try:
            manifests.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue  # never let one corrupt manifest crash aggregation
    return manifests


def aggregate_undo_totals(undo_dir: Path | None = None, scans_dir: Path | None = None) -> Totals:
    undo_dir = undo_dir if undo_dir is not None else resolve_data_home() / "undo"
    scans_dir = scans_dir if scans_dir is not None else resolve_data_home() / "scans"
    manifests = _load_manifests(undo_dir)
    totals = Totals(applies=len(manifests))
    for manifest in manifests:
        for e in manifest.get("entries", []):
            status = e.get("status", "")
            if status == "trashed":
                totals.files_trashed_net += 1
                totals.bytes_reclaimed_net += e.get("size", 0)
            elif status == "restored":
                totals.files_restored += 1
            elif status.startswith("failed:"):
                totals.files_failed += 1
    records = list_scans(scans_dir)
    totals.scans_recorded = len(records)
    totals.duplicates_found_total = sum(r.surplus_count for r in records)
    return totals


def applied_scan_ids(undo_dir: Path | None = None) -> set[str]:
    """scan_ids that have at least one apply — the 'applied' membership test."""
    undo_dir = undo_dir if undo_dir is not None else resolve_data_home() / "undo"
    return {m["scan_id"] for m in _load_manifests(undo_dir) if m.get("scan_id")}


def reclaimed_timeline(undo_dir: Path | None = None) -> list[tuple[str, int]]:
    """Reclaimed-ACTUAL series: [(YYYY-MM-DD, net_bytes)], bucketed by each
    manifest's created_at date, summing only entries currently status=="trashed".
    A date with manifests but nothing currently trashed (e.g. fully restored)
    still appears, with 0 — so the timeline doesn't silently skip a day."""
    undo_dir = undo_dir if undo_dir is not None else resolve_data_home() / "undo"
    by_date: dict[str, int] = {}
    for manifest in _load_manifests(undo_dir):
        date = manifest.get("created_at", "")[:10]
        by_date.setdefault(date, 0)
        for e in manifest.get("entries", []):
            if e.get("status") == "trashed":
                by_date[date] += e.get("size", 0)
    return sorted(by_date.items())


def duplicates_timeline(records: list[ScanRecord]) -> list[tuple[str, int]]:
    """Found (not reclaimed) series: [(scan_date, surplus_count)], one point
    per scan, sorted by scan_id (records from list_scans are already sorted)."""
    return [(r.created_at[:10], r.surplus_count) for r in records]


def render_stats_text(records: list[ScanRecord], totals: Totals, applied_ids: set[str]) -> str:
    lines = [
        f"{totals.scans_recorded} scans recorded · {totals.applies} applies",
        f"{totals.files_trashed_net} files currently trashed · "
        f"{_fmt_bytes(totals.bytes_reclaimed_net)} moved to Trash (net of restores) — "
        "may free space once you empty the Trash (less for any APFS clone, which "
        "shares its keeper's storage)",
        f"{totals.files_restored} restored · {totals.files_failed} failed",
        f"{totals.duplicates_found_total} surplus files flagged across "
        f"{totals.scans_recorded} scans — reclaimable only if deleted AND the Trash is "
        "emptied (not deduplicated: the same unresolved duplicate found again in a "
        "later scan counts again)",
        "note: findupe cannot measure disk space actually freed — only what's flagged "
        "and what's been moved to Trash; emptying the Trash is up to you.",
    ]
    return "\n".join(lines)
