import json
from html.parser import HTMLParser
from pathlib import Path

from dupefinder.grouping import build_families
from dupefinder.models import ScanResult
from dupefinder.report import generate_report
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
    generate_report(scan, possible, out, thumb=lambda p: None)
    text = out.read_text()
    parser = InputCollector()
    parser.feed(text)
    return text, parser.inputs


def simulate_js_export(inputs: list[dict]) -> dict:
    """Reimplements the report's exportSelection() logic for round-trip testing."""
    delete, keep = [], {}
    cands = [i for i in inputs if i.get("class") == "cand" and "checked" in i]
    keepers = [i for i in inputs if i.get("class") == "keeper"]
    for cb in cands:
        delete.append({
            "path": cb["data-path"], "size": int(cb["data-size"]),
            "blake2b": cb["data-hash"], "family": cb["data-family"], "format": cb["data-format"],
        })
        for k in keepers:
            if k["data-family"] == cb["data-family"] and k["data-format"] == cb["data-format"]:
                keep[k["data-path"]] = {
                    "path": k["data-path"], "size": int(k["data-size"]),
                    "blake2b": k["data-hash"], "family": k["data-family"], "format": k["data-format"],
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
    assert '"testscan"' in text  # scan id reaches the JS


def test_flagged_family_not_prechecked(tmp_path):
    recs = [mk(f"/p/burst{i}.jpg", phash=PH, dhash=PH, mtime=i) for i in range(5)]
    families, possible = build_families(recs, {})
    assert families[0].flags  # possible-burst
    _, inputs = render(families, possible, tmp_path)
    cands = [i for i in inputs if i.get("class") == "cand"]
    assert cands and all("checked" not in c for c in cands)


def test_possible_section_has_no_checkboxes(tmp_path):
    a = mk("/p/one.jpg", phash=PH, dhash=PH)
    b = mk("/p/two.jpg", phash=PH ^ 0b11110, dhash=PH)  # possible tier
    families, possible = build_families([a, b], {})
    assert not families and possible
    text, inputs = render(families, possible, tmp_path)
    assert [i for i in inputs if i["_section"] == "possible-sec"] == []
    assert "review only" in text


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
    json.dumps(sel)  # serializable


def test_unicode_paths_render(tmp_path):
    a = mk("/p/café 📷.jpg", exact_hash="dd")
    b = mk("/p/café 📷 copy.jpg", exact_hash="dd")
    families, possible = build_families([a, b], {"dd": [a, b]})
    text, inputs = render(families, possible, tmp_path)
    assert "café 📷" in text
    cand = next(i for i in inputs if i.get("class") == "cand")
    assert cand["data-path"] == "/p/café 📷 copy.jpg"
