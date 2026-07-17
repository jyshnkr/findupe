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

findupe flags two kinds of duplicates: files that are byte-for-byte identical, and
photos that look like the same shot even across formats or exports (HEIC, JPEG, RAW).
It's tuned to leave burst shots and RAW+JPEG pairs alone — those are usually
intentional, not duplicates — and everything is re-checked right before anything
actually moves, not just at scan time.

Matching tiers, calibration numbers, and the full safety model live in
[docs/architecture.md](docs/architecture.md).

## Documentation

- [Reading your report](docs/report-guide.md) — sections, badges, and flags explained
- [Architecture](docs/architecture.md) — matching tiers and the full safety model
- [FAQ](docs/faq.md) — common questions
- [How-to](docs/how-to.md) — recipes: multi-root scans, excluding paths, history, stats, undo
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, commit conventions, release pipeline
