# Architecture

Technical depth on how findupe matches files and what keeps deletion safe. If
you're deciding whether to install the tool, you don't need this — the
[README](../README.md) covers that. This is for people who want the internals:
contributors, or anyone deciding whether to trust it with their photo library.

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
- **APFS clones are detected where possible** (`⧉ clone — 0 B freed` badge, via physical
  extent comparison — `F_LOG2PHYS_EXT`) and excluded from the reclaimable total, since
  trashing one frees nothing while its keeper survives. Detection isn't foolproof (some
  volumes/setups can't be probed, and only clone-of-keeper is checked, not
  clone-of-another-candidate) — an undetected clone still reclaims no space when
  trashed, same as before this existed (the report footer explains this too).

## See also

- [Reading your report](report-guide.md) — sections, badges, and flags explained
- [FAQ](faq.md) — common questions, including what's deliberately out of scope
- [How-to](how-to.md) — recipes for scan history, multi-root scans, stats, and more
- [CONTRIBUTING.md](../CONTRIBUTING.md) — dev setup and release process
