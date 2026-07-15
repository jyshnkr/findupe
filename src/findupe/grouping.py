"""Family assembly: exact groups + perceptual clusters → reviewable families.

Tier rules (calibrated on real files: Phase 0 spike + real-data verification on
6,177 photos from the user's EOS R6 II):
- strong visual edge: pHash ≤ 2 AND dHash ≤ 2 — true re-encodes measure 0
- possible edge:      pHash 3..8          — burst pairs measure ~4; review-only
- demotions to "possible" (never strong):
    * capture metadata present on both sides and different (bursts, brackets)
    * SubSecTimeOriginal present on both sides and different — separates burst
      frames shot within the SAME second (measured '75' vs '97' on real files;
      static-scene frames can hash identically at distance 0!)
    * either side is RAW and capture metadata is missing or differs
    * both sides RAW and mtime differs — RAW previews lack SubSec, but burst
      frames get distinct write times while true re-imports preserve mtime

Families are connected components of exact + strong edges — but surplus is NOT
computed per family: union-find transitivity can chain A~B~C where A and C were
never directly compared. Surplus lives only in same-format CLUSTERS built from
direct edges (exact-hash equality or a recorded strong pair). Everything else in
a partition is informational — shown, never pre-checkable.
"""

from __future__ import annotations

import re

import pybktree

from .clones import KeeperExtents
from .imaging import hamming
from .models import RAW_EXTS, Cluster, Family, FileRecord, FormatPartition

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

    if strong:
        a_raw = a.path.suffix.lower() in RAW_EXTS
        b_raw = b.path.suffix.lower() in RAW_EXTS
        if a_raw and b_raw:
            # RAW↔RAW is NEVER perceptually strong: every real-world RAW duplicate
            # is a byte-identical copy (exact tier); previews lack SubSec, and
            # exFAT card timestamps make same-second burst frames share mtime —
            # measured on real files. Bursts land here; humans review them.
            strong = False
        elif a.capture_subsec and b.capture_subsec and a.capture_subsec != b.capture_subsec:
            strong = False  # burst frames within the same second
        elif a_raw or b_raw:
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

    families: exact/strong-visual components; surplus only inside direct-edge
    same-format clusters. possible_families: review-only clusters.
    """
    idx = {id(r): i for i, r in enumerate(records)}
    uf = _UnionFind(len(records))
    strong_pairs: set[frozenset[int]] = set()

    for members in exact_groups.values():
        first = idx[id(members[0])]
        for m in members[1:]:
            uf.union(first, idx[id(m)])

    hashable = [
        r for r in records
        if r.phash is not None and r.dhash is not None
        and r.hash_error is None and r.hardlink_of is None
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
                        strong_pairs.add(frozenset((idx[id(a)], idx[id(b)])))
                    elif tier == "possible":
                        possible_pairs.append((a, b))

    components: dict[int, list[FileRecord]] = {}
    for r in records:
        components.setdefault(uf.find(idx[id(r)]), []).append(r)

    families = [
        _make_family(f"fam-{n:05d}", members, exact_groups, strong_pairs, idx)
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
                # review-only: no clusters, so surplus is structurally impossible
                FormatPartition(format=r.format, files=[r], clusters=[])
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
    strong_pairs: set[frozenset[int]],
    idx: dict[int, int],
) -> Family:
    by_format: dict[str, list[FileRecord]] = {}
    for r in members:
        by_format.setdefault(r.format, []).append(r)

    partitions = []
    cluster_n = 0
    for fmt in sorted(by_format):
        files = sorted(by_format[fmt], key=lambda r: str(r.path))
        # direct-edge clusters within the partition: exact-hash equality or a
        # recorded strong pair — never transitive family membership
        local = _UnionFind(len(files))
        for i, a in enumerate(files):
            for j in range(i + 1, len(files)):
                b = files[j]
                if (a.exact_hash and a.exact_hash == b.exact_hash) or (
                    frozenset((idx[id(a)], idx[id(b)])) in strong_pairs
                ):
                    local.union(i, j)
        comps: dict[int, list[FileRecord]] = {}
        for i, f in enumerate(files):
            comps.setdefault(local.find(i), []).append(f)
        clusters = []
        for group in comps.values():
            if len(group) < 2:
                continue  # singleton: informational member, nothing deletable
            keeper = choose_keeper(group)
            surplus = [f for f in group if f is not keeper]
            # Clone detection only ever applies to a surplus file that's BYTE-
            # IDENTICAL to the keeper (exact_hash match) — a cluster can also
            # contain files linked only by a strong-visual edge (re-encodes),
            # which by definition can never be an APFS clone of anything.
            exact_surplus = [
                f for f in surplus
                if f.exact_hash and keeper.exact_hash and f.exact_hash == keeper.exact_hash
            ]
            if exact_surplus:
                keeper_extents = KeeperExtents(keeper.path)
                for f in exact_surplus:
                    f.is_clone = keeper_extents.shares_with(f.path)
            clusters.append(Cluster(
                cluster_id=f"c{cluster_n:04d}",
                files=group,
                keeper=keeper,
                surplus=surplus,
            ))
            cluster_n += 1
        partitions.append(FormatPartition(format=fmt, files=files, clusters=clusters))

    exact_ids = {id(r) for grp in exact_groups.values() for r in grp}
    all_exact = all(id(r) in exact_ids for r in members)
    flags = []
    visual_cluster_too_big = any(
        len(c.files) > 3 and not all(id(f) in exact_ids for f in c.files)
        for p in partitions for c in p.clusters
    )
    if visual_cluster_too_big:
        flags.append("possible-burst")
    if any(_low_entropy(r) for r in members):
        flags.append("low-entropy")

    return Family(
        family_id=family_id,
        kind="exact" if all_exact else "visual",
        partitions=partitions,
        flags=flags,
    )
