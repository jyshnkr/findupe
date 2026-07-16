"""Single owner of the ~/.findupe/ data directory and the one-time, in-place
migration off a legacy ~/.dupefinder/ (index.db, scans/, undo/ move together
as one directory rename — they must stay consistent with each other).

resolve_data_home() is the only thing that touches disk, and only a caller
that's actually about to use the *default* location (no explicit path given)
should call it — never at import time, and never unconditionally from the
CLI entry point, so that overriding one of --db/--undo-dir/--scans-dir for a
given command bypasses the migration check entirely for that run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

LEGACY_DATA_HOME = Path.home() / ".dupefinder"
DATA_HOME = Path.home() / ".findupe"


def resolve_data_home(legacy: Path = LEGACY_DATA_HOME, new: Path = DATA_HOME) -> Path:
    """`new`, migrating a legacy directory into place on first use.

    No-op if `new` already exists (already migrated, or created independently)
    or if `legacy` doesn't exist (nothing to migrate) — `new` always wins and
    is never clobbered. The two optional params exist for isolated testing;
    production call sites always use the real home-directory defaults.
    """
    if not new.exists() and legacy.exists():
        try:
            os.rename(legacy, new)
            print(f"findupe: migrated {legacy} -> {new} (one-time)", file=sys.stderr)
        except FileNotFoundError:
            # lost a race with another process migrating the same legacy dir
            # at the same time — it already won, `new` exists now.
            pass
    return new
