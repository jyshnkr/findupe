from pathlib import Path

import pytest

from findupe.cli import NO_ARGS_MESSAGE, _collect_hash_errors, main
from test_grouping import mk


def test_collect_hash_errors_dedups_shared_companion():
    """A sidecar shared by two primaries in the same family (e.g. one XMP
    attached to both a RAW and its JPEG sibling — discover._attach_companions
    appends the SAME record to every primary's .companions) must be
    counted/reported once, not once per primary that references it."""
    primary_a = mk("/p/IMG_1.CR3")
    primary_b = mk("/p/IMG_1.jpg")
    sidecar = mk("/p/IMG_1.xmp")
    sidecar.hash_error = "full hash: [Errno 2] No such file or directory"
    companions = [sidecar, sidecar]  # mirrors the real duplication

    result = _collect_hash_errors([primary_a, primary_b], companions)

    assert result == [(Path("/p/IMG_1.xmp"), sidecar.hash_error)]


def test_no_args_prints_guided_message_not_argparse_error(capsys):
    """A bare `findupe` invocation used to hard-error via argparse's
    required=True subparser; it must instead print a friendly 3-step
    orientation and exit cleanly, without touching any state."""
    rc = main([])

    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == NO_ARGS_MESSAGE
    assert "--demo" in out
    assert "findupe scan" in out


def test_help_epilog_shows_worked_example_and_demo_pointer(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "example:" in out
    assert "--demo" in out
    assert "github.com/jyshnkr/findupe" in out


def test_scan_accepts_no_ocr_flag(tmp_path):
    """The --no-ocr flag must be accepted by the scan subparser and
    does not crash when cmd_scan is called with it."""
    # Create a minimal directory to scan
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "dummy.txt").write_text("not an image")

    # Test that --no-ocr flag is accepted
    # Global args must come before the subcommand
    rc = main([
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "scan", str(scan_dir),
        "--output", str(tmp_path / "report.html"),
        "--no-ocr",
    ])

    # Should complete successfully (0 or 1 depending on findings)
    assert rc in (0, 1)
