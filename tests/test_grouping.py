"""Grouping is pure logic — records are constructed directly, no files needed."""

from pathlib import Path

from dupefinder.grouping import build_families, choose_keeper
from dupefinder.models import FileRecord


def mk(
    path: str,
    size: int = 1000,
    mtime: int = 1_000_000,
    phash: int | None = None,
    dhash: int | None = None,
    capture_key: str | None = None,
    capture_subsec: str | None = None,
    exact_hash: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> FileRecord:
    rec = FileRecord(
        path=Path(path), size=size, mtime_ns=mtime, dev=1, inode=hash(path) & 0xFFFF,
        volume="/",
    )
    rec.phash, rec.dhash = phash, dhash
    rec.capture_key, rec.capture_subsec = capture_key, capture_subsec
    rec.exact_hash = exact_hash
    rec.width, rec.height = width, height
    return rec


PH = 0x0123_4567_89AB_CDEF  # arbitrary base hash with mixed bits


def surplus_paths(families) -> set[str]:
    return {
        str(f.path)
        for fam in families for p in fam.partitions for c in p.clusters for f in c.surplus
    }


def clusters_of(fam, fmt):
    return next(p for p in fam.partitions if p.format == fmt).clusters


def test_raw_jpeg_family_never_prechecks_cross_format_sibling():
    """The plan's Phase 5 verify criterion."""
    raw = mk("/pics/IMG_1.CR3", phash=PH, dhash=PH, capture_key="t|1|2|3|4")
    jpg = mk("/pics/IMG_1.jpg", phash=PH, dhash=PH, capture_key="t|1|2|3|4")
    families, possible = build_families([raw, jpg], {})
    assert len(families) == 1 and not possible
    fam = families[0]
    assert {p.format for p in fam.partitions} == {"cr3", "jpeg"}
    assert fam.surplus_count == 0  # cross-format siblings are never candidates


def test_within_format_copy_is_surplus():
    a = mk("/pics/X.jpg", phash=PH, dhash=PH, mtime=100)
    b = mk("/pics/X copy.jpg", phash=PH ^ 1, dhash=PH, mtime=200)  # re-encode, 1 bit off
    families, _ = build_families([a, b], {})
    (fam,) = families
    (cluster,) = clusters_of(fam, "jpeg")
    assert cluster.keeper is a
    assert cluster.surplus == [b]


def test_same_second_burst_frames_demoted_by_subsec():
    """Real-data regression: static-scene burst frames hash at distance 0 but
    carry different SubSecTimeOriginal ('75' vs '97' on the user's EOS R6 II)."""
    a = mk("/p/JSCL0048.JPG", phash=PH, dhash=PH,
           capture_key="2026:04:26 16:28:15|0.01|2.8|160|50.0", capture_subsec="75")
    b = mk("/p/JSCL0049.JPG", phash=PH, dhash=PH,
           capture_key="2026:04:26 16:28:15|0.01|2.8|160|50.0", capture_subsec="97")
    families, possible = build_families([a, b], {})
    assert families == [] and len(possible) == 1


def test_same_subsec_reencode_stays_strong():
    a = mk("/p/X.jpg", phash=PH, dhash=PH, capture_key="t|1", capture_subsec="75")
    b = mk("/p/X copy.jpg", phash=PH, dhash=PH, capture_key="t|1", capture_subsec="75")
    families, _ = build_families([a, b], {})
    assert len(families) == 1 and families[0].surplus_count == 1


def test_raw_pair_distinct_mtime_demoted():
    """RAW previews lack SubSec; distinct write times = distinct frames."""
    a = mk("/p/A.CR3", phash=PH, dhash=PH, capture_key="t|1", mtime=1_000)
    b = mk("/p/B.CR3", phash=PH, dhash=PH, capture_key="t|1", mtime=2_000)
    families, possible = build_families([a, b], {})
    assert families == [] and len(possible) == 1


def test_raw_pair_same_mtime_same_key_is_strong():
    """A Finder re-import preserves mtime — that's a real copy."""
    a = mk("/p/A.CR3", phash=PH, dhash=PH, capture_key="t|1", mtime=5_000)
    b = mk("/p/copy/A.CR3", phash=PH, dhash=PH, capture_key="t|1", mtime=5_000)
    families, _ = build_families([a, b], {})
    assert len(families) == 1 and families[0].surplus_count == 1


def test_no_transitive_surplus_bleed():
    """Two capture pairs merged into one family by cross-format bridges must NOT
    make one pair surplus of the other: surplus requires a DIRECT edge."""
    a_raw = mk("/p/A.CR3", phash=PH, dhash=PH, capture_key="tA|1", mtime=1)
    a_jpg = mk("/p/A.jpg", phash=PH, dhash=PH, capture_key="tA|1", mtime=1)
    b_raw = mk("/p/B.CR3", phash=PH ^ 3, dhash=PH, capture_key="tB|1", mtime=2)
    b_jpg = mk("/p/B.jpg", phash=PH ^ 3, dhash=PH, capture_key="tB|1", mtime=2)
    # bridge: a_jpg strong with b_jpg would be demoted (different keys) — but give
    # them identical keys to force the bridge, while RAWs differ by mtime+key
    b_jpg.capture_key = a_jpg.capture_key
    b_jpg.phash = PH
    families, _ = build_families([a_raw, a_jpg, b_raw, b_jpg], {})
    # whatever merged, no CR3 may ever be surplus: no direct CR3<->CR3 edge exists
    assert not any(p.endswith(".CR3") for p in surplus_paths(families))


def test_capture_key_conflict_demotes_to_possible():
    a = mk("/p/a.jpg", phash=PH, dhash=PH, capture_key="2026:01:01|x")
    b = mk("/p/b.jpg", phash=PH, dhash=PH, capture_key="2026:01:02|y")
    families, possible = build_families([a, b], {})
    assert families == [] and len(possible) == 1


def test_raw_without_metadata_never_strong():
    a = mk("/p/a.CR3", phash=PH, dhash=PH)
    b = mk("/p/b.CR3", phash=PH, dhash=PH)
    families, possible = build_families([a, b], {})
    assert families == [] and len(possible) == 1


def test_dhash_none_records_do_not_crash():
    a = mk("/p/a.jpg", phash=PH, dhash=None)
    b = mk("/p/b.jpg", phash=PH, dhash=None)
    families, possible = build_families([a, b], {})
    assert families == [] and possible == []


def test_exact_and_visual_bridge_one_family():
    a = mk("/p/a.jpg", phash=PH, dhash=PH, exact_hash="h1")
    b = mk("/p/b.jpg", phash=PH, dhash=PH, exact_hash="h1")
    c = mk("/p/c.jpg", phash=PH ^ 1, dhash=PH)  # visual-only match
    families, _ = build_families([a, b, c], {"h1": [a, b]})
    (fam,) = families
    assert fam.kind == "visual"  # not purely exact
    (cluster,) = clusters_of(fam, "jpeg")
    assert len(cluster.files) == 3 and len(cluster.surplus) == 2


def test_exact_nonimage_family():
    a = mk("/docs/report.pdf", exact_hash="h9")
    b = mk("/backup/report.pdf", exact_hash="h9")
    families, _ = build_families([a, b], {"h9": [a, b]})
    (fam,) = families
    assert fam.kind == "exact" and fam.surplus_count == 1


def test_exact_same_bytes_different_format_no_surplus():
    a = mk("/p/data.dat", exact_hash="h2")
    b = mk("/p/data.bin", exact_hash="h2")
    families, _ = build_families([a, b], {"h2": [a, b]})
    assert families[0].surplus_count == 0


def test_keeper_resolution_beats_age():
    small_old = mk("/p/x.jpg", mtime=1, width=100, height=100)
    big_new = mk("/p/y.jpg", mtime=999, width=4000, height=3000)
    assert choose_keeper([small_old, big_new]) is big_new


def test_keeper_older_wins_at_equal_resolution():
    old = mk("/p/x.jpg", mtime=1, width=100, height=100)
    new = mk("/p/y.jpg", mtime=2, width=100, height=100)
    assert choose_keeper([old, new]) is old


def test_keeper_clean_name_beats_copy_and_suffix():
    clean = mk("/p/IMG_1234.jpg", mtime=5)
    copy = mk("/p/IMG_1234 copy.jpg", mtime=1)
    suffixed = mk("/p/IMG_1234_2.jpg", mtime=1)
    assert choose_keeper([clean, copy, suffixed]) is clean


def test_plain_numbered_name_is_not_penalized():
    # IMG_1234 must not be treated as "copy of IMG"; only X_2-of-existing-X is
    a = mk("/p/IMG_1234.jpg", mtime=2)
    b = mk("/p/IMG_9999.jpg", mtime=1)
    assert choose_keeper([a, b]) is b  # falls through to oldest


def test_keeper_organized_path_beats_downloads():
    messy = mk("/Users/u/Downloads/x.jpg", mtime=1)
    tidy = mk("/Users/u/Photos/2026/x.jpg", mtime=9)
    assert choose_keeper([messy, tidy]) is tidy


def test_possible_burst_flag_on_large_visual_cluster():
    recs = [mk(f"/p/s{i}.jpg", phash=PH, dhash=PH, mtime=i) for i in range(5)]
    families, _ = build_families(recs, {})
    assert "possible-burst" in families[0].flags


def test_no_burst_flag_when_all_exact():
    recs = [mk(f"/p/e{i}.bin", exact_hash="h") for i in range(5)]
    families, _ = build_families(recs, {"h": recs})
    assert "possible-burst" not in families[0].flags


def test_low_entropy_flag():
    a = mk("/p/wall1.jpg", phash=0, dhash=0)
    b = mk("/p/wall2.jpg", phash=0, dhash=0)
    families, _ = build_families([a, b], {})
    assert "low-entropy" in families[0].flags


def test_no_duplicates_no_families():
    a = mk("/p/a.jpg", phash=PH, dhash=PH)
    b = mk("/p/b.jpg", phash=~PH & ((1 << 64) - 1), dhash=PH)
    families, possible = build_families([a, b], {})
    assert families == [] and possible == []
