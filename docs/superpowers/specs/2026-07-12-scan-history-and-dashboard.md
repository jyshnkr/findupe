# Persistent scan history, stats & dashboard (Phase 2)

## Context

Phase 1 ("make problems loud" — fail-fast bad roots + surfaced hash/decode
errors) shipped and released as v0.2.1. This is Phase 2 of the same
original user request: two pain points remain. First,
`report-images.html`/`report-other.html` are fixed filenames overwritten by
every scan — a scan you reviewed but never applied is unrecoverable once
you run `scan` again. Second, there is no way to answer "how many
duplicates have I handled, and how much space have I actually reclaimed"
without hand-aggregating `~/.dupefinder/undo/*.json` yourself.

Investigation (3 Explore agents + a Plan agent, all against live source,
cross-checked directly) confirmed the raw data for the second problem
already exists in the undo manifests; what's missing is a persistent record
of *scans* (not just applies) and a way to view both. Scoped via two
AskUserQuestion rounds (6 locked decisions below).

## Locked requirements

1. Archive full reports, not just stats — a reviewed-but-never-applied scan
   must be fully recoverable exactly as it looked.
2. Every scan, automatically — no opt-in flag, even a zero-duplicates scan
   gets archived.
3. Dashboard = totals + trend chart, plus a separate plain-text
   `dupefinder stats` — both must exist.
4. Unbounded retention for now — no pruning; revisit only if it becomes a
   real problem.
5. Storage format: JSON-per-scan, not a new SQLite table — deliberately
   avoids `cache.py`'s drop-the-whole-table-on-schema-mismatch behavior
   (fine for a disposable hash cache, wrong for durable history).
6. A `dupefinder history` command to list and reopen individual past
   archived scans.

## Key technical decisions

| Decision | Choice | Why |
|---|---|---|
| Found vs. reclaimed | Two distinct series, never conflated. "Reclaimable-found" = `Family.surplus_bytes`/`surplus_count`, sourced from the new ledger. "Reclaimed-actual" = bytes/files really moved to Trash, sourced entirely from existing `~/.dupefinder/undo/*.json`. | The ledger never records what was actually deleted — no write-back step. Labeling must make this explicit everywhere or the dashboard overstates progress. Mirrors `report.py`'s own existing "reclaimable *if all suggestions accepted*" caveat (report.py:304). |
| Module split | `ledger.py` (persistence), `stats.py` (aggregation + plain text, no HTML), `dashboard.py` (self-contained HTML + inline SVG, no library). | Mirrors report.py's existing decomposition (`category_output_paths`/`_is_image_family`/`_write_report`/`generate_reports`). Keeps totals unit-testable as plain data; `dashboard.py` mirrors `report.py`'s role one-to-one. |
| `applied` status | Derived at read time (any undo manifest with matching `scan_id`), never stored in the ledger entry. | Keeps ledger entries write-once/immutable — no write-back step to get wrong. |
| `record_scan` inputs | Takes `cmd_scan`'s already-computed `img_families`/`other_families` rather than re-deriving the split. | Avoids a second, potentially-diverging copy of the `_is_image_family` classification. |
| Archival failure mode | `record_scan` raises on I/O failure (unit-testable); `cmd_scan` wraps it in a broad `try/except Exception`, prints a stderr warning, still returns 0. | Archival is a convenience on top of an already-fully-succeeded scan — it must never make a successful scan look failed. |
| Completeness signal | Report copies written first, `meta.json` written last (via `trash._write_json_atomic`). `list_scans` treats "no meta.json" as "skip." | A crash mid-archive leaves no valid meta.json — automatically excluded from listings, no separate recovery bookkeeping. |
| "Duplicates found" totals | Reported with explicit "across N scans" framing, never implied as deduplicated. | Re-scanning an overlapping library counts the same unresolved duplicates again — this is a cumulative activity number, not a distinct-duplicates-ever count. |
| `history <scan_id>` matching | Accepts a prefix (exact name, stem, or `startswith`), not just an exact id. | Mirrors `cmd_undo`'s existing flexible manifest-matching convention (`cli.py`). |

## Architecture

New files: `src/dupefinder/ledger.py`, `src/dupefinder/stats.py`,
`src/dupefinder/dashboard.py`.

Directory layout:
```
~/.dupefinder/scans/<scan_id>/
    report-images.html   # archived copy (shutil.copy2)
    report-other.html    # archived copy
    meta.json             # written LAST
```

`meta.json` schema (own `schema_version`, no shared migration machinery):
```json
{
  "schema_version": "1", "scan_id": "20260712-160000",
  "created_at": "2026-07-12T16:00:00.123456+00:00",
  "roots": ["/Users/jay/Photos"],
  "duplicate_families": 12, "possible_matches": 3,
  "surplus_count": 40, "surplus_bytes": 123456789,
  "categories": {"images": {"families": 8, "surplus_count": 30, "surplus_bytes": 100000000},
                 "other":  {"families": 4, "surplus_count": 10, "surplus_bytes": 23456789}},
  "problems": {"skipped_stubs": 5, "skipped_managed": 1, "hardlinks": 2,
               "zero_byte": 3, "read_errors": 0, "hash_errors": 1}
}
```
`problems` reuses the exact Phase 1 counts (including `hash_errors`).

