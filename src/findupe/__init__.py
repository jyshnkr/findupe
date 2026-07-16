"""findupe — safe duplicate finder & reviewer for macOS."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _read_version() -> str:
    try:
        return version("findupe")
    except PackageNotFoundError:
        # No installed dist-info (e.g. running straight from a checkout without
        # `uv sync`/`pip install -e .` first) — fall back to pyproject.toml so
        # there's still a single source of truth for the version.
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        try:
            return tomllib.loads(pyproject.read_text())["project"]["version"]
        except (OSError, KeyError):
            return "0.0.0+unknown"


__version__ = _read_version()
