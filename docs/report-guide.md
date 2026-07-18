# Reading your report

`scan` writes an HTML report and does nothing else — it's the one place you make
decisions, and the only output `apply` will act on. This is a guide to what you're
looking at.

## The three sections

- **Exact duplicates** — byte-identical files. The suggested keeper is pre-selected to
  survive (its checkbox is disabled — you can't accidentally check it); the other
  copies are pre-checked for the Trash.
- **Same image, multiple versions** — perceptually identical across formats or exports
  (a HEIC and its JPEG export, a resized copy, a re-encode). Cross-format siblings are
  shown together but never suggested for deletion — keeping one JPEG and one RAW is
  the point, not a duplicate. If the family is flagged (see below), nothing in it is
  pre-checked.
- **Possible matches — review only** — visually similar but not confirmed (bursts,
  brackets, similar-but-different shots). These rows have no checkboxes at all;
  findupe will not touch them no matter what you click. If you decide one really is a
  duplicate, delete it yourself in Finder.

## Badges

| Badge | Meaning |
|---|---|
| `KEEPER` | The suggested survivor for this partition. Checkbox is disabled — `apply` also independently refuses to trash the last copy, so this isn't just a UI nicety. |
| `sibling` | A related file that isn't a copy of anything (e.g. the RAW half of a RAW+JPEG pair). Never deletable here. |
| `☁ synced` | Lives in an iCloud Drive/Desktop & Documents/Dropbox-synced folder. Deleting it propagates to your other devices, not just this Mac. |
| `⧉ clone — 0 B freed` | An APFS clone of the keeper (shares physical storage). Trashing it frees no disk space even after the Trash is emptied, so it's excluded from the report's reclaimable total — though you can still check it if you want it gone for organizational reasons. Detection isn't foolproof; see the report footer for the exact caveats. |
| `+ filename` | A companion — a Live Photo `.MOV` or an `XMP`/`AAE` sidecar — that rides along: trashed with this file, restored with it on undo. |

A family header can also carry a flag:

- **`possible-burst`** — more than 3 visually-matched files in one same-format
  cluster. Often genuine burst-mode shots, not duplicates — nothing in the family is
  pre-checked, so you decide file-by-file.
- **`low-entropy`** — one or more near-uniform images (e.g. a blank wall, a solid
  color) whose hashes are less reliable as a similarity signal. Same treatment:
  nothing pre-checked.
- **`text-differs`** — this pair's pHash matched, but on-screen text (via macOS Vision
  OCR) didn't agree closely enough to confirm they're the same screenshot — demoted to
  review-only rather than suggested as a duplicate. Expand the "OCR text" details under
  each file to see what was recognized.

## Making and exporting your selection

Checkboxes reflect findupe's suggestion on load — pre-checked means "findupe thinks
this is safe to delete," not "this is already deleted." Nothing happens until you:

1. Adjust any boxes you disagree with (the live counter at the top tracks how much
   you've selected).
2. Click **⬇ Export selection** — downloads a `findupe-selection-<id>-<category>.json`
   file. This is a plan, not an action.
3. Run `findupe apply <that file> --dry-run` to preview, then again without
   `--dry-run` to actually move files to the Trash.

`apply` re-verifies every entry in the selection against the live filesystem before
touching anything — see [Architecture → Safety model](architecture.md#safety-model)
for exactly what that re-check covers.

## See also

- [Architecture](architecture.md) — how matches are computed, and the full safety model
- [FAQ](faq.md) — common questions
- [How-to](how-to.md) — CLI recipes beyond the basic scan → review → apply flow
