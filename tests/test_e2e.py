"""End-to-end: synthetic tree -> scan -> report -> selection -> apply -> undo."""

import json
import os
from pathlib import Path

import io

from PIL import Image

from dupefinder.cli import main
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
    report = tmp_path / "report.html"
    base = ["--db", str(db), "--undo-dir", str(undo_dir), "--trash-dir", str(trash_dir)]

    # -- scan
    rc = main(base + ["scan", str(root), "-o", str(report), "--workers", "0"])
    assert rc == 0
    text = report.read_text()
    assert "shot.heic" in text and "shot.jpg" in text

    parser = InputCollector()
    parser.feed(text)
    keepers = [i for i in parser.inputs if i.get("class") == "keeper"]
    cands = [i for i in parser.inputs if i.get("class") == "cand"]

    cand_paths = {c["data-path"] for c in cands}
    # cross-format siblings and hardlinks are never candidates
    assert not any(p.endswith(("shot.heic", "shot.jpg")) for p in cand_paths)
    assert not any("linked" in p for p in cand_paths)
    # the copy-named files lost the keeper contest
    assert any(p.endswith("photo copy.jpg") for p in cand_paths)
    assert any(p.endswith("café 📷 copy.png") for p in cand_paths)
    assert all("checked" in c for c in cands)  # unflagged families are pre-checked

    # -- export selection exactly as the report JS would
    sel = simulate_js_export(parser.inputs)
    sel["scan_id"] = "e2e"
    sel_path = tmp_path / "selection.json"
    sel_path.write_text(json.dumps(sel))

    # -- dry-run moves nothing
    rc = main(base + ["apply", str(sel_path), "--dry-run"])
    assert rc == 0
    assert (root / "photo copy.jpg").exists()

    # -- real apply with typed confirmation
    monkeypatch.setattr("builtins.input", lambda *_: "trash")
    rc = main(base + ["apply", str(sel_path)])
    assert rc == 0
    assert not (root / "photo copy.jpg").exists()
    assert not (root / "café 📷 copy.png").exists()
    assert (root / "photo.jpg").exists()
    assert (root / "shot.heic").exists() and (root / "shot.jpg").exists()
    assert (root / "linked.bin").exists() and (root / "linked2.bin").exists()
    # exactly one of the two PDFs survived
    assert (root / "report.pdf").exists() ^ (root / "backup" / "report.pdf").exists()

    # -- undo restores everything
    manifests = list((undo_dir).glob("*.json"))
    assert len(manifests) == 1
    rc = main(base + ["undo", manifests[0].name])
    assert rc == 0
    assert (root / "photo copy.jpg").exists()
    assert (root / "café 📷 copy.png").exists()
    assert (root / "report.pdf").exists() and (root / "backup" / "report.pdf").exists()

    # -- second scan is cache-warm and still succeeds
    rc = main(base + ["scan", str(root), "-o", str(report), "--workers", "0"])
    assert rc == 0


def test_apply_refuses_tampered_selection(tmp_path, capsys):
    keep = tmp_path / "k.bin"
    keep.write_bytes(b"x" * 10)
    from dupefinder.hashing import full_hash
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
