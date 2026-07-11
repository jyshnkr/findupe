# dupefinder

Safe duplicate finder & reviewer for macOS. Finds **exact duplicates** (any file type,
zero false positives) and **same-photo-different-format duplicates** (HEIC / JPEG / RAW,
re-encodes, resized exports), then lets *you* review everything visually in an HTML
report before anything moves — to the real macOS **Trash**, never deleted outright.

Built for photographers: keeping `X.CR3` + `X.jpg` side by side is intentional and is
**never** flagged. Only surplus copies *within* a format (`X copy.jpg`, `X_2.jpg`,
a re-imported CR3) become deletion candidates.

## Workflow

```
1. uv run dupefinder scan ~/Pictures/inbox "/Volumes/Extreme SSD/photos"
2. open report.html          # review side-by-side thumbnails, adjust checkboxes
3.                           # click "Export selection" -> dupefinder-selection-<id>.json
4. uv run dupefinder apply dupefinder-selection-<id>.json --dry-run   # preview
5. uv run dupefinder apply dupefinder-selection-<id>.json             # typed confirmation
6. uv run dupefinder undo                                             # list restore points
   uv run dupefinder undo <manifest>                                  # put everything back
```

## How it decides two files are "the same"

| Tier | Test | Shown as |
|---|---|---|
| Exact | size → 64KB-edges hash → full **BLAKE2b** | Exact duplicates (pre-checked per keeper rule) |
| Strong visual | **pHash ≤ 2 AND dHash ≤ 2** after EXIF-orientation normalization | Same image, multiple versions |
| Possible | pHash 3–8 | Review-only — no checkboxes, the tool will not touch these |

Thresholds were calibrated on real files: a HEIC→JPEG export measures distance **0**
(even resized); different photos measure ≥ 28. Burst frames are the treacherous case —
static-scene frames shot in the same second can hash **identically** — so three extra
guards demote them to review-only, verified against a real 6,177-photo library:

- capture metadata (time + exposure) must match for any strong match to form;
- **SubSecTimeOriginal** must match too — it differs between burst frames shot within
  the same second (`'75'` vs `'97'` on consecutive EOS R6 II frames);
- **RAW↔RAW pairs are never perceptually strong.** Every real-world RAW duplicate is a
  byte-identical copy (nobody re-encodes a CR3), so RAW deletion candidates come only
  from the exact tier — burst frames whose previews collide land in review-only.

RAW files are fingerprinted via their embedded JPEG preview (rawpy; exiftool fallback).
And "surplus" is computed only within *directly-matched* same-format clusters — a file
that merely shares a family through a chain of cross-format links renders as an
informational "sibling", never as a deletion candidate.

## Safety model

- **`scan` has no delete authority.** Deletion happens only through `apply`, which takes
  the selection file you exported from the report after human review.
- **Everything is re-verified at apply time** — every keeper and every candidate is
  re-checked (existence, size, full BLAKE2b). A file that changed since the scan is
  skipped; a keeper that changed rejects its whole partition; a selection that lists a
  keeper for deletion is rejected outright.
- **The last copy always survives**: at most `n-1` files of a (family, format) partition
  can be trashed, enforced independently of the report UI.
- **Real Trash, all volumes**: batched Finder AppleScript, so "Put Back" works — external
  drives use their own `.Trashes` (pre-flight checked; a volume without a working Trash is
  refused, never silently permanent-deleted).
- **Undo manifest written before anything moves** (atomic write), and `undo` re-locates
  files in the Trash by size + hash — immune to Finder's collision renames.
- **Never touched at all**: hardlinks (deleting one reclaims nothing — informational),
  Photos/Lightroom library internals (hard denylist), symlinks, iCloud dataless stubs
  (skipped and listed; `--materialize` downloads them on purpose), zero-byte files.
- **Companions ride along**: Live Photo `.MOV`s and `XMP`/`AAE` sidecars are trashed with
  their primary and restored with it on undo.
- **Flagged families are never pre-checked**: >3 visually-matched same-format files
  ("possible-burst") or near-uniform images ("low-entropy") require deliberate clicks.
- Files in iCloud/Dropbox-synced folders carry a ☁ badge — deleting them propagates to
  your other devices.

Known caveat: APFS **clones** are indistinguishable from true copies without deep extent
inspection — trashing a clone reclaims no space (the report footer says so too).

## Install / dev

Requires macOS + [uv](https://docs.astral.sh/uv/). Python 3.13 and all dependencies
(Pillow, pillow-heif, imagehash, rawpy, pybktree) are resolved automatically.

```
uv sync
uv run pytest          # 70 tests
uv run dupefinder --help
```

First `apply` may trigger a one-time macOS permission prompt ("Terminal wants to control
Finder") — that's the Trash integration. If you deny it, apply aborts safely.

The hash cache lives in `~/.dupefinder/index.db` (re-scans only hash new/changed files);
undo manifests in `~/.dupefinder/undo/`. `dupefinder cache clear` resets the cache.

## Commit conventions & releases

Commits to `main` follow [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` for user-facing additions, `fix:` for bug fixes, `chore:`/`docs:`/`test:`
for everything with no release impact. A qualifying push is picked up automatically by
[Commitizen](https://commitizen-tools.github.io/commitizen/) — it computes the next
[semantic version](https://semver.org/) (`feat` → minor, `fix`/`perf`/`refactor` → patch,
`feat!`/`BREAKING CHANGE` → major), updates `CHANGELOG.md`, tags the release, and a GitHub
Action turns that tag into a [GitHub Release](https://github.com/jyshnkr/dupefinder/releases)
with no manual step. See `.github/workflows/release.yml`.

## Deliberately out of scope (v1)

APFS clone detection via extent inspection · scanning inside Photos/Lightroom libraries ·
OCR screenshot discrimination · config file · GUI · scheduling. See
`docs/superpowers/specs/2026-07-09-dupefinder-design.md` for the full design + rationale.
