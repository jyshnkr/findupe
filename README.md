# findupe

[![PyPI version](https://img.shields.io/pypi/v/findupe.svg)](https://pypi.org/project/findupe/)
[![Python versions](https://img.shields.io/pypi/pyversions/findupe.svg)](https://pypi.org/project/findupe/)
[![License: MIT](https://img.shields.io/pypi/l/findupe.svg)](https://github.com/jyshnkr/findupe/blob/main/LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://github.com/jyshnkr/findupe#install)

**See your duplicate photos side by side, pick what goes, and nothing disappears
without your OK.**

findupe finds **exact duplicates** (any file type, zero false positives) and
**same-photo-different-format duplicates** (HEIC / JPEG / RAW, re-encodes, resized
exports) on your Mac, then lets *you* review everything visually in an HTML report
before anything moves — to the real macOS **Trash**, never deleted outright.

Built for photographers: keeping `X.CR3` + `X.jpg` side by side is intentional and is
**never** flagged. Only surplus copies *within* a format (`X copy.jpg`, `X_2.jpg`,
a re-imported CR3) become deletion candidates.

![findupe's HTML report showing exact, strong-visual, and cross-format duplicate groups with thumbnails](https://raw.githubusercontent.com/jyshnkr/findupe/main/docs/assets/hero-report.png)

## Quickstart

```sh
pipx install findupe
findupe --demo     # scans a small bundled sample and opens the report — no setup needed
```

`--demo` gives you a real report to click through immediately. When you're ready to
point it at your own photos, the flow is the same one shown below:

![Terminal recording of scan, review, export, apply --dry-run, apply, and undo](https://raw.githubusercontent.com/jyshnkr/findupe/main/docs/assets/terminal-demo.gif)

1. `findupe scan ~/Pictures/inbox "/Volumes/Extreme SSD/photos"` — writes an HTML report.
2. Open the report, check the boxes you agree with (sensible ones are pre-checked),
   click **Export selection**.
3. `findupe apply <selection>.json --dry-run` to preview, then without `--dry-run` to
   move the checked files to the Trash (typed confirmation required).
4. `findupe undo` any time — restores from the Trash by content, not by guessing names.

Nothing is deleted outright: checked files go to the real Trash (recoverable, "Put
Back" works), and the last copy of anything always survives — enforced independently
of what the report UI shows you.

## Install

Requires macOS.

```sh
pipx install findupe
# or
uv tool install findupe
```

Then `findupe --help`. Running on a non-macOS platform refuses immediately with a
clear error — the Trash integration (Finder/AppleScript) and clone-detection notes
are macOS/APFS-specific.

## Workflow

`scan` writes two reports — one for images (exact + perceptual matching), one
for everything else (PDFs, text, archives, ... — exact-hash matches only,
since perceptual matching never applies to non-images). Review and apply each
independently.

```
1. findupe scan ~/Pictures/inbox "/Volumes/Extreme SSD/photos"
2. open report-images.html   # photo/image duplicates — thumbnails, adjust checkboxes
   open report-other.html    # everything else — plain checkbox + path + size rows
3.                           # click "Export selection" on each ->
                              #   findupe-selection-<id>-images.json
                              #   findupe-selection-<id>-other.json
4. findupe apply findupe-selection-<id>-images.json --dry-run   # preview
   findupe apply findupe-selection-<id>-other.json  --dry-run   # preview
5. findupe apply findupe-selection-<id>-images.json            # typed confirmation
   findupe apply findupe-selection-<id>-other.json             # typed confirmation
6. findupe undo                                             # list restore points
   findupe undo <manifest>                                  # put everything back
```

## How it works

<details open>
<summary><strong>How it decides two files are "the same"</strong></summary>

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

</details>

<details open>
<summary><strong>Safety model</strong></summary>

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

</details>

## Deliberately out of scope (v1)

Scanning inside Photos/Lightroom libraries · OCR screenshot discrimination · config
file · GUI · scheduling. (APFS clone detection shipped — see "How it works" above.)

## Contributing

Commit conventions, the release pipeline, and dev setup live in
[CONTRIBUTING.md](CONTRIBUTING.md).
