# Make problems loud: fail-fast roots + surfaced hash/decode errors

## Context

A real scan against the user's photo library exposed two related gaps.
First, `~/Users/jayashankarmangina/Documents/DCIMZ_2` (a doubled-up `~`
expansion typo) silently produced a scan of 0 files from that root with no
loud complaint — macOS does not collapse the doubled path (verified live),
and the CLI only ever printed an aggregate `N errors` count, never the
specific bad path. Second, investigation of `discover.py`/`cli.py`/`report.py`
found that `FileRecord.hash_error` (set when a file discovers fine but fails
to decode/hash) was never rolled into `ScanResult` at all — such files
silently vanish from candidates with no count in the terminal and no line in
the HTML report notes, unlike every other skip/error category, which already
renders fully in the report (just not the terminal). This spec covers making
every class of scan problem loud: a bad root aborts the run before any
scanning, and every skip/error/refusal category — including the previously
invisible hash/decode failures — is both counted on the terminal and
detailed in the report.

Scoped via 3 parallel Explore agents against the actual `discover.py`/
`cli.py`/`report.py`/`models.py`/`cache.py`/`trash.py` source, two
AskUserQuestion rounds, and an advisor review that caught a real gap in the
initial code read (see Key technical decisions).

This is Phase 1 of a two-phase user request. Phase 2 (persistent scan
history + a stats/space-reclaimed dashboard) is deliberately deferred — see
Deferred below.

## Locked requirements (from AskUserQuestion rounds)

1. **Fail fast, pre-flight only.** Validate every named root before any
   scanning; abort with a non-zero exit if any is missing/unreadable, naming
   the resolved path. Mid-scan problems (a permission-denied subfolder, one
   unreadable file deep in a tree) do **not** abort — only the up-front root
   check does.
2. **Smart-mix terminal verbosity.** High-signal, usually-short lists
   (refused managed libraries) print in full on the terminal. Potentially
   large lists (per-file read errors, decode/hash errors) print as a count +
   a pointer to the report notes, not flooded into the terminal.
3. **Hash/decode errors get their own category**, both in the report notes
   (a distinct `<details>` block, matching the existing skip/error blocks)
   and in the terminal (their own count).

## Key technical decisions

| Decision | Choice | Why |
|---|---|---|
| Fail-fast location | CLI layer (`cmd_scan`), not `discover()` | Keeps `discover()`'s existing record-and-continue contract intact for its own direct callers/tests (`test_discover.py::test_unmounted_root_is_error_not_crash`); fail-fast is a UX policy that belongs at the command boundary. `discover.py`'s own `root.is_dir()` check stays as TOCTOU defense-in-depth for a root that vanishes between validation and the walk. |
| `hash_errors` collection source | `disc.records` (all primaries) **+** the already-gathered `companions` list, not `members` | `members` (family participants) *excludes* exactly the records grouping filtered out for having `hash_error` set — using it would silently miss every hash/decode failure, the opposite of the fix's purpose. Companions are removed from `disc.records` by `_attach_companions` and only hashed later via `ensure_hashes`; a dropped companion hash error is safety-relevant since companion hashes feed apply-time verification. (Caught by advisor review before implementation — the initial read had proposed collecting from `members` alone.) |
| Dedup by path in `_collect_hash_errors` | Extracted as its own function, deduping on `rec.path` | `discover._attach_companions` appends one shared sidecar (e.g. one XMP) to *every* primary's `.companions` in a stem-group — exactly the RAW+JPEG-with-shared-sidecar shape this user's library has. Without dedup, `companions` (built as `[c for r in members for c in r.companions]`) contains that sidecar twice whenever both the RAW and JPEG primary are family members, so a single decode/hash error would be double-counted on the terminal and double-listed in the report. Caught by a second advisor review after the first implementation pass; fixed and covered by `tests/test_cli.py::test_collect_hash_errors_dedups_shared_companion` before commit. |
| Terminal print ordering | Discovery counts print immediately after `discover()`; the `decode/hash errors` count prints after the hash/perceptual passes complete | `hash_error` isn't fully known until after `compute_perceptual` (primaries) and `ensure_hashes` (companions) have both run — printing it earlier would be wrong or require restructuring the pipeline, which is out of scope. |

## Architecture

`src/dupefinder/models.py` — `ScanResult` gains one field, mirroring the
existing five:
```python
hash_errors: list[tuple[Path, str]] = field(default_factory=list)
```

`src/dupefinder/cli.py` — `cmd_scan`:
- Pre-validates all roots (resolved, `.is_dir()`) before printing anything
  else; prints each bad resolved path to stderr and returns `2` if any fail.
- Extends the discovery summary line with hardlink/zero-byte/read-error
  counts (previously silent or count-only); prints refused managed libraries
  in full; prints a read-errors count + report pointer when non-empty.
- After the hash/perceptual passes, collects `hash_errors` from
  `disc.records` + `companions`, prints its own count + pointer line, and
  passes it into `ScanResult`.

`src/dupefinder/report.py` — `_notes_html` gains one more `block(...)` call
for `scan.hash_errors`, using the same collapsible-`<details>` pattern and
200-item cap as the existing five blocks.

No changes to `discover.py`, `grouping.py`, `hashing.py`, `imaging.py`,
`trash.py`, or the safety contract (scan retains no delete authority; apply's
independent re-verification is untouched).

## Verification

1. `uv run pytest -q` — 93 passing + 1 pre-existing skip (the real-CR3-sample
   test, skipped because the sample file was deleted mid-session,
   unrelated to this change). 88 baseline + 6 new tests, strict TDD
   (red confirmed before green for every behavior, including two prints that
   were initially written ahead of their tests and were corrected by
   isolating them, re-confirming red, then green; and a dedup bug caught by
   a second advisor pass, fixed test-first before commit).
2. New tests: `test_scan_fails_fast_on_nonexistent_root`,
   `test_hash_errors_render_as_own_notes_block`,
   `test_scan_surfaces_hash_errors_in_terminal_and_report`,
   `test_scan_lists_refused_libraries_in_full`,
   `test_scan_prints_read_error_count_with_pointer`,
   `test_collect_hash_errors_dedups_shared_companion`.
3. Manual CLI smoke test (not just pytest): ran `dupefinder scan` with one
   nonexistent root → confirmed loud abort naming the resolved doubled/bad
   path, no report written. Ran `dupefinder scan` against a tree containing
   an undecodable `.jpg` → confirmed the terminal `1 decode/hash errors —
   see report notes for details` line and the report's new "Unreadable/
   undecodable during hashing" block, both showing the real path and the
   underlying `PIL.UnidentifiedImageError` message.

## Deferred

Phase 2 (not scoped by this spec — needs its own brainstorming/question
round before implementation): a persistent scan-history ledger under
`~/.dupefinder/` (reports currently overwrite every scan and are
unrecoverable once reviewed-but-not-applied), and a stats/dashboard view
(both a `dupefinder stats` terminal summary and an HTML dashboard, per the
user's stated preference) aggregating the deletion history that already
exists in `~/.dupefinder/undo/*.json` plus new forward-looking scan records.
The "notes duplicated verbatim across both category reports" observation
(found during investigation, never raised by the user) is also left as-is —
self-contained per-category reports are arguably correct.
