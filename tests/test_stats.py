import json
from pathlib import Path


def write_manifest(undo_dir: Path, name: str, scan_id: str, created_at: str, entries: list) -> Path:
    undo_dir.mkdir(parents=True, exist_ok=True)
    path = undo_dir / name
    path.write_text(json.dumps({
        "schema_version": "1", "scan_id": scan_id, "created_at": created_at,
        "volumes": ["/"], "entries": entries,
    }))
    return path


def entry(path="/p/a.bin", size=1000, status="trashed", companion=False) -> dict:
    return {"path": path, "size": size, "blake2b": "cc", "status": status, "companion": companion}


def test_aggregate_undo_totals_counts_trashed_bytes(tmp_path):
    from dupefinder.stats import aggregate_undo_totals

    undo_dir = tmp_path / "undo"
    write_manifest(undo_dir, "m1.json", "s1", "2026-07-01T00:00:00+00:00", [
        entry("/p/a.bin", 1000), entry("/p/b.bin", 2000),
    ])

    totals = aggregate_undo_totals(undo_dir)

    assert totals.files_trashed_net == 2
    assert totals.bytes_reclaimed_net == 3000


def test_restored_not_counted_as_net_reclaimed(tmp_path):
    from dupefinder.stats import aggregate_undo_totals

    undo_dir = tmp_path / "undo"
    write_manifest(undo_dir, "m1.json", "s1", "2026-07-01T00:00:00+00:00", [
        entry("/p/a.bin", 1000, status="restored"),
    ])

    totals = aggregate_undo_totals(undo_dir)

    assert totals.files_restored == 1
    assert totals.bytes_reclaimed_net == 0
    assert totals.files_trashed_net == 0


def test_companion_entries_count_toward_reclaimed(tmp_path):
    from dupefinder.stats import aggregate_undo_totals

    undo_dir = tmp_path / "undo"
    write_manifest(undo_dir, "m1.json", "s1", "2026-07-01T00:00:00+00:00", [
        entry("/p/a.xmp", 500, companion=True),
    ])

    totals = aggregate_undo_totals(undo_dir)

    assert totals.files_trashed_net == 1
    assert totals.bytes_reclaimed_net == 500


def test_failed_entries_excluded_from_reclaimed(tmp_path):
    from dupefinder.stats import aggregate_undo_totals

    undo_dir = tmp_path / "undo"
    write_manifest(undo_dir, "m1.json", "s1", "2026-07-01T00:00:00+00:00", [
        entry("/p/a.bin", 1000, status="failed: OSError"),
    ])

    totals = aggregate_undo_totals(undo_dir)

    assert totals.files_failed == 1
    assert totals.bytes_reclaimed_net == 0
    assert totals.files_trashed_net == 0


def test_stats_totals_empty_state(tmp_path):
    from dupefinder.stats import aggregate_undo_totals

    totals = aggregate_undo_totals(tmp_path / "undo")

    assert totals.files_trashed_net == 0
    assert totals.bytes_reclaimed_net == 0
    assert totals.applies == 0


def test_applied_scan_ids_from_manifests(tmp_path):
    from dupefinder.stats import applied_scan_ids

    undo_dir = tmp_path / "undo"
    write_manifest(undo_dir, "m1.json", "scan-a", "2026-07-01T00:00:00+00:00", [entry()])
    write_manifest(undo_dir, "m2.json", "scan-b", "2026-07-02T00:00:00+00:00", [entry()])

    assert applied_scan_ids(undo_dir) == {"scan-a", "scan-b"}


def test_reclaimed_timeline_buckets_by_apply_date(tmp_path):
    from dupefinder.stats import reclaimed_timeline

    undo_dir = tmp_path / "undo"
    write_manifest(undo_dir, "m1.json", "s1", "2026-07-01T10:00:00+00:00",
                   [entry("/p/a.bin", 1000)])
    write_manifest(undo_dir, "m2.json", "s1", "2026-07-01T14:00:00+00:00",
                   [entry("/p/b.bin", 500)])
    write_manifest(undo_dir, "m3.json", "s2", "2026-07-02T09:00:00+00:00",
                   [entry("/p/c.bin", 2000, status="restored")])

    timeline = reclaimed_timeline(undo_dir)

    assert timeline == [("2026-07-01", 1500), ("2026-07-02", 0)]


def test_duplicates_timeline_one_point_per_scan():
    from dupefinder.ledger import ScanRecord
    from dupefinder.stats import duplicates_timeline

    records = [
        ScanRecord(scan_id="a", created_at="2026-07-01T10:00:00+00:00", roots=[],
                   duplicate_families=1, possible_matches=0, surplus_count=5,
                   surplus_bytes=100, categories={}, problems={}),
        ScanRecord(scan_id="b", created_at="2026-07-02T10:00:00+00:00", roots=[],
                   duplicate_families=2, possible_matches=0, surplus_count=8,
                   surplus_bytes=200, categories={}, problems={}),
    ]

    assert duplicates_timeline(records) == [("2026-07-01", 5), ("2026-07-02", 8)]


def test_render_stats_text_contains_totals(tmp_path):
    from dupefinder.stats import Totals, render_stats_text

    totals = Totals(scans_recorded=3, applies=2, files_trashed_net=5,
                     bytes_reclaimed_net=123456, files_restored=1, files_failed=0,
                     duplicates_found_total=40)

    text = render_stats_text([], totals, set())

    assert "5" in text and "123456" not in text  # bytes rendered human-readable, not raw
    assert "3" in text  # scans_recorded
    assert "40" in text and "across" in text  # cumulative-across-scans framing
