# Report split by category + release automation

## Context

`dupefinder scan` had been run for real and worked, but `report.html` mixed
every duplicate together — image duplicates next to PDF/text/archive
duplicates in one document. Separately, the project (pushed to GitHub as
`jyshnkr/dupefinder`, public) had zero release infrastructure: no tags, no
`CHANGELOG.md`, no CI, a static `version = "0.1.0"`, and no `LICENSE` file on
disk despite `pyproject.toml` declaring MIT. This spec covers both: splitting
the review report by file-type category, and adopting Conventional Commits +
Commitizen + a GitHub Action for fully automatic semantic-versioned releases.

Both were scoped via 2 parallel Explore agents (against the actual `report.py`/
`cli.py`/`models.py` source and the actual `git`/`gh` state), a Plan agent
(using current Commitizen/GitHub Actions docs rather than guessed config
syntax), then independently re-verified by re-reading the cited source and
test files directly before implementation.

## Locked requirements (from AskUserQuestion rounds)

**Report split:**
1. Exactly 2 categories: images (`FileRecord.is_image`) vs. everything else.
   No further sub-categorization.
2. Same folder, distinct filenames — `report.html` → `report-images.html` +
   `report-other.html`.
3. `apply` stays single-selection-file; the user runs it once per category.
   No selection.json schema change.
4. This is the new default `scan` behavior; the combined report is gone.
5. One scan run (one `scan_id`) still produces both files — never re-scan.

**Release automation:**
1. Conventional Commits (`feat:`/`fix:`/`chore:`) from here forward, driven by
   Commitizen (computes the semver bump, updates `CHANGELOG.md`, tags).
2. Fully automatic on every qualifying push to `main`.
3. First version: `0.1.0`, matching `pyproject.toml`, baseline for everything
   built through the adversarial-review-fixed, real-data-validated state.
4. GitHub Releases only — no PyPI, no wheel/sdist asset.
5. LICENSE copyright holder: `jyshnkr` (GitHub handle).

## Key technical decisions

| Decision | Choice | Why |
|---|---|---|
| Category rule | `any(rec.is_image for p in fam.partitions for rec in p.files)` | Non-image files can only ever form `kind="exact"` families (perceptual hashing is images-only); every deletion *candidate* is already category-homogeneous — the rule only decides which report an informational cross-format sibling appears under |
| Report architecture | `generate_report` (monolithic) → `_write_report(scan, families, possible, out_path, category, thumb)` + `generate_reports(scan, possible, base_out_path, thumb)` orchestrator | One render primitive, called twice from one scan; `possible` is images-only by construction so it's always `[]` for the "other" render |
| Export filename | `dupefinder-selection-<scan_id>-<category>.json` (added `-<category>`) | Both category reports share one `scan_id`; without the category suffix both reports' "Export selection" downloads would collide on the same filename |
| Undo manifest timestamp | `%Y%m%dT%H%M%S.%fZ` (microseconds, was seconds) | Direct consequence of per-category apply: two applies of the *same* `scan_id` landing in the same UTC second would otherwise silently overwrite one undo manifest with the other — found during implementation, fixed in `trash.py` |
| Commitizen version provider | `pep621` (not `uv`) | The `commitizen-action` Docker image has no `uv` on PATH; `pep621` edits `[project].version` natively |
| `v0.1.0` baseline | Manually tagged on `d1918d7` (last pre-tooling commit) rather than inferred from zero-tag state | No existing commit history is Conventional-Commits-formatted, so there's nothing to bump *from* without a starting tag; confirmed via local dry-run (`cz bump --dry-run` after a temporary tag) that this produces clean, predictable `0.1.0 → 0.2.0` math |
| Changelog heading style | `## v$version ($date)`, e.g. `## v0.1.0 (2026-07-09)` — not bracketed Keep a Changelog style | Matches Commitizen's actual emitted format (confirmed via the same dry-run), so the hand-authored `0.1.0` entry doesn't visually clash with future auto-generated entries |
| `major_version_zero = true` | `feat` bumps MINOR while pre-1.0, not MAJOR | Standard semver.org pre-1.0 semantics; avoids an early jump to 1.0.0 from an ordinary feature commit |

## Architecture

**Report split** — `src/dupefinder/report.py`:
```
category_output_paths(base) -> (base-images.html, base-other.html)
_is_image_family(fam) -> bool
_write_report(scan, families, possible, out_path, category, thumb)   # doc assembly, was inline in generate_report
generate_reports(scan, possible, base_out_path, thumb) -> (img_path, other_path)
```
`cli.py`'s `cmd_scan` calls `generate_reports` once, prints per-category
family/surplus/byte counts (computed from each category's own family list,
not the whole scan), and points to both exported-selection filenames.

**Release automation** — `.github/workflows/release.yml`: triggers on push to
`main`, skips its own `bump:` commits, runs `commitizen-tools/commitizen-action@0.27.1`
(bump + changelog + tag + push-back, using the default `GITHUB_TOKEN` — pushes
made with it don't re-trigger `push` workflows), then `softprops/action-gh-release@v3.0.1`
creates the Release from the newly-generated changelog increment, gated on
`steps.cz.outputs.version != steps.cz.outputs.previous_version` so a no-op run
(nothing to bump, both outputs equal) creates nothing. Note: the action always
sets `version` (= `cz version --project`, the static pyproject version) even
on a no-bump run — `version != ''` alone never blocks, since a no-increment
`cz bump` exits via the action's default `no_raise=21`; comparing against
`previous_version` is the real bump signal, confirmed against the pinned
action's `entrypoint.sh`.

## Verification

1. `uv run pytest -q` — 87 tests green (82 existing + 5 new for the report
   split; `test_full_round_trip` rewritten for two sequential per-category
   `apply` calls and two undo manifests instead of one).
2. Manual smoke test: scanned a small non-image-only tree
   (`--trash-dir` fake, non-destructive) — confirmed `report-images.html`
   renders the empty-state note, `report-other.html` shows both duplicate
   pairs with `PDF`/`TXT` format badges, correct per-category export
   filenames in each file's footer/JS.
3. Local Commitizen dry-run (temporary tag + throwaway empty `feat:` commit,
   reverted via `git reset --soft` + `git tag -d`, no working-tree changes
   touched): confirmed `0.1.0 → 0.2.0` MINOR bump and the exact changelog
   heading format, before writing anything durable.
4. Landing sequence: tag `v0.1.0` on `d1918d7` → push tag → one `chore:`
   commit with all Feature 2 tooling (expect a no-op workflow run, nothing to
   bump yet) → one `feat:` commit with the report-split code (expect the
   first real automated bump to `0.2.0` + Release).

## Deferred

Sub-categories beyond images/other; combined multi-file `apply`; PyPI
publishing; wheel/sdist release assets; `--single-report` escape hatch back to
the old combined view (add only if a real need appears).
