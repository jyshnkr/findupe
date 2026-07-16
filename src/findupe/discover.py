"""File discovery: walk user-chosen roots safely and produce FileRecords.

Safety behaviors (see spec):
- never descends into managed libraries (.photoslibrary, Lightroom, …) — recorded, not scanned
- never follows symlinks (no loops, no escaping the chosen roots)
- iCloud dataless stubs are skipped + recorded unless materialize=True
- hardlinks (same dev+inode) are recorded once; later paths become informational notes
- sidecars (XMP/AAE/THM) and Live Photo MOVs attach to their primary as companions
"""

from __future__ import annotations

import fnmatch
import os
import plistlib
import stat as stat_mod
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .models import (
    LIVE_PHOTO_VIDEO_EXTS,
    MANAGED_LIBRARY_SUFFIXES,
    PILLOW_EXTS,
    RAW_EXTS,
    SIDECAR_EXTS,
    SKIP_DIR_NAMES,
    FileRecord,
    norm_path,
)

# stat.SF_DATALESS exists on Python 3.13+/macOS; keep the raw constant as a guard.
SF_DATALESS = getattr(stat_mod, "SF_DATALESS", 0x40000000)

_HOME = Path.home()
_CLOUD_SYNC_ROOTS: tuple[Path, ...] = (
    _HOME / "Library" / "Mobile Documents",   # iCloud Drive
    _HOME / "Library" / "CloudStorage",       # Dropbox / Google Drive / OneDrive
)
# iCloud "Desktop & Documents" sync mirrors these two into Mobile Documents.
_ICLOUD_DESKTOP_DOCS = _HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Desktop"


@dataclass
class DiscoverResult:
    records: list[FileRecord] = field(default_factory=list)
    skipped_stubs: list[Path] = field(default_factory=list)
    skipped_managed: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    hardlink_notes: list[tuple[Path, Path]] = field(default_factory=list)  # (dupe path, first path)
    zero_byte: list[Path] = field(default_factory=list)


def volume_root(path: Path) -> str:
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return f"/{parts[1]}/{parts[2]}"
    return "/"


@lru_cache(maxsize=64)
def volume_uuid(mount: str) -> str:
    """Filesystem identity for cache keys. A different drive mounted at the same
    path must not inherit cached hashes — the mount path alone can't tell them
    apart. Falls back to the mount path if diskutil is unavailable."""
    try:
        out = subprocess.run(
            ["diskutil", "info", "-plist", mount],
            capture_output=True, timeout=15,
        )
        if out.returncode == 0:
            info = plistlib.loads(out.stdout)
            u = info.get("VolumeUUID") or info.get("DiskUUID")
            if u:
                return str(u)
    except (OSError, subprocess.TimeoutExpired, plistlib.InvalidFileException):
        pass
    return mount


def is_cloud_synced(path: Path) -> bool:
    for root in _CLOUD_SYNC_ROOTS:
        if path.is_relative_to(root):
            return True
    if _ICLOUD_DESKTOP_DOCS.exists() and (
        path.is_relative_to(_HOME / "Desktop") or path.is_relative_to(_HOME / "Documents")
    ):
        return True
    return False


