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


def entry(path: Path, family="fam-00000", fmt="bin", cluster="c0000", companions=None) -> dict:
    return {
        "path": str(path), "size": path.stat().st_size, "blake2b": full_hash(path),
        "family": family, "format": fmt, "cluster": cluster,
        "companions": companions or [],
    }


def comp_entry(path: Path) -> dict:
    return {"path": str(path), "size": path.stat().st_size, "blake2b": full_hash(path)}


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
    sel["delete"][0]["companions"] = [comp_entry(sidecar)]
    trasher, undo_dir = env(tmp_path)
    plan, manifest_path = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert not sidecar.exists()
    manifest = json.loads(manifest_path.read_text())
    comp = next(e for e in manifest["entries"] if e.get("companion"))
    assert comp["status"] == "trashed"
    assert comp["blake2b"]  # scan-time hash travels into the manifest


def test_modified_companion_left_in_place(tmp_path):
    """A companion that changed since the scan must not be trashed unverified."""
    sel, keep, dele = make_selection(tmp_path)
    sidecar = tmp_path / "delete.xmp"
    sidecar.write_text("<xmp/>")
    sel["delete"][0]["companions"] = [comp_entry(sidecar)]
    sidecar.write_text("<xmp>edited after scan</xmp>")
    trasher, undo_dir = env(tmp_path)
    plan, _ = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert not dele.exists()      # primary was verified and trashed
    assert sidecar.exists()       # modified companion untouched
    assert any("companion" in reason for _, reason in plan.skipped)


def test_shared_companion_trashed_once(tmp_path):
    """A sidecar attached to two deleted primaries appears once in the plan."""
    content = b"same-bytes"
    keep = tmp_path / "keep.bin"; keep.write_bytes(content)
    d1 = tmp_path / "d1.bin"; d1.write_bytes(content)
    d2 = tmp_path / "d2.bin"; d2.write_bytes(content)
    sidecar = tmp_path / "shared.xmp"; sidecar.write_text("<xmp/>")
    ce = comp_entry(sidecar)
    sel = {"schema_version": "1", "scan_id": "s2",
           "delete": [entry(d1, companions=[ce]), entry(d2, companions=[ce])],
           "keep": [entry(keep)]}
    trasher, undo_dir = env(tmp_path)
    plan, _ = apply_selection(sel, trasher, undo_dir=undo_dir)
    assert [c["path"] for c in plan.companions] == [str(sidecar)]
    assert not sidecar.exists()


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
    sel["delete"][0]["companions"] = [comp_entry(sidecar)]
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


def test_malformed_selection_is_fatal_not_crash(tmp_path):
    """Review finding: hand-edited/corrupt selections must fail loudly, not raise."""
    cases = [
        "not a dict",
        {"schema_version": "1", "delete": "not-a-list", "keep": []},
        {"schema_version": "1", "delete": [{"path": 42}], "keep": []},
        {"schema_version": "1", "delete": [{"path": "/x", "size": "big",
                                            "blake2b": "h", "family": "f", "format": "b"}], "keep": []},
        {"schema_version": "1",
         "delete": [{"path": "/x", "size": 1, "blake2b": "h", "family": "f",
                     "format": "b", "companions": "nope"}], "keep": []},
        {"schema_version": "1",
         "delete": [{"path": "/x", "size": 1, "blake2b": "h", "family": "f",
                     "format": "b", "companions": [{"path": 1}]}], "keep": []},
    ]
    for sel in cases:
        plan = build_apply_plan(sel)  # must not raise
        assert plan.fatal, f"expected fatal for {sel!r}"
