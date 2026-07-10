"""Family assembly: exact groups + perceptual clusters → reviewable families.

Tier rules (calibrated on real files in the Phase 0 spike):
- strong visual edge: pHash ≤ 2 AND dHash ≤ 2 — true re-encodes measure 0
- possible edge:      pHash 3..8          — real burst pairs measure ~4; review-only
- demotions to "possible" (never strong):
    * either side is RAW and capture metadata is missing or differs (red-team:
      embedded previews can collide across different captures)
    * both sides have capture metadata and it differs (bursts, brackets)

Families are connected components of exact + strong edges. Each family is
partitioned by format; the keeper heuristic suggests one survivor per partition
and everything else becomes pre-checkable surplus. Cross-format siblings are
separate partitions — never surplus, never pre-checked.
"""

from __future__ import annotations

import re

import pybktree

from .imaging import hamming
from .models import RAW_EXTS, Family, FileRecord, FormatPartition

THRESHOLD_HIGH = 2
THRESHOLD_POSSIBLE = 8

_COPY_NAME = re.compile(r"(?i)(\s+copy(\s*\d+)?|\(\d+\))$")
_COPY_SUFFIX = re.compile(r"(?i)^[\s_\-]*(copy(\s*\d+)?|\d{1,3}|\(\d+\))$")
_MESSY_DIRS = {"downloads", "desktop", "tmp", "temp", ".temporaryitems"}


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _is_copy_name(stem: str) -> bool:
    return bool(_COPY_NAME.search(stem))


def _extends_sibling_stem(stem: str, sibling_stems: set[str]) -> bool:
    """True if stem looks like <sibling> + copy-suffix (X_2, X copy, X(1))."""
    low = stem.casefold()
    for other in sibling_stems:
        o = other.casefold()
        if low != o and low.startswith(o) and _COPY_SUFFIX.match(low[len(o):]):
            return True
    return False


def _in_messy_dir(rec: FileRecord) -> bool:
    return any(part.casefold() in _MESSY_DIRS for part in rec.path.parts)


def choose_keeper(files: list[FileRecord]) -> FileRecord:
    stems = {f.path.stem for f in files}

    def key(rec: FileRecord):
        return (
            -((rec.width or 0) * (rec.height or 0)),   # highest resolution
            _is_copy_name(rec.path.stem),               # clean name over "X copy"
            _extends_sibling_stem(rec.path.stem, stems),  # "X_2" loses to "X"
            _in_messy_dir(rec),                         # organized path
            rec.mtime_ns,                               # oldest
            len(str(rec.path)),
            str(rec.path),                              # deterministic tiebreak
        )

    return min(files, key=key)


def _low_entropy(rec: FileRecord) -> bool:
    """Near-uniform images (white walls, black frames) hash degenerately."""
    if rec.phash is None or rec.dhash is None:
        return False
    return (
        rec.phash.bit_count() <= 2 or rec.phash.bit_count() >= 62
        or rec.dhash in (0, (1 << 64) - 1)
    )


def _edge_tier(a: FileRecord, b: FileRecord) -> str | None:
    dp = hamming(a.phash, b.phash)
    if dp > THRESHOLD_POSSIBLE:
        return None
    dd = hamming(a.dhash, b.dhash)
    strong = dp <= THRESHOLD_HIGH and dd <= THRESHOLD_HIGH

    a_raw = a.path.suffix.lower() in RAW_EXTS
    b_raw = b.path.suffix.lower() in RAW_EXTS
    if a_raw or b_raw:
        if not (a.capture_key and b.capture_key and a.capture_key == b.capture_key):
            strong = False
    elif a.capture_key and b.capture_key and a.capture_key != b.capture_key:
        strong = False

    return "strong" if strong else "possible"


