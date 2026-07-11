import json
from html.parser import HTMLParser
from pathlib import Path

from dupefinder.grouping import build_families
from dupefinder.models import ScanResult
from dupefinder.report import _write_report, category_output_paths, generate_reports
from test_grouping import PH, mk


class InputCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs: list[dict] = []
        self.section_stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section":
            self.section_stack.append(a.get("id", ""))
        if tag == "input":
            a["_section"] = self.section_stack[-1] if self.section_stack else ""
            self.inputs.append(a)

    def handle_endtag(self, tag):
        if tag == "section" and self.section_stack:
            self.section_stack.pop()


def render(families, possible, tmp_path, **scan_kw) -> tuple[str, list[dict]]:
    scan = ScanResult(scan_id="testscan", roots=[Path("/p")], families=families, **scan_kw)
    out = tmp_path / "report.html"
    _write_report(scan, families, possible, out, "all", thumb=lambda p: None)
    text = out.read_text()
    parser = InputCollector()
    parser.feed(text)
    return text, parser.inputs


def simulate_js_export(inputs: list[dict]) -> dict:
    """Reimplements the report's exportSelection() logic for round-trip testing."""
    def ckey(i):
        return (i["data-family"], i["data-format"], i["data-cluster"])

    keeper_map = {ckey(i): i for i in inputs if i.get("class") == "keeper"}
    delete, keep = [], {}
    for cb in [i for i in inputs if i.get("class") == "cand" and "checked" in i]:
        delete.append({
            "path": cb["data-path"], "size": int(cb["data-size"]),
            "blake2b": cb["data-hash"], "family": cb["data-family"],
            "format": cb["data-format"], "cluster": cb["data-cluster"],
            "companions": json.loads(cb.get("data-companions", "[]")),
        })
        k = keeper_map.get(ckey(cb))
        if k:
            keep[k["data-path"]] = {
                "path": k["data-path"], "size": int(k["data-size"]),
                "blake2b": k["data-hash"], "family": k["data-family"],
                "format": k["data-format"], "cluster": k["data-cluster"],
            }
    return {"schema_version": "1", "scan_id": "testscan",
            "delete": delete, "keep": list(keep.values())}


def make_exact_family():
    a = mk("/p/doc.pdf", exact_hash="aa11")
    b = mk("/p/backup/doc.pdf", exact_hash="aa11")
    families, possible = build_families([a, b], {"aa11": [a, b]})
    return families, possible


def test_keeper_disabled_surplus_prechecked(tmp_path):
    families, possible = make_exact_family()
    text, inputs = render(families, possible, tmp_path)
    keeper = [i for i in inputs if i.get("class") == "keeper"]
    cands = [i for i in inputs if i.get("class") == "cand"]
    assert len(keeper) == 1 and "disabled" in keeper[0]
    assert len(cands) == 1 and "checked" in cands[0]
    assert keeper[0]["data-hash"] == "aa11"
    assert keeper[0]["data-cluster"] == cands[0]["data-cluster"]
    assert '"testscan"' in text  # scan id reaches the JS


def test_flagged_family_not_prechecked_and_marked(tmp_path):
    recs = [mk(f"/p/burst{i}.jpg", phash=PH, dhash=PH, mtime=i) for i in range(5)]
    families, possible = build_families(recs, {})
    assert families[0].flags  # possible-burst
    text, inputs = render(families, possible, tmp_path)
    cands = [i for i in inputs if i.get("class") == "cand"]
    assert cands and all("checked" not in c for c in cands)
    # data-flagged keeps "Check all suggested" away from these
    assert all(c.get("data-flagged") == "1" for c in cands)
    assert ":not([data-flagged])" in text


def test_possible_section_has_no_checkboxes(tmp_path):
    a = mk("/p/one.jpg", phash=PH, dhash=PH)
    b = mk("/p/two.jpg", phash=PH ^ 0b11110, dhash=PH)  # possible tier
    families, possible = build_families([a, b], {})
    assert not families and possible
    text, inputs = render(families, possible, tmp_path)
    assert [i for i in inputs if i["_section"] == "possible-sec"] == []
    assert "review only" in text


def test_cross_format_sibling_renders_as_info_row(tmp_path):
    raw = mk("/p/IMG_1.CR3", phash=PH, dhash=PH, capture_key="t|1", mtime=7)
    jpg1 = mk("/p/IMG_1.jpg", phash=PH, dhash=PH, capture_key="t|1", capture_subsec="5")
    jpg2 = mk("/p/IMG_1 copy.jpg", phash=PH, dhash=PH, capture_key="t|1", capture_subsec="5")
    families, possible = build_families([raw, jpg1, jpg2], {})
    text, inputs = render(families, possible, tmp_path)
    # the CR3 is family context but must carry NO checkbox
    assert not any(i["data-path"].endswith(".CR3") for i in inputs)
    assert "sibling" in text


def test_cloud_badge_and_notes(tmp_path):
    a = mk("/p/x.bin", exact_hash="cc")
    b = mk("/p/y.bin", exact_hash="cc")
    b.cloud_synced = True
    families, possible = build_families([a, b], {"cc": [a, b]})
    text, _ = render(
        families, possible, tmp_path,
        skipped_stubs=[Path("/p/stub.jpg")],
        hardlink_notes=[(Path("/p/h1"), Path("/p/h2"))],
        errors=[(Path("/p/bad"), "Permission denied")],
    )
    assert "☁ synced" in text
    assert "stub.jpg" in text and "not local" in text
    assert "Hardlinks" in text
    assert "Permission denied" in text


