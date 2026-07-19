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


def test_config_subcommands(tmp_path, monkeypatch, capsys):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    # 1. Bare config when file doesn't exist
    rc = main(["config"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "does not exist" in out
    
    # 2. Config init
    rc = main(["config", "init"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "initialized config at" in out
    assert conf_file.exists()
    
    # 3. Config init --force override
    rc = main(["config", "init"])
    assert rc == 2
    out = capsys.readouterr().err
    assert "already exists" in out
    
    rc = main(["config", "init", "--force"])
    assert rc == 0
    
    # 4. Config set and get
    rc = main(["config", "set", "threshold", "6"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "set threshold = 6" in out
    
    # set bad type
    rc = main(["config", "set", "threshold", "not-an-int"])
    assert rc == 2
    
    rc = main(["config", "set", "no_ocr", "true"])
    assert rc == 0
    capsys.readouterr()
    
    rc = main(["config", "get", "threshold"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == "6"
    
    # 5. Config add-root and add-exclude
    rc = main(["config", "add-root", "/tmp/photos"])
    assert rc == 0
    
    rc = main(["config", "add-exclude", "*.tmp"])
    assert rc == 0
    capsys.readouterr()
    
    # Verify get on lists
    rc = main(["config", "get", "roots"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == "/tmp/photos"
    
    # Bare config lists settings
    rc = main(["config"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "threshold = 6" in out
    assert "/tmp/photos" in out


def test_empty_roots_friendly_error(tmp_path, monkeypatch, capsys):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    # Run scan with no CLI paths and no config roots
    rc = main([
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "scan",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "no scan roots specified" in err
    assert "findupe config init" in err


def test_scan_driven_by_config(tmp_path, monkeypatch, capsys):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    # Set config roots and threshold
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "dummy.txt").write_text("not an image")
    
    main(["config", "init"])
    main(["config", "add-root", str(scan_dir)])
    main(["config", "set", "threshold", "5"])
    main(["config", "add-exclude", "*.log"])
    capsys.readouterr()
    
    # Run bare scan, should be driven by config roots, exclude, threshold
    # Also assert safety echo is present
    rc = main([
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "scan",
        "--output", str(tmp_path / "report.html"),
    ])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "findupe: config" in out
    assert "scanning 1 root" in out
    assert "*.log" in out
    assert "threshold: 5" in out


def test_cli_paths_override_config_roots(tmp_path, monkeypatch, capsys):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    scan_dir_1 = tmp_path / "scan1"
    scan_dir_1.mkdir()
    scan_dir_2 = tmp_path / "scan2"
    scan_dir_2.mkdir()
    (scan_dir_2 / "dummy.txt").write_text("not an image")
    
    main(["config", "init"])
    main(["config", "add-root", str(scan_dir_1)])
    capsys.readouterr()
    
    # Run scan pointing to scan_dir_2. CLI path should override config root.
    rc = main([
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "scan",
        str(scan_dir_2),
        "--output", str(tmp_path / "report.html"),
    ])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "findupe: config" in out
    assert "scanning 1 root" in out
    assert str(scan_dir_2) in out
    assert str(scan_dir_1) not in out


def test_cli_excludes_stack_on_config_excludes(tmp_path, monkeypatch, capsys):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "dummy.txt").write_text("not an image")
    
    main(["config", "init"])
    main(["config", "add-root", str(scan_dir)])
    main(["config", "add-exclude", "*.log"])
    capsys.readouterr()
    
    rc = main([
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "scan",
        "--exclude", "*.tmp",
        "--output", str(tmp_path / "report.html"),
    ])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    # Check that both excludes are active in the safety echo
    assert "*.log" in out
    assert "*.tmp" in out


def test_malformed_config_reports_loud_error(tmp_path, monkeypatch, capsys):
    """A malformed config must not crash the process or corrupt a scan — it
    must fail loudly, naming the offending key, before any work starts."""
    conf_file = tmp_path / "config.toml"
    conf_file.write_text("not_a_real_key = 42\n")
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))

    rc = main([
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "scan",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not_a_real_key" in err
