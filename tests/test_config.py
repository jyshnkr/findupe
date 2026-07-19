import os
import argparse
from pathlib import Path
import pytest

from findupe.config import (
    ConfigError,
    config_read_path,
    config_write_path,
    load_raw_config,
    load_config,
    render_template,
    write_values,
    merge_into_args,
    KNOWN_KEYS,
)
from findupe import paths


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("FINDUPE_CONFIG", raising=False)


def test_config_paths(tmp_path, clean_env, monkeypatch):
    # Default paths (no env var)
    # mock home to tmp_path
    legacy_home = tmp_path / ".dupefinder"
    new_home = tmp_path / ".findupe"
    monkeypatch.setattr(paths, "LEGACY_DATA_HOME", legacy_home)
    monkeypatch.setattr(paths, "DATA_HOME", new_home)
    
    # 1. Neither exists, config_read_path is None
    assert config_read_path() is None
    assert config_write_path() == new_home / "config.toml"
    
    # 2. Legacy exists, config_read_path returns legacy config
    legacy_home.mkdir(parents=True, exist_ok=True)
    legacy_conf = legacy_home / "config.toml"
    legacy_conf.touch()
    assert config_read_path() == legacy_conf
    
    # 3. New exists, it overrides legacy
    new_home.mkdir(parents=True, exist_ok=True)
    new_conf = new_home / "config.toml"
    new_conf.touch()
    assert config_read_path() == new_conf
    
    # 4. Env var overrides all, even if it doesn't exist for write, or exists for read
    env_conf = tmp_path / "env_config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(env_conf))
    assert config_write_path() == env_conf
    assert config_read_path() is None  # doesn't exist yet
    
    env_conf.touch()
    assert config_read_path() == env_conf


