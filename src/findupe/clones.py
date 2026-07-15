"""APFS clone detection via physical-extent comparison (F_LOG2PHYS_EXT).

Spiked and verified on real hardware (2026-07-15): a `cp -c` clone reports
the exact same physical device offset as its original; an independent copy
does not; two independently-written files with identical content don't
overlap either (ruling out any content-based-dedup false positive). A
partially-edited clone still shares extents for its unedited remainder —
detecting that correctly needs interval overlap, not exact-offset equality,
which is what `shares_physical_extents` does.

Only ever called within an already-confirmed exact-hash duplicate cluster
(the only files clones can ever be) — never scan-wide, since the fcntl call
has real per-file cost.

Fails safe in BOTH directions, and the two directions are not symmetric:
- Any error (non-APFS volume, permission issue, file vanished) -> treat as
  "not a clone". A detection failure must never HIDE reclaimable space that
  genuinely exists.
- A *successful* call that reports device offset 0 for a file's first
  extent -> also treated as "not a clone" and the whole probe for that file
  is discarded. This is the more dangerous direction: a filesystem that
  returns a "successful" but meaningless placeholder offset (rather than a
  real error) for an unsupported case could make two genuinely-independent
  files look identical, causing this code to falsely claim trashing one
  frees no space — undercounting, the opposite of the error case, and worse
  than not having this feature at all. Empirically verified on this real
  machine (2026-07-15): FAT32 and exFAT volumes both raise a clean
  OSError(ENOTSUP) rather than returning a placeholder — this guard is
  deliberate defense-in-depth for filesystems/drivers not tested here, not
  a response to an observed failure.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

_F_LOG2PHYS_EXT = 65
_STRUCT_FMT = "<Iqq"  # struct log2phys, #pragma pack(4): uint, off_t, off_t


def _extent_map(path: Path) -> list[tuple[int, int]] | None:
    """[(device_offset, length), ...] covering the whole file. None on any
    error, or if the kernel reports a suspicious 0 device offset."""
    import fcntl  # Unix-only; imported lazily so this module stays importable
    # on Windows — cli.py's sys.platform guard runs before any code path that
    # would actually reach this function, but module-level imports elsewhere
    # in the chain (cli -> grouping -> clones) happen before that guard does.
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            size = os.fstat(fd).st_size
        except OSError:
            return None
        if size == 0:
            return None
        extents: list[tuple[int, int]] = []
        file_offset = 0
        while file_offset < size:
            query = struct.pack(_STRUCT_FMT, 0, size - file_offset, file_offset)
            try:
                result = fcntl.fcntl(fd, _F_LOG2PHYS_EXT, query)
            except OSError:
                return None  # unsupported (non-APFS, network volume, etc.)
            _flags, contig, dev_off = struct.unpack(_STRUCT_FMT, result)
            if dev_off <= 0:
                # a real device offset is never 0 (reserved/boot area) — treat
                # as a placeholder/unsupported response, not real data
                return None
            if contig <= 0:
                break  # avoid an infinite loop if the kernel reports no progress
            extents.append((dev_off, contig))
            file_offset += contig
        return extents
    finally:
        os.close(fd)


def _overlaps(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(
        da < db + lb and db < da + la
        for da, la in a
        for db, lb in b
    )


class KeeperExtents:
    """One file's extent map, computed once and checked against many others —
    for a cluster with N surplus files, the keeper would otherwise get
    re-probed N times for no reason."""

    def __init__(self, path: Path) -> None:
        self._extents = _extent_map(path)

    def shares_with(self, other: Path) -> bool:
        """True if `other` shares ANY physical storage with this extent map —
        an APFS clone relationship, even if `other` has since been partially
        edited (the unedited remainder still overlaps). False (never raises)
        if either side's extent map can't be trusted (unreadable, unsupported
        volume, or a suspicious response) or if they genuinely share nothing."""
        if not self._extents:
            return False
        other_extents = _extent_map(other)
        if not other_extents:
            return False
        return _overlaps(self._extents, other_extents)


def shares_physical_extents(a: Path, b: Path) -> bool:
    """Convenience wrapper for a single one-off comparison. See KeeperExtents
    for reusing one side's extent map across multiple comparisons."""
    return KeeperExtents(a).shares_with(b)