def test_selection_round_trip(tmp_path):
    families, possible = make_exact_family()
    _, inputs = render(families, possible, tmp_path)
    sel = simulate_js_export(inputs)
    assert len(sel["delete"]) == 1 and len(sel["keep"]) == 1
    assert sel["delete"][0]["blake2b"] == "aa11"
    assert sel["keep"][0]["path"] != sel["delete"][0]["path"]
    assert sel["delete"][0]["cluster"] == sel["keep"][0]["cluster"]
    json.dumps(sel)  # serializable


def test_companions_exported_with_size_and_hash(tmp_path):
    prim_keep = mk("/p/X.jpg", phash=PH, dhash=PH, mtime=1)
    prim_del = mk("/p/X copy.jpg", phash=PH, dhash=PH, mtime=2)
    sidecar = mk("/p/X copy.xmp", size=88)
    sidecar.exact_hash = "sidehash"
    prim_del.companions.append(sidecar)
    families, possible = build_families([prim_keep, prim_del], {})
    _, inputs = render(families, possible, tmp_path)
    sel = simulate_js_export(inputs)
    (comp,) = sel["delete"][0]["companions"]
    assert comp == {"path": "/p/X copy.xmp", "size": 88, "blake2b": "sidehash"}


def test_unicode_paths_render(tmp_path):
    a = mk("/p/café 📷.jpg", exact_hash="dd")
    b = mk("/p/café 📷 copy.jpg", exact_hash="dd")
    families, possible = build_families([a, b], {"dd": [a, b]})
    text, inputs = render(families, possible, tmp_path)
    assert "café 📷" in text
    cand = next(i for i in inputs if i.get("class") == "cand")
    assert cand["data-path"] == "/p/café 📷 copy.jpg"


def test_category_output_paths_derivation():
    assert category_output_paths(Path("report.html")) == (
        Path("report-images.html"), Path("report-other.html"),
    )
    assert category_output_paths(Path("/tmp/out/report.html")) == (
        Path("/tmp/out/report-images.html"), Path("/tmp/out/report-other.html"),
    )
    assert category_output_paths(Path("scan")) == (Path("scan-images"), Path("scan-other"))


def test_generate_reports_splits_by_category(tmp_path):
    pdf_a = mk("/p/doc.pdf", exact_hash="aa11")
    pdf_b = mk("/p/backup/doc.pdf", exact_hash="aa11")
    img_a = mk("/p/photo.jpg", exact_hash="bb22")
    img_b = mk("/p/photo copy.jpg", exact_hash="bb22")
    families, possible = build_families(
        [pdf_a, pdf_b, img_a, img_b],
        {"aa11": [pdf_a, pdf_b], "bb22": [img_a, img_b]},
    )
    scan = ScanResult(scan_id="testscan", roots=[Path("/p")], families=families)
    base = tmp_path / "report.html"
    img_path, other_path = generate_reports(scan, possible, base, thumb=lambda p: None)

    assert img_path == tmp_path / "report-images.html"
    assert other_path == tmp_path / "report-other.html"

    img_text = img_path.read_text()
    other_text = other_path.read_text()
    assert "photo copy.jpg" in img_text and "doc.pdf" not in img_text
    assert "doc.pdf" in other_text and "photo copy.jpg" not in other_text


def test_other_report_has_no_visual_or_possible_sections(tmp_path):
    pdf_a = mk("/p/doc.pdf", exact_hash="aa11")
    pdf_b = mk("/p/backup/doc.pdf", exact_hash="aa11")
    families, possible = build_families([pdf_a, pdf_b], {"aa11": [pdf_a, pdf_b]})
    scan = ScanResult(scan_id="testscan", roots=[Path("/p")], families=families)
    base = tmp_path / "report.html"
    _, other_path = generate_reports(scan, possible, base, thumb=lambda p: None)
    text = other_path.read_text()
    assert 'id="visual-sec"' not in text
    assert 'id="possible-sec"' not in text
    assert 'id="exact-sec"' in text
    assert "dupefinder-selection-testscan-other.json" in text
    assert '"other"' in text  # CATEGORY const reaches the JS


def test_other_report_empty_state(tmp_path):
    img_a = mk("/p/photo.jpg", exact_hash="bb22")
    img_b = mk("/p/photo copy.jpg", exact_hash="bb22")
    families, possible = build_families([img_a, img_b], {"bb22": [img_a, img_b]})
    scan = ScanResult(scan_id="testscan", roots=[Path("/p")], families=families)
    base = tmp_path / "report.html"
    _, other_path = generate_reports(scan, possible, base, thumb=lambda p: None)
    assert "No duplicate other found in this scan." in other_path.read_text()


def test_non_image_row_shows_format_badge(tmp_path):
    pdf_a = mk("/p/doc.pdf", exact_hash="aa11")
    pdf_b = mk("/p/backup/doc.pdf", exact_hash="aa11")
    families, possible = build_families([pdf_a, pdf_b], {"aa11": [pdf_a, pdf_b]})
    text, _ = render(families, possible, tmp_path)
    assert 'class="noimg fileicon"' in text and "PDF" in text
