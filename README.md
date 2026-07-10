# dupefinder

Safe duplicate finder & reviewer for macOS. Finds exact duplicates (any file type) and
same-photo-different-format duplicates (HEIC/JPEG/RAW), then lets **you** review them
visually in an HTML report before anything is moved — to the real macOS Trash, never
deleted outright.

Built for photographers: keeping `X.CR3` + `X.jpeg` side by side is intentional and never
flagged; only surplus copies *within* a format (`X copy.jpeg`, re-imports) are candidates.

Status: under construction. See `docs/superpowers/specs/2026-07-09-dupefinder-design.md`.

## Usage

```
uv run dupefinder scan PATH... [--exclude GLOB] [--materialize] [--threshold N] [-o report.html]
uv run dupefinder apply selection.json [--dry-run]
uv run dupefinder undo [SCAN_ID]
uv run dupefinder cache clear
```