def build_families(
    records: list[FileRecord],
    exact_groups: dict[str, list[FileRecord]],
    threshold_possible: int = THRESHOLD_POSSIBLE,
) -> tuple[list[Family], list[Family]]:
    """Return (families, possible_families).

    families: exact/strong-visual components, partitioned by format with keepers.
    possible_families: review-only clusters from possible edges across families.
    """
    idx = {id(r): i for i, r in enumerate(records)}
    uf = _UnionFind(len(records))

    for members in exact_groups.values():
        first = idx[id(members[0])]
        for m in members[1:]:
            uf.union(first, idx[id(m)])

    hashable = [
        r for r in records
        if r.phash is not None and r.hash_error is None and r.hardlink_of is None
    ]
    by_phash: dict[int, list[FileRecord]] = {}
    for r in hashable:
        by_phash.setdefault(r.phash, []).append(r)

    possible_pairs: list[tuple[FileRecord, FileRecord]] = []
    if by_phash:
        tree = pybktree.BKTree(hamming, list(by_phash))
        seen_phash_pairs: set[tuple[int, int]] = set()
        for ph, recs in by_phash.items():
            for _dist, other_ph in tree.find(ph, threshold_possible):
                pair = (min(ph, other_ph), max(ph, other_ph))
                if pair in seen_phash_pairs:
                    continue
                seen_phash_pairs.add(pair)
                candidates = (
                    [(a, b) for i, a in enumerate(recs) for b in recs[i + 1:]]
                    if ph == other_ph
                    else [(a, b) for a in recs for b in by_phash[other_ph]]
                )
                for a, b in candidates:
                    tier = _edge_tier(a, b)
                    if tier == "strong":
                        uf.union(idx[id(a)], idx[id(b)])
                    elif tier == "possible":
                        possible_pairs.append((a, b))

    components: dict[int, list[FileRecord]] = {}
    for r in records:
        components.setdefault(uf.find(idx[id(r)]), []).append(r)

    families = [
        _make_family(f"fam-{n:05d}", members, exact_groups)
        for n, members in enumerate(
            sorted((m for m in components.values() if len(m) > 1), key=lambda m: str(m[0].path))
        )
    ]

    # possible edges whose endpoints did not end up in the same strong family
    puf = _UnionFind(len(records))
    cross = [
        (a, b) for a, b in possible_pairs
        if uf.find(idx[id(a)]) != uf.find(idx[id(b)])
    ]
    for a, b in cross:
        puf.union(idx[id(a)], idx[id(b)])
    pcomp: dict[int, set[int]] = {}
    for a, b in cross:
        for r in (a, b):
            pcomp.setdefault(puf.find(idx[id(r)]), set()).add(idx[id(r)])
    possible_families = []
    for n, member_ids in enumerate(sorted(pcomp.values(), key=min)):
        members = [records[i] for i in sorted(member_ids)]
        fam = Family(
            family_id=f"poss-{n:05d}",
            kind="possible",
            partitions=[
                # review-only: every file is its own keeper; surplus is always empty
                FormatPartition(format=r.format, files=[r], keeper=r, surplus=[])
                for r in members
            ],
            flags=["review-only"],
        )
        possible_families.append(fam)

    return families, possible_families


def _make_family(
    family_id: str,
    members: list[FileRecord],
    exact_groups: dict[str, list[FileRecord]],
) -> Family:
    by_format: dict[str, list[FileRecord]] = {}
    for r in members:
        by_format.setdefault(r.format, []).append(r)

    partitions = []
    for fmt in sorted(by_format):
        files = sorted(by_format[fmt], key=lambda r: str(r.path))
        keeper = choose_keeper(files)
        partitions.append(FormatPartition(
            format=fmt,
            files=files,
            keeper=keeper,
            surplus=[f for f in files if f is not keeper],
        ))

    exact_ids = {id(r) for grp in exact_groups.values() for r in grp}
    all_exact = all(id(r) in exact_ids for r in members)
    flags = []
    visual_partition_too_big = any(
        len(p.files) > 3 and not all(id(f) in exact_ids for f in p.files)
        for p in partitions
    )
    if visual_partition_too_big:
        flags.append("possible-burst")
    if any(_low_entropy(r) for r in members):
        flags.append("low-entropy")

    return Family(
        family_id=family_id,
        kind="exact" if all_exact else "visual",
        partitions=partitions,
        flags=flags,
    )
