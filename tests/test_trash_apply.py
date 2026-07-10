import json
from pathlib import Path

from dupefinder.hashing import full_hash
from dupefinder.trash import (
    FakeTrasher,
    FinderTrasher,
    apply_selection,
    build_apply_plan,
    list_manifests,
    undo,
)


def entry(path: Path, family="fam-00000", fmt="bin", companions=None) -> dict:
    return {
        "path": str(path), "size": path.stat().st_size, "blake2b": full_hash(path),
        "family": family, "format": fmt, "companions": companions or [],
    }


def make_selection(tmp_path, content=b"same-bytes") -> tuple[dict, Path, Path]:
    keep = tmp_path / "keep.bin"
    dele = tmp_path / "delete.bin"
    keep.write_bytes(content)
    dele.write_bytes(content)
    sel = {"schema_version": "1", "scan_id": "s1",
           "delete": [entry(dele)], "keep": [entry(keep)]}
    return sel, keep, dele


def env(tmp_path):
    return FakeTrasher(tmp_path / "trash"), tmp_path / "undo"


def test_happy_path_trashes_and_writes_manifest(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    trasher, undo_dir = env(tmp_path)
    plan, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert plan.fatal is None and plan.skipped == []
    assert not dele.exists() and keep.exists()
    assert (trasher.trash_dir / "delete.bin").exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["entries"][0]["status"] == "trashed"
    assert list_manifests(undo_dir) == [manifest_path]


def test_keeper_in_delete_list_is_fatal(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    sel["delete"].append(entry(keep))  # tampered selection
    trasher, undo_dir = env(tmp_path)
    plan, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert plan.fatal and "keeper" in plan.fatal
    assert manifest_path is None
    assert keep.exists() and dele.exists()
    assert trasher.calls == []


def test_keeper_modified_rejects_partition(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    keep.write_bytes(b"changed after scan!")
    trasher, undo_dir = env(tmp_path)
    plan, _ = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert plan.to_trash == []
    assert dele.exists()
    assert any("keeper" in reason for _, reason in plan.skipped)
    assert plan.rejected_families


def test_candidate_modified_is_skipped_others_proceed(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    other = tmp_path / "delete2.bin"
    other.write_bytes(b"same-bytes")
    sel["delete"].append(entry(other))
    dele.write_bytes(b"user edited this since the scan")
    trasher, undo_dir = env(tmp_path)
    plan, _ = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert dele.exists()  # mismatch -> untouched
    assert not other.exists()  # verified -> trashed
    assert any("changed" in reason for _, reason in plan.skipped)


def test_delete_without_keeper_partition_is_skipped(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    sel["keep"] = []
    trasher, undo_dir = env(tmp_path)
    plan, _ = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert plan.to_trash == [] and dele.exists()


def test_companions_ride_along(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    sidecar = tmp_path / "delete.xmp"
    sidecar.write_text("<xmp/>")
    sel["delete"][0]["companions"] = [str(sidecar)]
    trasher, undo_dir = env(tmp_path)
    plan, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert not sidecar.exists()
    manifest = json.loads(manifest_path.read_text())
    comp = next(e for e in manifest["entries"] if e.get("companion"))
    assert comp["status"] == "trashed"


def test_dry_run_moves_nothing(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    trasher, undo_dir = env(tmp_path)
    plan, manifest_path = apply_selection(sel, trasher, dry_run=True, undo_dir=undo_dir)
    assert manifest_path is None
    assert dele.exists() and trasher.calls == []
    assert len(plan.to_trash) == 1  # the plan still reports what WOULD happen


def test_undo_restores_files(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    sidecar = tmp_path / "delete.xmp"
    sidecar.write_text("<xmp/>")
    sel["delete"][0]["companions"] = [str(sidecar)]
    trasher, undo_dir = env(tmp_path)
    _, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)

    results = dict(undo(manifest_path, trasher=trasher, undo_dir=undo_dir))
    assert results[str(dele)] == "restored"
    assert results[str(sidecar)] == "restored"
    assert dele.exists() and sidecar.exists()
    manifest = json.loads(manifest_path.read_text())
    assert all(e["status"] == "restored" for e in manifest["entries"])


def test_undo_never_overwrites_existing(tmp_path):
    sel, keep, dele = make_selection(tmp_path)
    trasher, undo_dir = env(tmp_path)
    _, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)
    dele.write_bytes(b"a brand-new file took this path")
    results = dict(undo(manifest_path, trasher=trasher, undo_dir=undo_dir))
    assert "already exists" in results[str(dele)]
    assert dele.read_bytes() == b"a brand-new file took this path"


def test_undo_finds_file_despite_trash_rename(tmp_path):
    # Finder renames on collision; FakeTrasher does too. Hash-match must find it.
    sel, keep, dele = make_selection(tmp_path)
    trasher, undo_dir = env(tmp_path)
    (trasher.trash_dir / "delete.bin").write_bytes(b"pre-existing trash item, different bytes")
    _, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert not dele.exists()
    results = dict(undo(manifest_path, trasher=trasher, undo_dir=undo_dir))
    assert results[str(dele)] == "restored"
    assert dele.read_bytes() == b"same-bytes"


def test_preflight_refuses_volume_without_trashes():
    problems = FinderTrasher.preflight({"/", "/Volumes/definitely-not-mounted-xyz"})
    assert "/" not in problems
    assert "PERMANENTLY" in problems["/Volumes/definitely-not-mounted-xyz"]


def test_bad_schema_and_empty_selection_are_fatal(tmp_path):
    assert build_apply_plan({"schema_version": "999"}).fatal
    assert build_apply_plan({"schema_version": "1", "delete": [], "keep": []}).fatal
