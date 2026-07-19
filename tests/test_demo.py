"""`findupe --demo`: copies the bundled examples/ dataset to a scratch dir,
scans it, and opens the report — a zero-setup path exercised end to end
against the real bundled dataset, with --db/--scans-dir/--undo-dir always
overridden to tmp_path per the standing e2e-pollution rule."""

import findupe.cli as cli_module
from findupe.cli import main


def _base(tmp_path, demo_dir):
    return [
        "--db", str(tmp_path / "index.db"),
        "--scans-dir", str(tmp_path / "scans"),
        "--undo-dir", str(tmp_path / "undo"),
        "--demo-dir", str(demo_dir),
        "--demo",
    ]


def test_demo_scans_bundled_examples_and_opens_report(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli_module, "_open_in_finder", lambda p: opened.append(p))
    demo_dir = tmp_path / "demo"

    rc = main(_base(tmp_path, demo_dir))

    assert rc == 0
    assert (demo_dir / "report-images.html").exists()
    assert (demo_dir / "report-other.html").exists()
    assert opened == [demo_dir / "report-images.html"]
    out = capsys.readouterr().out
    assert "duplicate families" in out
    assert list((tmp_path / "scans").iterdir())  # scan history landed in the override, not ~/.findupe


def test_demo_copies_dataset_rather_than_scanning_repo_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, "_open_in_finder", lambda p: None)
    demo_dir = tmp_path / "demo"

    main(_base(tmp_path, demo_dir))

    copied = demo_dir / "examples"
    assert (copied / "inbox" / "sunset.jpg").exists()
    assert (copied / "backup" / "mountain.jpg").exists()


def test_demo_can_run_twice_into_the_same_dir(tmp_path, monkeypatch):
    """dirs_exist_ok copytree: re-running --demo (e.g. after deleting the
    report to look again) must not crash on the second pass."""
    monkeypatch.setattr(cli_module, "_open_in_finder", lambda p: None)
    demo_dir = tmp_path / "demo"
    args = _base(tmp_path, demo_dir)

    assert main(args) == 0
    assert main(args) == 0


def test_demo_flag_bypasses_required_subcommand(tmp_path, monkeypatch):
    """--demo must work with no subcommand at all (subparsers is required=False
    precisely so --demo alone is valid)."""
    monkeypatch.setattr(cli_module, "_open_in_finder", lambda p: None)
    demo_dir = tmp_path / "demo"

    rc = main(_base(tmp_path, demo_dir))

    assert rc == 0


def test_demo_ignores_populated_config(tmp_path, monkeypatch):
    """The --demo command must completely ignore the configuration file,
    even if one is populated with invalid data or different scan roots."""
    monkeypatch.setattr(cli_module, "_open_in_finder", lambda p: None)
    demo_dir = tmp_path / "demo"
    
    # Create an invalid config file that would normally cause ConfigError on merge
    conf_file = tmp_path / "config.toml"
    conf_file.write_text("invalid_key = 42\n")
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    # Run demo - it should not raise ConfigError or crash
    rc = main(_base(tmp_path, demo_dir))
    assert rc == 0
