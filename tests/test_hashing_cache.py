import os
from pathlib import Path

from findupe.cache import Cache
from findupe.discover import discover
from findupe.hashing import group_exact


def scan(root) -> list:
    return discover([root]).records


def test_exact_groups_found(tree):
    root = tree({
        "a.txt": "same content",
        "sub/b.txt": "same content",
        "c.txt": "different",
    })
    groups = group_exact(scan(root))
    assert len(groups) == 1
    (members,) = groups.values()
    assert {m.path.name for m in members} == {"a.txt", "b.txt"}


def test_same_size_same_edges_different_middle(tree):
    # partial hash (first/last 64KB) collides; full hash must separate them
    body = bytearray(300 * 1024)
    a = bytes(body)
    body[150 * 1024] = 0xFF
    b = bytes(body)
    root = tree({"a.bin": a, "b.bin": b, "a2.bin": a})
    groups = group_exact(scan(root))
    assert len(groups) == 1
    (members,) = groups.values()
    assert {m.path.name for m in members} == {"a.bin", "a2.bin"}


def test_hardlinks_not_candidates(tree):
    root = tree({"orig.bin": "x" * 1000})
    os.link(root / "orig.bin", root / "hard.bin")
    groups = group_exact(scan(root))
    assert groups == {}


def test_unreadable_file_is_error_not_crash(tree):
    root = tree({"a.bin": "same", "b.bin": "same", "c.bin": "same"})
    (root / "b.bin").chmod(0o000)
    try:
        records = scan(root)
        groups = group_exact(records)
        errored = [r for r in records if r.hash_error]
        assert len(errored) == 1
        (members,) = groups.values()
        assert {m.path.name for m in members} == {"a.bin", "c.bin"}
    finally:
        (root / "b.bin").chmod(0o644)


def test_cache_avoids_rehash(tree, tmp_path_factory):
    root = tree({"a.bin": "dup", "b.bin": "dup"})
    db = tmp_path_factory.mktemp("db") / "index.db"

    with Cache(db) as cache:
        group_exact(scan(root), cache=cache)
        assert cache.misses == 2 and cache.hits == 0

    with Cache(db) as cache:
        groups = group_exact(scan(root), cache=cache)
        assert cache.hits == 2 and cache.misses == 0
        assert len(groups) == 1


def test_cache_invalidates_on_change(tree, tmp_path_factory):
    root = tree({"a.bin": "dup", "b.bin": "dup"})
    db = tmp_path_factory.mktemp("db") / "index.db"
    with Cache(db) as cache:
        group_exact(scan(root), cache=cache)

    (root / "a.bin").write_text("dup")  # same content, new mtime
    os.utime(root / "a.bin", ns=(1, 999_999_999_000_000_000))
    with Cache(db) as cache:
        group_exact(scan(root), cache=cache)
        assert cache.misses == 1 and cache.hits == 1


def test_stale_perceptual_fields_replaced_on_change(tree, tmp_path_factory):
    """A changed file must not keep its old phash via the stage-merge COALESCE."""
    root = tree({"a.bin": "v1-content-x", "b.bin": "v1-content-x"})
    db = tmp_path_factory.mktemp("db") / "index.db"
    with Cache(db) as cache:
        recs = scan(root)
        recs[0].phash = 12345  # simulate perceptual stage having run
        group_exact(recs, cache=cache)
        cache.store([recs[0]])

    (root / recs[0].path.name).write_text("v2-changed!!")
    with Cache(db) as cache:
        recs2 = scan(root)
        changed = next(r for r in recs2 if r.path.name == recs[0].path.name)
        changed.exact_hash = "newhash"
        cache.store([changed])  # phash unknown (None) for the new version
        row = cache.conn.execute(
            "SELECT phash, exact_hash FROM files WHERE path LIKE ?",
            (f"%{changed.path.name}",),
        ).fetchone()
        assert row == (None, "newhash")


def test_high_bit_phash_survives_cache_round_trip(tree, tmp_path_factory):
    """Unsigned 64-bit hashes with the top bit set must not overflow SQLite."""
    root = tree({"a.jpg": "not-really-image"})
    db = tmp_path_factory.mktemp("db") / "index.db"
    big = 0xFEDC_BA98_7654_3210  # > 2^63
    with Cache(db) as cache:
        (rec,) = scan(root)
        rec.phash, rec.dhash = big, (1 << 64) - 1
        cache.store([rec])
    with Cache(db) as cache:
        (rec2,) = scan(root)
        cached = cache.lookup(rec2)
        assert cached.phash == big and cached.dhash == (1 << 64) - 1


def test_interrupted_scan_resumes_from_cache(tree, tmp_path_factory):
    root = tree({"a.bin": "dup", "b.bin": "dup", "c.bin": "dup"})
    db = tmp_path_factory.mktemp("db") / "index.db"
    records = scan(root)

    with Cache(db) as cache:
        group_exact(records[:2], cache=cache)  # "interrupted" after two files

    with Cache(db) as cache:
        groups = group_exact(scan(root), cache=cache)
        assert cache.hits == 2 and cache.misses == 1
        (members,) = groups.values()
        assert len(members) == 3
