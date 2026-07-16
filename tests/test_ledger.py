import json
from pathlib import Path

from findupe.grouping import build_families
from findupe.models import ScanResult
from findupe.report import _is_image_family
from test_grouping import mk


def _scan_with_one_family(tmp_path, scan_id="20260712-160000"):
    """One exact-duplicate pair of a non-image format -> one 'other' family."""
    a = mk(str(tmp_path / "data" / "a.bin"), exact_hash="cc")
    b = mk(str(tmp_path / "data" / "b.bin"), exact_hash="cc")
    families, possible = build_families([a, b], {"cc": [a, b]})
    scan = ScanResult(
        scan_id=scan_id, roots=[tmp_path / "data"], families=families,
        skipped_stubs=[Path("/p/stub.jpg")], skipped_managed=[],
        errors=[], hardlink_notes=[], zero_byte=[], hash_errors=[],
    )
    img_families = [f for f in families if _is_image_family(f)]
    other_families = [f for f in families if not _is_image_family(f)]
    return scan, possible, img_families, other_families


def _write_reports(tmp_path):
    img = tmp_path / "report-images.html"
    other = tmp_path / "report-other.html"
    img.write_text("<html>images report</html>")
    other.write_text("<html>other report</html>")
    return img, other


def test_record_scan_writes_meta_and_copies_both_reports(tmp_path):
    from findupe.ledger import record_scan

    scan, possible, img_families, other_families = _scan_with_one_family(tmp_path)
    img_path, other_path = _write_reports(tmp_path)
    scans_dir = tmp_path / "scans"

    scan_dir = record_scan(scan, possible, img_families, other_families,
                            (img_path, other_path), scans_dir=scans_dir)

    assert scan_dir == scans_dir / scan.scan_id
    meta = json.loads((scan_dir / "meta.json").read_text())
    assert meta["scan_id"] == scan.scan_id
    assert (scan_dir / "report-images.html").read_text() == "<html>images report</html>"
    assert (scan_dir / "report-other.html").read_text() == "<html>other report</html>"


def test_record_scan_meta_fields(tmp_path):
    from findupe.ledger import record_scan

    scan, possible, img_families, other_families = _scan_with_one_family(tmp_path)
    report_paths = _write_reports(tmp_path)
    scan_dir = record_scan(scan, possible, img_families, other_families,
                            report_paths, scans_dir=tmp_path / "scans")

    meta = json.loads((scan_dir / "meta.json").read_text())
    assert meta["schema_version"] == "1"
    assert meta["scan_id"] == "20260712-160000"
    assert meta["roots"] == [str(tmp_path / "data")]
    assert meta["duplicate_families"] == 1
    assert meta["possible_matches"] == len(possible)
    assert meta["surplus_count"] == 1
    assert meta["surplus_bytes"] == 1000  # mk() default size
    assert meta["categories"] == {
        "images": {"families": 0, "surplus_count": 0, "surplus_bytes": 0},
        "other": {"families": 1, "surplus_count": 1, "surplus_bytes": 1000},
    }
    assert meta["problems"] == {
        "skipped_stubs": 1, "skipped_managed": 0, "hardlinks": 0,
        "zero_byte": 0, "read_errors": 0, "hash_errors": 0,
    }


def test_record_scan_writes_meta_last_so_a_copy_failure_leaves_no_meta(tmp_path):
    """A crash/failure mid-archive must leave no meta.json — that's the signal
    list_scans uses to skip a partial directory."""
    from findupe.ledger import record_scan

    scan, possible, img_families, other_families = _scan_with_one_family(tmp_path)
    img_path, other_path = _write_reports(tmp_path)
    other_path.unlink()  # the second copy will now fail with FileNotFoundError

    try:
        record_scan(scan, possible, img_families, other_families,
                    (img_path, other_path), scans_dir=tmp_path / "scans")
        assert False, "expected the missing source report to raise"
    except OSError:
        pass

    scan_dir = tmp_path / "scans" / scan.scan_id
    assert not (scan_dir / "meta.json").exists()


def test_list_scans_sorted_by_scan_id(tmp_path):
    from findupe.ledger import list_scans, record_scan

    scans_dir = tmp_path / "scans"
    for scan_id in ("20260712-180000", "20260712-160000", "20260712-170000"):
        scan, possible, imgf, otherf = _scan_with_one_family(tmp_path, scan_id=scan_id)
        report_paths = _write_reports(tmp_path)
        record_scan(scan, possible, imgf, otherf, report_paths, scans_dir=scans_dir)

    records = list_scans(scans_dir)

    assert [r.scan_id for r in records] == [
        "20260712-160000", "20260712-170000", "20260712-180000",
    ]


def test_list_scans_skips_corrupt_dir_without_crashing(tmp_path):
    from findupe.ledger import SCAN_SCHEMA_VERSION, list_scans, record_scan

    scans_dir = tmp_path / "scans"
    scan, possible, imgf, otherf = _scan_with_one_family(tmp_path, scan_id="20260712-160000")
    record_scan(scan, possible, imgf, otherf, _write_reports(tmp_path), scans_dir=scans_dir)

    # missing meta.json entirely
    (scans_dir / "20260712-161000").mkdir()
    # invalid JSON
    bad_json_dir = scans_dir / "20260712-162000"
    bad_json_dir.mkdir()
    (bad_json_dir / "meta.json").write_text("{not valid json")
    # wrong schema_version
    bad_schema_dir = scans_dir / "20260712-163000"
    bad_schema_dir.mkdir()
    (bad_schema_dir / "meta.json").write_text(
        f'{{"schema_version": "{SCAN_SCHEMA_VERSION}999", "scan_id": "20260712-163000"}}'
    )

    records = list_scans(scans_dir)

    assert [r.scan_id for r in records] == ["20260712-160000"]


def test_list_scans_skips_dir_with_schema_valid_but_missing_field(tmp_path):
    """A meta.json that's valid JSON with the correct schema_version but
    missing a required field (e.g. truncated write despite the atomic-write
    guard) must be skipped like any other corrupt record, not crash the
    whole listing with an uncaught KeyError."""
    from findupe.ledger import SCAN_SCHEMA_VERSION, list_scans, record_scan

    scans_dir = tmp_path / "scans"
    scan, possible, imgf, otherf = _scan_with_one_family(tmp_path, scan_id="20260712-160000")
    record_scan(scan, possible, imgf, otherf, _write_reports(tmp_path), scans_dir=scans_dir)

    incomplete_dir = scans_dir / "20260712-164000"
    incomplete_dir.mkdir()
    (incomplete_dir / "meta.json").write_text(
        json.dumps({"schema_version": SCAN_SCHEMA_VERSION})  # scan_id, roots, etc. missing
    )

    records = list_scans(scans_dir)  # must not raise

    assert [r.scan_id for r in records] == ["20260712-160000"]


def test_load_scan_returns_none_for_missing(tmp_path):
    from findupe.ledger import load_scan

    assert load_scan("does-not-exist", scans_dir=tmp_path / "scans") is None


def test_read_record_reports_missing_report_as_none(tmp_path):
    from findupe.ledger import load_scan, record_scan

    scans_dir = tmp_path / "scans"
    scan, possible, imgf, otherf = _scan_with_one_family(tmp_path)
    record_scan(scan, possible, imgf, otherf, _write_reports(tmp_path), scans_dir=scans_dir)
    (scans_dir / scan.scan_id / "report-other.html").unlink()

    rec = load_scan(scan.scan_id, scans_dir=scans_dir)

    assert rec is not None
    assert rec.report_paths["images"] is not None
    assert rec.report_paths["other"] is None