def is_dataless(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def _is_managed_library(name: str) -> bool:
    return name.lower().endswith(MANAGED_LIBRARY_SUFFIXES)


def discover(
    roots: list[Path],
    exclude_globs: list[str] | None = None,
    materialize: bool = False,
) -> DiscoverResult:
    result = DiscoverResult()
    excludes = exclude_globs or []
    seen_inodes: dict[tuple[int, int], Path] = {}
    seen_paths: set[str] = set()  # NFC-normalized, case-folded: overlapping roots / APFS case quirks

    for root in roots:
        root = root.expanduser().resolve()
        if not root.is_dir():
            result.errors.append((root, "not a directory or not mounted"))
            continue
        if _is_managed_library(root.name):
            result.skipped_managed.append(root)
            continue
        _walk(root, excludes, materialize, seen_inodes, seen_paths, result)

    _attach_companions(result)
    return result


def _walk(
    top: Path,
    excludes: list[str],
    materialize: bool,
    seen_inodes: dict[tuple[int, int], Path],
    seen_paths: set[str],
    result: DiscoverResult,
) -> None:
    try:
        entries = list(os.scandir(top))
    except OSError as e:
        result.errors.append((top, str(e)))
        return

    for entry in entries:
        path = Path(entry.path)
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name in SKIP_DIR_NAMES:
                    continue
                if _is_managed_library(entry.name):
                    result.skipped_managed.append(path)
                    continue
                if any(fnmatch.fnmatch(entry.path, g) or fnmatch.fnmatch(entry.name, g) for g in excludes):
                    continue
                _walk(path, excludes, materialize, seen_inodes, seen_paths, result)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.name == ".DS_Store":
                continue
            if any(fnmatch.fnmatch(entry.path, g) or fnmatch.fnmatch(entry.name, g) for g in excludes):
                continue

            # exact-NFC path dedup only — casefolding here would silently drop
            # genuinely distinct files (a.txt vs A.TXT) on case-SENSITIVE volumes
            key = norm_path(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)

            st = entry.stat(follow_symlinks=False)
            if st.st_size == 0:
                result.zero_byte.append(path)
                continue

            stub = is_dataless(st)
            if stub and not materialize:
                result.skipped_stubs.append(path)
                continue

            ino_key = (st.st_dev, st.st_ino)
            if ino_key in seen_inodes and st.st_nlink <= 1:
                # same physical file reached twice (overlapping roots, or a
                # case-insensitive volume aliasing the same path spelling)
                continue

            vol = volume_root(path)
            rec = FileRecord(
                path=path,
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                dev=st.st_dev,
                inode=st.st_ino,
                volume=vol,
                volume_uuid=volume_uuid(vol),
                is_cloud_stub=stub,
                cloud_synced=is_cloud_synced(path),
            )

            if st.st_nlink > 1 and ino_key in seen_inodes:
                rec.hardlink_of = seen_inodes[ino_key]
                result.hardlink_notes.append((path, seen_inodes[ino_key]))
            else:
                seen_inodes[ino_key] = path

            result.records.append(rec)
        except OSError as e:
            result.errors.append((path, str(e)))


# Live Photo HEIC and MOV are written moments apart; an unrelated same-stem video
# (vacation.jpg + vacation.mov from different sources) is typically far away in time.
# Failing to pair is safe (the MOV just stays an ordinary record); over-pairing
# would trash an unrelated video — so pair conservatively.
_LIVE_PHOTO_MTIME_WINDOW_NS = 10 * 1_000_000_000


def _attach_companions(result: DiscoverResult) -> None:
    """Sidecars (XMP/AAE/THM) and Live Photo MOVs become companions of their primary.

    Pairing rule: same directory (exact) + same stem (case-insensitive). MOV/MP4
    pairs only with a HEIC/JPEG primary whose mtime is within 10s (Live Photo);
    otherwise it stays a normal record. Companions are full FileRecords so their
    size and hash travel through the selection JSON and undo manifest for
    verification.

    The parent directory is NOT case-folded, only the stem is: on a
    case-sensitive APFS volume, `/Photos/A` and `/Photos/a` are genuinely
    distinct directories, and folding them together could attach a sidecar
    from one to an unrelated primary in the other, trashing it incorrectly.
    On the far more common case-insensitive-APFS default, the filesystem
    itself never presents two such directories in the same scan, so this
    costs nothing there.
    """
    by_stem: dict[tuple[str, str], list[FileRecord]] = {}
    for rec in result.records:
        by_stem.setdefault(
            (norm_path(rec.path.parent), rec.path.stem.casefold()), []
        ).append(rec)

    companion_paths: set[Path] = set()
    for group in by_stem.values():
        sidecars = [r for r in group if r.path.suffix.lower() in SIDECAR_EXTS]
        movs = [r for r in group if r.path.suffix.lower() in LIVE_PHOTO_VIDEO_EXTS]
        primaries = [
            r for r in group
            if r.path.suffix.lower() in RAW_EXTS
            or r.path.suffix.lower() in PILLOW_EXTS
        ]
        if not primaries:
            continue  # orphan sidecars/videos stay ordinary records
        for r in sidecars:
            for p in primaries:
                p.companions.append(r)
            companion_paths.add(r.path)
        for r in movs:
            live_primaries = [
                p for p in primaries
                if p.format in ("heic", "jpeg")
                and abs(p.mtime_ns - r.mtime_ns) <= _LIVE_PHOTO_MTIME_WINDOW_NS
            ]
            if live_primaries:
                for p in live_primaries:
                    p.companions.append(r)
                companion_paths.add(r.path)

    if companion_paths:
        result.records = [r for r in result.records if r.path not in companion_paths]
