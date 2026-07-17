# Contributing to findupe

## Dev setup

Requires macOS + [uv](https://docs.astral.sh/uv/). Python 3.11+ and all dependencies
(Pillow, pillow-heif, imagehash, rawpy, pybktree) are resolved automatically.

```sh
uv sync
uv run pytest          # 145+ tests
uv run findupe --help  # dev invocation — installed users just run `findupe --help`
```

First `apply` may trigger a one-time macOS permission prompt ("Terminal wants to control
Finder") — that's the Trash integration. If you deny it, apply aborts safely.

All state lives under `~/.findupe/`: the hash cache (`index.db`, re-scans only hash
new/changed files), scan history (`scans/`), and undo manifests (`undo/`).
`findupe cache clear` resets the hash cache only. If you have an existing
`~/.dupefinder/` from before the `findupe` rename, it's moved into place
automatically, once, the first time you run any command that doesn't override
`--db`/`--undo-dir`/`--scans-dir`.

Tests that run a scan must override `--db`/`--undo-dir`/`--scans-dir` to a `tmp_path`
fixture — omitting them silently pollutes a real `~/.findupe/` on whoever runs the suite.

## Commit conventions & releases

Commits to `main` follow [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` for user-facing additions, `fix:` for bug fixes, `chore:`/`docs:`/`test:`
for everything with no release impact. A qualifying push is picked up automatically by
[Commitizen](https://commitizen-tools.github.io/commitizen/) — it computes the next
[semantic version](https://semver.org/) (`feat` → minor, `fix`/`perf`/`refactor` → patch,
`feat!`/`BREAKING CHANGE` → major), updates `CHANGELOG.md`, tags the release, and a GitHub
Action turns that tag into a [GitHub Release](https://github.com/jyshnkr/findupe/releases)
with no manual step. See `.github/workflows/release.yml`.

## Design background

[docs/architecture.md](docs/architecture.md) has the matching tiers and safety-model
internals — start there before touching `grouping.py`, `imaging.py`, or `trash.py`.
