"""End-to-end: synthetic tree -> scan -> report -> selection -> apply -> undo."""

import json
import os
from pathlib import Path

import io

from PIL import Image

from findupe.cli import main
from conftest import gradient_image
from test_report import InputCollector, simulate_js_export


def _img_bytes(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def build_tree(root: Path) -> None:
    (root / "backup").mkdir(parents=True)
    # three visually distinct, photo-like images (uniform colors would trip the
    # low-entropy safety flag — by design)
    img = gradient_image()
    img2 = gradient_image().transpose(Image.Transpose.ROTATE_180)
    img3 = gradient_image().transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    # exact duplicate pair (non-image)
    (root / "report.pdf").write_bytes(b"%PDF-1.4 fake but identical" * 100)
    (root / "backup" / "report.pdf").write_bytes(b"%PDF-1.4 fake but identical" * 100)
    # cross-format strong family (must never be candidates)
    img.save(root / "shot.heic", "HEIF", quality=80)
    img.save(root / "shot.jpg", "JPEG", quality=90)
    # within-format copy-named exact duplicate
    photo = _img_bytes(img2, "JPEG", quality=90)
    (root / "photo.jpg").write_bytes(photo)
    (root / "photo copy.jpg").write_bytes(photo)
    # hardlink pair (never a candidate)
    (root / "linked.bin").write_bytes(b"linked-payload" * 50)
    os.link(root / "linked.bin", root / "linked2.bin")
    # zero-byte + emoji-named exact pair
    (root / "empty.txt").write_bytes(b"")
    cafe = _img_bytes(img3, "PNG")
    (root / "café 📷.png").write_bytes(cafe)
    (root / "café 📷 copy.png").write_bytes(cafe)


def test_full_round_trip(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    build_tree(root)
    db = tmp_path / "index.db"
    undo_dir = tmp_path / "undo"
    trash_dir = tmp_path / "trash"
    scans_dir = tmp_path / "scans"
    report = tmp_path / "report.html"
    img_report = tmp_path / "report-images.html"
    other_report = tmp_path / "report-other.html"
    base = ["--db", str(db), "--undo-dir", str(undo_dir), "--trash-dir", str(trash_dir),
            "--scans-dir", str(scans_dir)]

    # -- scan: now writes two category-scoped reports from one scan
    rc = main(base + ["scan", str(root), "-o", str(report), "--workers", "0"])
    assert rc == 0
    img_text = img_report.read_text()
    other_text = other_report.read_text()
    assert "shot.heic" in img_text and "shot.jpg" in img_text
    assert "report.pdf" in other_text and "photo copy.jpg" not in other_text

    img_parser = InputCollector()
    img_parser.feed(img_text)
    other_parser = InputCollector()
    other_parser.feed(other_text)

    img_cands = [i for i in img_parser.inputs if i.get("class") == "cand"]
    img_cand_paths = {c["data-path"] for c in img_cands}
    # cross-format siblings and hardlinks are never candidates
    assert not any(p.endswith(("shot.heic", "shot.jpg")) for p in img_cand_paths)
    assert not any("linked" in p for p in img_cand_paths)
    # the copy-named files lost the keeper contest
    assert any(p.endswith("photo copy.jpg") for p in img_cand_paths)
    assert any(p.endswith("café 📷 copy.png") for p in img_cand_paths)
    assert all("checked" in c for c in img_cands)  # unflagged families are pre-checked

    other_cands = [i for i in other_parser.inputs if i.get("class") == "cand"]
    other_cand_paths = {c["data-path"] for c in other_cands}
    assert any(p.endswith("report.pdf") for p in other_cand_paths)
    assert not any("linked" in p for p in other_cand_paths)
    assert all("checked" in c for c in other_cands)

    # -- export both selections exactly as each report's JS would, then apply
    #    each once (per-category apply — the locked workflow decision)
    img_sel = simulate_js_export(img_parser.inputs)
    img_sel["scan_id"] = "e2e"
    img_sel_path = tmp_path / "selection-images.json"
    img_sel_path.write_text(json.dumps(img_sel))

    other_sel = simulate_js_export(other_parser.inputs)
    other_sel["scan_id"] = "e2e"
    other_sel_path = tmp_path / "selection-other.json"
    other_sel_path.write_text(json.dumps(other_sel))

    # -- dry-run moves nothing
    rc = main(base + ["apply", str(img_sel_path), "--dry-run"])
    assert rc == 0
    rc = main(base + ["apply", str(other_sel_path), "--dry-run"])
    assert rc == 0
    assert (root / "photo copy.jpg").exists()
    assert (root / "report.pdf").exists() and (root / "backup" / "report.pdf").exists()

    # -- real apply, once per category, each with typed confirmation
    monkeypatch.setattr("builtins.input", lambda *_: "trash")
    rc = main(base + ["apply", str(img_sel_path)])
    assert rc == 0
    rc = main(base + ["apply", str(other_sel_path)])
    assert rc == 0

    assert not (root / "photo copy.jpg").exists()
    assert not (root / "café 📷 copy.png").exists()
    assert (root / "photo.jpg").exists()
    assert (root / "shot.heic").exists() and (root / "shot.jpg").exists()
    assert (root / "linked.bin").exists() and (root / "linked2.bin").exists()
    # exactly one of the two PDFs survived
    assert (root / "report.pdf").exists() ^ (root / "backup" / "report.pdf").exists()

    # -- undo restores everything: one manifest per category apply
    manifests = list((undo_dir).glob("*.json"))
    assert len(manifests) == 2
    for m in manifests:
        rc = main(base + ["undo", m.name])
        assert rc == 0
    assert (root / "photo copy.jpg").exists()
    assert (root / "café 📷 copy.png").exists()
    assert (root / "report.pdf").exists() and (root / "backup" / "report.pdf").exists()

    # -- second scan is cache-warm and still succeeds (doesn't re-inspect output)
    rc = main(base + ["scan", str(root), "-o", str(report), "--workers", "0"])
    assert rc == 0


def test_refuses_to_run_on_non_macos(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")

    rc = main(["stats"])

    assert rc == 2
    assert "requires macOS" in capsys.readouterr().err


def test_apply_refuses_tampered_selection(tmp_path, capsys):
    keep = tmp_path / "k.bin"
    keep.write_bytes(b"x" * 10)
    from findupe.hashing import full_hash
    e = {"path": str(keep), "size": 10, "blake2b": full_hash(keep),
         "family": "f", "format": "bin", "companions": []}
    sel = {"schema_version": "1", "scan_id": "t", "delete": [e], "keep": [e]}
    sel_path = tmp_path / "sel.json"
    sel_path.write_text(json.dumps(sel))
    rc = main(["--trash-dir", str(tmp_path / "trash"), "--undo-dir", str(tmp_path / "u"),
               "apply", str(sel_path)])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err
    assert keep.exists()


def test_undo_lists_when_no_arg(tmp_path, capsys):
    rc = main(["--undo-dir", str(tmp_path / "empty"), "undo"])
    assert rc == 0
    assert "no undo manifests" in capsys.readouterr().out


def test_cache_clear(tmp_path):
    rc = main(["--db", str(tmp_path / "i.db"), "cache", "clear"])
    assert rc == 0


def test_scan_fails_fast_on_nonexistent_root(tmp_path, capsys):
    """A typo'd/missing root must abort before any scanning, naming the resolved path."""
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "does-not-exist"
    report = tmp_path / "report.html"

    rc = main(["scan", str(good), str(bad), "-o", str(report)])

    assert rc == 2
    err = capsys.readouterr().err
    assert str(bad.resolve()) in err
    assert not report.exists()
    assert not (tmp_path / "report-images.html").exists()


def test_scan_surfaces_hash_errors_in_terminal_and_report(tmp_path, capsys):
    """A file that discovers fine but fails to decode/hash must be counted on the
    terminal and detailed in the report notes — never silently dropped."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "broken.jpg").write_bytes(b"not really a jpeg")
    db = tmp_path / "index.db"
    undo_dir = tmp_path / "undo"
    report = tmp_path / "report.html"

    rc = main(["--db", str(db), "--undo-dir", str(undo_dir), "--scans-dir", str(tmp_path / "scans"),
               "scan", str(root), "-o", str(report), "--workers", "0"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 decode/hash errors" in out
    assert "see report notes" in out
    img_text = (tmp_path / "report-images.html").read_text()
    assert "broken.jpg" in img_text
    assert "Unreadable/undecodable during hashing" in img_text


def test_scan_lists_refused_libraries_in_full(tmp_path, capsys):
    """High-signal, usually-short lists (refused libraries) print in full on the
    terminal rather than being buried as a bare count."""
    root = tmp_path / "data"
    lib = root / "Photos Library.photoslibrary"
    lib.mkdir(parents=True)
    (lib / "inner.jpg").write_bytes(b"x")
    (root / "a.txt").write_bytes(b"hello")
    db = tmp_path / "index.db"
    undo_dir = tmp_path / "undo"
    report = tmp_path / "report.html"

    rc = main(["--db", str(db), "--undo-dir", str(undo_dir), "--scans-dir", str(tmp_path / "scans"),
               "scan", str(root), "-o", str(report), "--workers", "0"])

    assert rc == 0
    out = capsys.readouterr().out
    assert str(lib.resolve()) in out


def test_scan_prints_read_error_count_with_pointer(tmp_path, capsys):
    """Potentially-large lists (per-file read errors) print as a count + a
    pointer to the report, not flooding the terminal with every path."""
    root = tmp_path / "data"
    good = root / "ok"
    good.mkdir(parents=True)
    (good / "a.txt").write_bytes(b"hello")
    blocked = root / "blocked"
    blocked.mkdir()
    (blocked / "secret.txt").write_bytes(b"x")
    blocked.chmod(0o000)
    try:
        db = tmp_path / "index.db"
        undo_dir = tmp_path / "undo"
        report = tmp_path / "report.html"
        rc = main(["--db", str(db), "--undo-dir", str(undo_dir), "--scans-dir", str(tmp_path / "scans"),
                   "scan", str(root), "-o", str(report), "--workers", "0"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "1 read errors — see report notes for details" in out
    finally:
        blocked.chmod(0o755)


def test_scan_writes_history_entry(tmp_path):
    """Every scan archives its report + a record, so a reviewed-but-never-applied
    scan is recoverable after the next scan overwrites report.html."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.bin").write_bytes(b"dup" * 10)
    (root / "b.bin").write_bytes(b"dup" * 10)
    scans_dir = tmp_path / "scans"
    base = ["--db", str(tmp_path / "index.db"), "--undo-dir", str(tmp_path / "undo"),
            "--scans-dir", str(scans_dir)]

    rc = main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])

    assert rc == 0
    scan_dirs = list(scans_dir.iterdir())
    assert len(scan_dirs) == 1
    scan_dir = scan_dirs[0]
    assert (scan_dir / "meta.json").exists()
    assert (scan_dir / "report-images.html").exists()
    assert (scan_dir / "report-other.html").exists()


