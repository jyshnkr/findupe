# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/). New entries are generated
automatically by [Commitizen](https://commitizen-tools.github.io/commitizen/)
from [Conventional Commits](https://www.conventionalcommits.org/) on every
qualifying push to `main` — the heading style below matches what it emits.

## v0.6.0 (2026-07-18)

### Feat

- show OCR text snippets in HTML report
- wire the OCR demoter into the scan CLI
- demote screenshot matches whose OCR text disagrees
- add macOS Vision OCR backend with text normalization and similarity
- add metadata-only screenshot predicate
- persist OCR and camera-EXIF fields in the scan cache (schema v3)
- detect camera EXIF in the perceptual pass
- add OCR fields to FileRecord

### Fix

- default has_camera_exif to None so cached EXIF survives the exact-pass cache merge

### Refactor

- apply CodeRabbit review nits (redundant set, zero-width escapes, test annotations)

## v0.5.0 (2026-07-17)

### Feat

- add --demo, friendlier no-args message, and --help epilog

## v0.4.0 (2026-07-16)

### BREAKING CHANGE

- the CLI command changes from `dupefinder` to `findupe`,
and the on-disk data directory moves from `~/.dupefinder/` to
`~/.findupe/` (auto-migrated in place on first run). Any scripts,
aliases, or cron jobs invoking `dupefinder` directly, or reading
`~/.dupefinder/` paths directly instead of through the CLI, need updating.

### Feat

- PyPI-publish CI (OIDC), and APFS clone detection
- rename project dupefinder -> findupe, honest space labels, PyPI-ready metadata

### Fix

- address PR review findings (Codex + CodeRabbit)

## v0.3.0 (2026-07-13)

### Feat

- persistent scan history, stats totals, and an HTML dashboard

## v0.2.1 (2026-07-12)

### Fix

- fail fast on bad scan roots, surface hash/decode errors loudly

## v0.2.0 (2026-07-11)

### Feat

- split HTML report into images/other categories

## v0.1.0 (2026-07-09)

### Feat

- exact-duplicate detection for any file type via size → 64KB-edge → full BLAKE2b funnel
- same-photo-across-formats detection (HEIC/JPEG/RAW, re-encodes, resized exports) via orientation-normalized pHash + dHash; keeping `X.CR3` + `X.jpg` side by side is never flagged
- self-contained offline HTML review report: side-by-side thumbnails, per-keeper pre-checked candidates, live reclaim counter, one-click selection export
- review-first `apply` to the real macOS Trash (all volumes), typed confirmation, full re-verification at apply time, last-copy-per-partition protection
- `undo` restores from an atomically-written manifest, immune to Finder's collision renames
- companions (Live Photo `.MOV`, `.XMP`/`.AAE` sidecars) ride along with their primary on trash and undo
- safety guards: Photos/Lightroom denylist, iCloud/Dropbox stub handling (`--materialize`), hardlink/symlink/zero-byte exclusion, burst/low-entropy flagging, cloud-sync badges
- persistent SQLite hash cache; re-scans only hash new/changed files
- validated end-to-end against a real 6,177-photo library; two rounds of real-data tuning plus a 20-agent adversarial code review (12 defects found and fixed) drove false positives to zero
