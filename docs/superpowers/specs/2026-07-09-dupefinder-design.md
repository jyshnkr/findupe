# dupefinder — safe duplicate finder & reviewer for macOS

## Context

The user (a photographer: Canon RAW CR3/CRAW, iPhone HEIC, JPEG exports) has duplicate files — especially the same photo in multiple formats and multiple copies — scattered across their SSD and external drives. Generic dupe finders miss the cross-format case (a HEIC and a JPEG of one shot are never byte-identical) and don't understand a photographer's intent (keeping RAW + JPEG side-by-side is deliberate). Goal: a simple-but-powerful, fail-proof tool where **the user always makes the delete decision**.

Environment: macOS 26.5.1 (Apple Silicon), APFS, system Python 3.9.6 only, Homebrew + uv 0.9.24, `~/Pictures` contains Photos Library + Lightroom (managed stores — off-limits).

Design was researched by a 3-agent workflow (library/API research, 28-finding red-team, architecture) — all findings folded in below.

## Locked requirements (from brainstorming Q&A)

1. **Exact duplicates**: all file types, size → partial hash → full BLAKE2b. Zero false positives.
2. **Perceptual matching**: images only (JPEG/HEIC/HEIF/PNG/TIFF/WebP; RAW via embedded preview), to catch cross-format and re-encoded copies.
3. **Family model** (user's key clarification): same visual image = a *family*, partitioned **by format**. One copy per format is intentional (RAW for editing, JPEG for sharing). Only *surplus copies within a format* (`X_2.jpeg`, `X copy.jpeg`, re-imported CR3) are deletion candidates. Cross-format siblings shown as context, never auto-flagged.
4. **Scope**: folders given per run; external drives supported; managed-library internals refused by default.
5. **Deletion**: real macOS Trash (Put Back works, incl. externals) + undo manifest + `undo` command.
6. **Review**: self-contained HTML report (thumbnails, pre-checked checkboxes, live byte counter, Export selection → JSON). Separate `apply` command re-verifies before trashing. Never auto-delete; never trash the last copy in a format partition.
7. **Keeper heuristic** (suggestion only): highest resolution → camera-original format → oldest → clean filename → organized path.
8. **SQLite cache** (`~/.dupefinder/index.db`), keyed path+size+mtime+volume-UUID.
9. **Cloud stubs**: skip + report "not local" by default; `--materialize` downloads on demand; warn that deleting synced files propagates.
10. **Companions**: Live Photo HEIC+MOV and RAW XMP/AAE sidecars trashed together with their primary, clearly shown.
11. **Home**: `~/Documents/Projects/dupefinder`, git repo, uv-managed, pytest.

## Key technical decisions (from research)

| Decision | Choice | Why |
|---|---|---|
| Python | uv-managed **3.13** project (pyproject, src layout) | pillow-heif needs ≥3.10; uv handles everything |
| Deps | Pillow 12.x, pillow-heif 1.4, imagehash 4.3.2, rawpy 0.27, pybktree — **5 total** | all have macOS arm64 wheels; verified current; no PyObjC (see Trash/Cloud rows) |
| Perceptual hash | **pHash + dHash both** (imagehash, 64-bit). High-confidence: pHash ≤ 2 AND dHash ≤ 2. "Possible" tier: pHash 3–8, shown separately, never pre-checked. `--threshold` to tune | **Recalibrated by Phase 0 on real files**: HEIC→JPEG export = distance 0 (even resized); real burst pair = pHash 4 (must NOT be high-confidence); different photos ≥ 28 |
| Orientation | `ImageOps.exif_transpose()` before hashing (Pillow HEIF plugin auto-rotates on open) | same image rotated ≠ hash mismatch |
| RAW previews | `rawpy.extract_thumb()` primary; `exiftool -b -PreviewImage` fallback (brew). **CR3/CRAW support unverified → Phase 0 spike on real files** | fastest path; sips can't extract embedded previews |
| RAW grouping guard | RAWs only join a perceptual group if preview pHash ≤ 3 **AND** capture metadata (DateTimeOriginal + exposure params) matches | embedded previews can collide across different captures (red-team) |
| Trash | **Batched osascript Finder delete** (~100 files per AppleScript list, via stdin): Put Back works for ALL files on ALL volumes. If Finder automation is denied/fails → **abort with clear message** (no degraded fallback deleter, by design). Never `rm` | NSFileManager preserves Put Back only for first file per process (rdar://41878624); batching amortizes osascript spawn cost. Needs one-time Automation permission for Finder |
| Cloud stubs | detect via `os.stat().st_flags & stat.SF_DATALESS`; `--materialize` = don't skip stubs — the hash's streaming read itself triggers iCloud download (per-file timeout + progress note) | reading a dataless file is the OS's own materialization trigger; deletes the PyObjC dependency and the most fragile code path |
| Grouping at scale | BK-tree (pybktree) over 64-bit hashes | 100k images ≈ seconds, not hours |
| Concurrency | ThreadPool (8) for hashing (I/O-bound), ProcessPool (4) for decode+phash (CPU-bound); single SQLite writer thread, batched commits | keeps cache consistent; interrupted scans resume from cache |

## Explicit assumptions (surfaced per Karpathy review — flag now if wrong)

- ~~Perceptual thresholds provisional~~ **Phase 0 DONE (2026-07-09)**: rawpy extract_thumb ✅ on real CR3s (6000×4000 JPEG previews); pillow-heif ✅ on real iPhone HEICs; thresholds recalibrated to pHash ≤ 2 AND dHash ≤ 2 (high) / 3–8 (possible) — real burst pair measured at pHash 4.
- A one-time macOS Automation permission prompt (Terminal → Finder) is acceptable; if denied, `apply` aborts safely — there is deliberately no fallback deleter.
- The HTML report is reviewed **on the same machine** that scanned (selection.json carries absolute paths).
- v1 has **no flag** to scan inside Photos/Lightroom libraries; that's a hard denylist.
- rawpy's CR3/CRAW support is unverified until the Phase 0 spike; exiftool (brew) is the contingency.

## Safety model (red-team → design rules)

1. **Two-command separation**: `scan` has no delete authority; `apply` acts only on a reviewed selection JSON.
2. **Keeper survival, enforced twice**: keeper checkbox disabled in HTML; `apply` independently validates ≥1 surviving, present, readable, hash-verified file per format partition — else rejects that family with a clear error.
3. **TOCTOU re-verification**: `apply` re-checks existence + size + mtime + full BLAKE2b per file; any mismatch → skip + prominent report line. Keeper verified before any sibling is trashed.
4. **Hardlinks**: same `(st_dev, st_ino)` = one file, not duplicates — shown informationally ("deleting reclaims nothing"), excluded from candidates. APFS clone caveat documented in report footer.
5. **Undo manifest written BEFORE trashing** (intent + expected hashes, atomic temp+fsync+rename); per-file outcomes appended (JSONL); `undo <scan-id>` restores from Trash to original paths, best-effort with per-file results.
6. **External volume pre-flight**: verify per-volume `.Trashes` exists/writable before trashing there; on failure, abort those files with explanation — never silently permanent-delete.
7. **Cloud-sync warnings**: files under iCloud Drive/Desktop&Documents/Dropbox get a sync badge in the report and a summary warning in `apply` ("deletion propagates to other devices").
8. **Suspicious-group flags**: same-format perceptual groups > 3 files ("possible burst — review carefully") and low-entropy images (near-uniform thumbnails) are flagged and never pre-checked.
9. **Robustness**: streaming hash (4 MB chunks; >50 GB files fine); zero-byte files excluded from dupes (reported separately); NFC/NFD path normalization; permission errors collected and reported, never fatal mid-scan; report paginates at 50 families/page with lazy thumbnails.
10. **Managed stores**: `.photoslibrary`, `.lrlib`, `.lrdata`, `.lrcat`, Time Machine snapshots on built-in denylist; scan refuses with explanation (no override flag in v1).

## Architecture

```
~/Documents/Projects/dupefinder/
├── pyproject.toml            # uv, requires-python >=3.13
├── README.md
├── src/dupefinder/
│   ├── models.py     # dataclasses: FileRecord, Family, FormatPartition, Selection, UndoManifest…
│   ├── discover.py   # walk roots, exclusions/denylist, stub & hardlink & companion detection
│   ├── hashing.py    # size → partial(64KB head+tail) → full BLAKE2b, streaming
│   ├── imaging.py    # decode (Pillow/pillow-heif/rawpy), exif_transpose, pHash+dHash, thumbnails
│   ├── grouping.py   # exact groups + BK-tree clusters → families → format partitions → keeper
│   ├── cache.py      # SQLite index; (path,size,mtime,volume_uuid) invalidation; batched writer
│   ├── report.py     # self-contained HTML: sections Exact / Families / Possible; export JSON via Blob
│   ├── trash.py      # Trasher protocol: FinderTrasher (batched osascript) / FakeTrasher (tests); undo manifests
│   └── cli.py        # argparse: scan / apply / undo / cache clear
└── tests/            # pytest; generated fixture images; FakeTrasher; e2e on tmp tree
```

**Pipeline**: discover → exact pass (all files) → perceptual pass (images, cache-aware) → family assembly + keeper suggestion + companions → HTML report → *user reviews, exports selection.json* → `apply` (re-verify → confirm with count+bytes → batched Trash → undo manifest) → `undo` if regretted.

**CLI**:
```
dupefinder scan PATH... [--exclude GLOB] [--materialize] [--threshold N] [-o report.html]
dupefinder apply selection.json [--dry-run]      # dry-run prints what would happen
dupefinder undo [SCAN_ID]                        # no arg = list restorable manifests
dupefinder cache clear
```
No config file — CLI flags only (unrequested configurability cut per Karpathy review).

## Implementation phases

Each phase ends with its **verify** criterion green before the next begins (goal-driven execution).

- **Phase 0 — risk spike (before building)**: in a scratch venv (`uv run --with rawpy --with pillow-heif ...`), read-only test on a few of the user's real CR3/CRAW + HEIC files. → *verify: rawpy `extract_thumb` returns a decodable JPEG from a real CR3/CRAW; pillow-heif opens an iPhone HDR HEIC; a real HEIC/JPEG export pair lands at pHash ≤ 4 while two different photos land ≥ 10. Thresholds recalibrated from these numbers if needed; exiftool contingency triggered if rawpy fails.*
- **Phase 1 — scaffold**: `uv init`, pyproject (5 deps), git init, README stub, spec doc (`docs/superpowers/specs/2026-07-09-dupefinder-design.md`). → *verify: `uv run python -c "import dupefinder"` and `uv run pytest` (empty suite) both succeed; first commit made.*
- **Phase 2 — models + discover**: denylist, stubs, hardlinks, companions, unicode. → *verify: unit tests cover each on a generated tmp tree.*
- **Phase 3 — hashing + cache**: streaming, partial-hash funnel, invalidation, batched writer. → *verify: tests incl. rehash-only-on-change and interrupted-scan resume.*
- **Phase 4 — imaging**: HEIC/JPEG/PNG decode, orientation, pHash+dHash, RAW preview w/ fallback, thumbnails. → *verify: tests incl. rotated-copy-matches-original.*
- **Phase 5 — grouping**: BK-tree clustering, families, format partitions, keeper heuristic, RAW metadata guard, burst/low-entropy flags. → *verify: tests incl. RAW+JPEG family never pre-checks the cross-format sibling.*
- **Phase 6 — report**: HTML gen. → *verify: test parses generated HTML and round-trips a selection.json.*
- **Phase 7 — trash/apply/undo**: FakeTrasher; keeper-survival validation; manifest atomicity; batched osascript trasher. → *verify: tests incl. tampered-selection (keeper checked for deletion) is rejected; hash-mismatch file is skipped.*
- **Phase 8 — cli + e2e**: synthetic tree (exact dupes, HEIC/JPEG pair, copy-names, hardlink pair, zero-byte, emoji filename). → *verify: full scan → report → selection → apply → undo round-trip passes with FakeTrasher.*
- **Phase 9 — manual verification + docs + commit**: checklist below; README with safety model. → *verify: manual checklist executed and results recorded in README.*

## Verification

1. `uv run pytest` — full suite green.
2. **Synthetic e2e** (automated): fixture script builds a tmp tree covering every case above; assert report contents, keeper choices, apply skips/trashes, undo restores.
3. **Manual checklist** (real system, cannot be automated):
   - Scan a real folder (e.g. `~/Downloads`) → open report in browser → thumbnails render, export works.
   - Apply a tiny selection → files appear in Trash → Finder "Put Back" restores one → `dupefinder undo` restores the rest.
   - Repeat on an external drive → files land in that volume's `.Trashes`.
   - Confirm one-time Automation permission prompt (Terminal → Finder) is handled gracefully.
4. **Real-data spot check**: run on a photo folder; verify a known HEIC/JPEG pair lands in one family as cross-format siblings (not flagged), and a known `X copy.jpeg` is pre-checked as surplus.

## Deferred (YAGNI, documented in README)

APFS clone detection via extent inspection; OCR-based screenshot discrimination; Time Machine exclusion of trashed files; scanning inside managed libraries; GUI app; scheduling/automation. **Cut in Karpathy review** (add back only if a real need appears): config file, `status` / `cache stats` / `cache prune` commands, NSFileManager trasher fallback, PyObjC-based iCloud materialization.

## Karpathy-guidelines review — addressed

Reviewed against the four guidelines (Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution): assumptions now stated explicitly (section above); speculative features cut (config file, extra cache/status commands, fallback trasher, PyObjC — dependency count 6 → 5); every phase given a concrete verify criterion. Surgical-changes rule maps to the tool's own guarantee: it never modifies anything outside the reviewed selection.
