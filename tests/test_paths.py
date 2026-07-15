"""resolve_data_home(): the one-time ~/.dupefinder -> ~/.findupe migration.

Always called with explicit legacy/new paths here — never against the real
home directory — so running this suite can never touch a developer's actual
~/.dupefinder or ~/.findupe.
"""

from findupe.paths import resolve_data_home


def test_migrates_legacy_dir_preserving_contents(tmp_path):
    legacy = tmp_path / ".dupefinder"
    new = tmp_path / ".findupe"
    (legacy / "scans" / "20260101-000000").mkdir(parents=True)
    (legacy / "undo").mkdir()
    (legacy / "index.db").write_bytes(b"cache")
    (legacy / "undo" / "manifest.json").write_text('{"schema_version": "1"}')

    result = resolve_data_home(legacy=legacy, new=new)

    assert result == new
    assert not legacy.exists()
    assert (new / "index.db").read_bytes() == b"cache"
    assert (new / "scans" / "20260101-000000").is_dir()
    assert (new / "undo" / "manifest.json").read_text() == '{"schema_version": "1"}'


def test_no_legacy_dir_returns_new_without_creating_it(tmp_path):
    legacy = tmp_path / ".dupefinder"
    new = tmp_path / ".findupe"

    result = resolve_data_home(legacy=legacy, new=new)

    assert result == new
    assert not new.exists()
    assert not legacy.exists()


def test_new_dir_already_present_wins_and_legacy_is_untouched(tmp_path):
    """Already migrated, or the user created ~/.findupe/ independently — either
    way ~/.findupe/ must never be clobbered, and ~/.dupefinder/ is left alone."""
    legacy = tmp_path / ".dupefinder"
    new = tmp_path / ".findupe"
    (legacy).mkdir()
    (legacy / "index.db").write_bytes(b"old-cache")
    (new).mkdir()
    (new / "index.db").write_bytes(b"current-cache")

    result = resolve_data_home(legacy=legacy, new=new)

    assert result == new
    assert (new / "index.db").read_bytes() == b"current-cache"
    assert legacy.exists()
    assert (legacy / "index.db").read_bytes() == b"old-cache"


def test_concurrent_first_use_does_not_crash(tmp_path, monkeypatch):
    """Two processes racing resolve_data_home() on first use: the guard check
    (`not new.exists() and legacy.exists()`) can pass for both before either
    calls os.rename. Whichever calls os.rename second gets FileNotFoundError
    — legacy is already gone, moved by the winner — and that must not
    propagate as a crash: `new` exists by then and is exactly what the loser
    wanted anyway.

    Single-threaded simulation of the loser's call: by the time THIS call's
    os.rename fires, the race is already decided elsewhere, so the mock
    performs the winner's move first (standing in for the other process)
    and then raises exactly what os.rename raises for a vanished source —
    the same observable failure a real second-place os.rename() would
    produce. (Verified this test actually discriminates: temporarily
    reverting the try/except in paths.py makes it fail with an unhandled
    FileNotFoundError, as expected.)
    """
    import os

    legacy = tmp_path / ".dupefinder"
    new = tmp_path / ".findupe"
    legacy.mkdir()
    (legacy / "index.db").write_bytes(b"cache")

    real_os_rename = os.rename

    def losers_rename_call(src, dst):
        real_os_rename(src, dst)  # the winning process's migration, completing first
        raise FileNotFoundError(2, "No such file or directory")  # this call's own view: gone

    monkeypatch.setattr(os, "rename", losers_rename_call)

    result = resolve_data_home(legacy=legacy, new=new)

    assert result == new
    assert (new / "index.db").read_bytes() == b"cache"


def test_second_call_is_a_no_op(tmp_path):
    legacy = tmp_path / ".dupefinder"
    new = tmp_path / ".findupe"
    legacy.mkdir()
    (legacy / "index.db").write_bytes(b"cache")

    first = resolve_data_home(legacy=legacy, new=new)
    (new / "index.db").write_bytes(b"cache-modified-after-migration")
    second = resolve_data_home(legacy=legacy, new=new)

    assert first == second == new
    assert (new / "index.db").read_bytes() == b"cache-modified-after-migration"