def test_load_and_validate(tmp_path, monkeypatch):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    # Empty/missing file -> empty dict
    assert load_config() == {}
    
    # Valid config
    conf_file.write_text(
        "threshold = 6\n"
        "roots = ['/tmp/a']\n"
        "no_ocr = true\n"
    )
    res = load_config()
    assert res["threshold"] == 6
    assert res["roots"] == ["/tmp/a"]
    assert res["no_ocr"] is True
    
    # Invalid key
    conf_file.write_text("invalid_key = 42\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "Unknown configuration key" in str(exc.value)
    
    # Invalid type (scalar instead of list)
    conf_file.write_text("roots = 'not-a-list'\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "must be a list" in str(exc.value)
    
    # Invalid element type in list
    conf_file.write_text("roots = [123]\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "must be str" in str(exc.value)

    # Invalid scalar type
    conf_file.write_text("threshold = 'not-an-int'\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "must be int" in str(exc.value)

    # bool is a subclass of int in Python — must not silently pass as a threshold
    conf_file.write_text("threshold = true\n")
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "must be int" in str(exc.value)


def test_path_expansion(tmp_path, monkeypatch):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    monkeypatch.setenv("MY_TEST_VAR", "foo")
    
    # test ~ and env var expansion
    conf_file.write_text(
        "roots = ['~/Pictures/$MY_TEST_VAR']\n"
        "db = '~/hashing_$MY_TEST_VAR.db'\n"
    )
    res = load_config()
    expected_home = str(Path.home())
    assert res["roots"] == [f"{expected_home}/Pictures/foo"]
    assert res["db"] == f"{expected_home}/hashing_foo.db"
    
    # load_raw_config does not expand
    raw = load_raw_config()
    assert raw["roots"] == ["~/Pictures/$MY_TEST_VAR"]
    assert raw["db"] == "~/hashing_$MY_TEST_VAR.db"


def test_template_rendering_and_write(tmp_path, monkeypatch):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))
    
    # 1. Render empty template
    tpl = render_template({})
    assert "threshold = 8" in tpl
    assert "# roots =" in tpl
    
    # 2. Write values and check round-trip
    write_values({"threshold": 5, "roots": ["/a"]})
    assert conf_file.exists()
    
    # Check it can be loaded
    loaded = load_config()
    assert loaded["threshold"] == 5
    assert loaded["roots"] == ["/a"]
    
    # File should have commented versions of unset keys
    content = conf_file.read_text()
    assert "# no_ocr = false" in content
    assert "threshold = 5" in content
    assert "roots = [\"/a\"]" in content or "roots = ['/a']" in content


def test_template_round_trips_non_ascii_paths(tmp_path, monkeypatch):
    conf_file = tmp_path / "config.toml"
    monkeypatch.setenv("FINDUPE_CONFIG", str(conf_file))

    write_values({"roots": ["~/Pictures/\U0001F4F7 Trip"]})
    # must not require surrogate-pair \uXXXX escapes tomllib rejects
    loaded = load_config()
    assert loaded["roots"] == [str(Path.home() / "Pictures/\U0001F4F7 Trip")]


def test_merge_into_args():
    # Precedence: CLI flag > config > built-in default
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--threshold", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--no-materialize", action="store_true")
    parser.add_argument("--db", type=Path, default=None)
    
    # Case 1: All empty config, fallback to argparse defaults (and our built-in rules)
    args = parser.parse_args([])
    # args after parse: paths=[], exclude=[], threshold=None, output=None, no_ocr=False, ocr=False, materialize=False, no_materialize=False, db=None
    # Let's mock a simple custom merge helper that behaves like cli.py does with tri-state etc.
    # We will pass this to merge_into_args
    # Wait, the CLI is responsible for mutually exclusive group config.
    # In merge_into_args(args, config):
    # - roots: fill args.paths when empty
    # - exclude: config + CLI
    # - threshold: CLI > config > default (8)
    # - output: CLI > config > default ("report.html")
    # - no_ocr: CLI (no-ocr/ocr) > config > False
    # - materialize: CLI (materialize/no-materialize) > config > False
    # - db: CLI > config
    config = {}
    merge_into_args(args, config)
    assert args.paths == []
    assert args.exclude == []
    assert args.threshold == 8
    assert args.output == "report.html"
    assert args.no_ocr is False
    assert args.materialize is False
    assert args.db is None
    
    # Case 2: Config values present, not overridden by CLI
    args = parser.parse_args([])
    config = {
        "roots": ["/conf/root"],
        "exclude": ["*.log"],
        "threshold": 4,
        "output": "conf.html",
        "no_ocr": True,
        "materialize": True,
        "db": "/conf/db",
    }
    merge_into_args(args, config)
    assert args.paths == ["/conf/root"]
    assert args.exclude == ["*.log"]
    assert args.threshold == 4
    assert args.output == "conf.html"
    assert args.no_ocr is True
    assert args.materialize is True
    assert args.db == Path("/conf/db")
    
    # Case 3: CLI overrides config (roots replace, exclude stacks, scalar overrides, tri-states override)
    args = parser.parse_args(["/cli/root", "--exclude", "*.tmp", "--threshold", "7", "--output", "cli.html", "--ocr", "--no-materialize", "--db", "/cli/db"])
    # CLI parsed: no_ocr=False, ocr=True (means ocr is requested, i.e., no_ocr should be False)
    # CLI parsed: materialize=False, no_materialize=True (means no_materialize is requested, i.e., materialize should be False)
    config = {
        "roots": ["/conf/root"],
        "exclude": ["*.log"],
        "threshold": 4,
        "output": "conf.html",
        "no_ocr": True,
        "materialize": True,
        "db": "/conf/db",
    }
    merge_into_args(args, config)
    assert args.paths == ["/cli/root"]
    assert args.exclude == ["*.log", "*.tmp"]
    assert args.threshold == 7
    assert args.output == "cli.html"
    assert args.no_ocr is False
    assert args.materialize is False
    assert args.db == Path("/cli/db")