`ledger.py`: `record_scan(scan, possible, img_families, other_families,
report_paths, scans_dir=SCANS_DIR) -> Path`; `list_scans(scans_dir) ->
list[ScanRecord]` (sorted by scan_id, skips corrupt dirs); `load_scan(scan_id,
scans_dir) -> ScanRecord | None`. Reuses `trash._write_json_atomic`.

`stats.py`: `Totals` dataclass (scans_recorded, applies,
files_trashed_net, bytes_reclaimed_net, files_restored, files_failed,
duplicates_found_total); `aggregate_undo_totals`, `applied_scan_ids`,
`reclaimed_timeline` (reclaimed-actual, by apply date), `duplicates_timeline`
(found, by scan date), `render_stats_text`. Companion entries count toward
reclaimed bytes (real disk space); summing only `status=="trashed"` entries
yields net-of-restore for free.

`dashboard.py`: `render_dashboard_html(records, totals, applied_ids,
reclaimed_series, dup_series) -> str` — totals band, per-scan table with
applied badge, hand-rolled inline `<svg>` two-line chart (no library),
theme-aware matching report.py's `color-scheme`/`color-mix` conventions.

CLI (`cli.py`): new hidden `--scans-dir` global; `dupefinder stats` (text);
`dupefinder stats --html [PATH]` (also writes dashboard, default
`dupefinder-dashboard.html` in CWD); `dupefinder history` (list) and
`dupefinder history <scan_id>` (detail + archived report paths, prefix
matching). `cmd_scan` calls `record_scan` as the last step before
`return 0`.

Out of scope: pruning/retention; an archival opt-out flag; deduplicating
"duplicates found" across repeat scans; the pre-existing `_fmt_bytes`
duplication between `cli.py`/`report.py`.

## Post-implementation fixes (advisor review, before commit)

Two real issues found by an `advisor()` pass after implementation, both
fixed test-first before committing:

1. **`ledger._read_record` could still crash the whole listing.** Its
   docstring claimed "never raises," but `ScanRecord(scan_id=meta["scan_id"], ...)`
   read required fields with plain dict indexing — a `meta.json` that's
   valid JSON with the correct `schema_version` but a missing field (e.g. a
   write truncated despite the atomic-write guard) raised an uncaught
   `KeyError` that propagated through `list_scans`'s comprehension, crashing
   `stats`/`history` entirely — precisely the "one bad directory takes down
   the listing" failure this design exists to prevent. The existing
   corrupt-dir test didn't cover it (the wrong-schema-version case returns
   `None` before reaching the field-access code). Fixed by widening the
   catch to include `KeyError`; regression test added
   (`test_list_scans_skips_dir_with_schema_valid_but_missing_field`).
2. **A single data point rendered as a misleading pinned-to-top-left dot.**
   `_svg_line_chart` scales y by `value / max(values)`, so with exactly one
   point that ratio is always 1 — the dot always lands at the axis max
   regardless of its actual value. Because the ledger is forward-only, a
   user's *first* `stats --html` will typically have exactly 0 or 1 scans,
   making this degenerate case the likely first impression rather than a
   rare edge case. Fixed by extending the existing "no data yet" placeholder
   to cover `len(points) < 2`, with a distinct "only 1 scan so far — check
   back after your next scan" message for the `== 1` case; regression test
   added (`test_dashboard_single_point_shows_placeholder_not_pinned_dot`).

A design correction made **during** implementation (not a bug, a course
correction against the dataviz skill's non-negotiables): the plan's original
"one two-series line chart" was replaced with two separate single-series
charts (small multiples) before any chart code was written — bytes-reclaimed
and duplicates-found are different units/scales, so sharing one y-axis (or a
dual-axis chart) would misrepresent one series relative to the other. This
still fully satisfies the locked "totals + trend chart" requirement.

## Verification

Strict TDD (red confirmed before green, one behavior per test) across
`tests/test_ledger.py`, `tests/test_stats.py`, `tests/test_dashboard.py`,
and additions to `tests/test_e2e.py` — see the plan file
(`mighty-discovering-cocke.md`, this session) for the full representative
test list. Manual smoke test: real scan → confirm archive directory
contents; `dupefinder history`; `dupefinder stats --html` opened in a
browser to confirm the chart renders and matches the existing report's
visual style in both light and dark mode.

## Deferred

Same list as Phase 1's spec (APFS clone detection, OCR screenshot
discrimination, GUI, scheduling, config file, PyPI publishing, etc.), plus
this phase's own explicitly-out-of-scope items above.