def test_zero_duplicate_scan_still_archived(tmp_path):
    """Locked decision: every scan is archived automatically, even one that
    finds nothing to review."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "unique.txt").write_bytes(b"only one file, no duplicates")
    scans_dir = tmp_path / "scans"
    base = ["--db", str(tmp_path / "index.db"), "--undo-dir", str(tmp_path / "undo"),
            "--scans-dir", str(scans_dir)]

    rc = main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])

    assert rc == 0
    scan_dirs = list(scans_dir.iterdir())
    assert len(scan_dirs) == 1
    assert (scan_dirs[0] / "meta.json").exists()


def test_scan_archival_failure_is_non_fatal(tmp_path, capsys):
    """A disk-full/permissions problem while archiving must never make an
    otherwise-successful scan look like it failed."""
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    # a FILE where a directory is expected — mkdir(parents=True) inside
    # record_scan will raise NotADirectoryError/FileExistsError
    scans_dir_blocker = tmp_path / "scans_is_a_file"
    scans_dir_blocker.write_bytes(b"not a directory")
    report = tmp_path / "report.html"
    base = ["--db", str(tmp_path / "index.db"), "--undo-dir", str(tmp_path / "undo"),
            "--scans-dir", str(scans_dir_blocker)]

    rc = main(base + ["scan", str(root), "-o", str(report), "--workers", "0"])

    assert rc == 0
    err = capsys.readouterr().err
    assert "warning: could not archive" in err
    assert (tmp_path / "report-images.html").exists()
    assert (tmp_path / "report-other.html").exists()


def _base_dirs(tmp_path):
    return ["--db", str(tmp_path / "index.db"), "--undo-dir", str(tmp_path / "undo"),
            "--scans-dir", str(tmp_path / "scans")]


def test_history_lists_scans(tmp_path, capsys):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    base = _base_dirs(tmp_path)

    rc = main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])
    assert rc == 0
    capsys.readouterr()

    rc = main(base + ["history"])

    assert rc == 0
    out = capsys.readouterr().out
    scan_dirs = list((tmp_path / "scans").iterdir())
    assert scan_dirs[0].name in out


def test_history_shows_one_entry_report_paths(tmp_path, capsys):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    base = _base_dirs(tmp_path)
    main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])
    capsys.readouterr()
    scan_id = next((tmp_path / "scans").iterdir()).name

    rc = main(base + ["history", scan_id])

    assert rc == 0
    out = capsys.readouterr().out
    assert str((tmp_path / "scans" / scan_id / "report-images.html").resolve()) in out
    assert str((tmp_path / "scans" / scan_id / "report-other.html").resolve()) in out


def test_history_applied_flag_true_only_after_apply(tmp_path, capsys):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.bin").write_bytes(b"dup" * 10)
    (root / "b.bin").write_bytes(b"dup" * 10)
    base = _base_dirs(tmp_path)
    main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])
    capsys.readouterr()
    scan_id = next((tmp_path / "scans").iterdir()).name

    main(base + ["history"])
    out_before = capsys.readouterr().out
    assert "not applied" in out_before

    other_text = (tmp_path / "report-other.html").read_text()
    parser = InputCollector()
    parser.feed(other_text)
    sel = simulate_js_export(parser.inputs)
    sel["scan_id"] = scan_id
    sel_path = tmp_path / "selection-other.json"
    sel_path.write_text(json.dumps(sel))
    monkeypatch_input_trash = ["--trash-dir", str(tmp_path / "trash")]
    import builtins
    orig_input = builtins.input
    builtins.input = lambda *_: "trash"
    try:
        rc = main(base + monkeypatch_input_trash + ["apply", str(sel_path)])
    finally:
        builtins.input = orig_input
    assert rc == 0
    capsys.readouterr()

    main(base + ["history"])
    out_after = capsys.readouterr().out
    assert "applied" in out_after and "not applied" not in out_after


def test_stats_text_reflects_undo_manifests(tmp_path, capsys):
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.bin").write_bytes(b"dup" * 10)
    (root / "b.bin").write_bytes(b"dup" * 10)
    base = _base_dirs(tmp_path)
    main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])
    capsys.readouterr()
    scan_id = next((tmp_path / "scans").iterdir()).name

    other_text = (tmp_path / "report-other.html").read_text()
    parser = InputCollector()
    parser.feed(other_text)
    sel = simulate_js_export(parser.inputs)
    sel["scan_id"] = scan_id
    sel_path = tmp_path / "selection-other.json"
    sel_path.write_text(json.dumps(sel))
    import builtins
    orig_input = builtins.input
    builtins.input = lambda *_: "trash"
    try:
        rc = main(base + ["--trash-dir", str(tmp_path / "trash"), "apply", str(sel_path)])
    finally:
        builtins.input = orig_input
    assert rc == 0
    capsys.readouterr()

    rc = main(base + ["stats"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 applies" in out
    assert "30 B moved to Trash" in out  # two 30-byte dup files, one trashed


def test_stats_html_writes_dashboard_default_and_explicit_path(tmp_path, capsys, monkeypatch):
    import shutil

    root = tmp_path / "data"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    base = _base_dirs(tmp_path)
    main(base + ["scan", str(root), "-o", str(tmp_path / "report.html"), "--workers", "0"])
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)

    # A single scan renders a "not enough data for a trend yet" placeholder,
    # not a chart (deliberate — see dashboard.py). Duplicate the one real
    # ledger entry under a second scan_id to get 2 points without depending
    # on real wall-clock time passing between two live `scan` calls (which
    # could land in the same second and collide on one scan_id).
    scans_dir = tmp_path / "scans"
    first_dir = next(scans_dir.iterdir())
    second_id = first_dir.name + "-2"
    shutil.copytree(first_dir, scans_dir / second_id)
    meta_path = scans_dir / second_id / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["scan_id"] = second_id
    meta_path.write_text(json.dumps(meta))

    rc = main(base + ["stats", "--html"])
    assert rc == 0
    default_path = tmp_path / "findupe-dashboard.html"
    assert default_path.exists()
    assert "<svg" in default_path.read_text()  # 2 scans -> the found-duplicates series has 2 points

    explicit_path = tmp_path / "custom-dash.html"
    rc = main(base + ["stats", "--html", str(explicit_path)])
    assert rc == 0
    assert explicit_path.exists()


def test_stats_and_history_empty_state(tmp_path, capsys):
    base = _base_dirs(tmp_path)

    rc = main(base + ["stats"])
    assert rc == 0
    capsys.readouterr()

    rc = main(base + ["history"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no archived scans" in out
