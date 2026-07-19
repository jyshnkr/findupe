"""Single owner of the config file — schema, load, template render, writes, and the arg-merge."""

from __future__ import annotations

import os
import tomllib
import argparse
import json
from pathlib import Path
from collections import namedtuple
from . import paths

ConfigKey = namedtuple("ConfigKey", ["type", "is_list", "default", "help_text", "is_path", "example"])

KNOWN_KEYS = {
    "roots": ConfigKey(str, True, [], "List of default directories to scan", True, ["~/Pictures/inbox"]),
    "exclude": ConfigKey(str, True, [], "List of glob patterns to exclude from scans", False, ["**/.*", "**/*.lrdata/**"]),
    "threshold": ConfigKey(int, False, 8, "Max pHash distance for the review-only 'possible' tier", False, 8),
    "no_ocr": ConfigKey(bool, False, False, "Skip the screenshot-text demoter (default: false)", False, False),
    "materialize": ConfigKey(bool, False, False, "Download iCloud stubs instead of skipping them (default: false)", False, False),
    "output": ConfigKey(str, False, "report.html", "Base report path; writes <name>-images.html and <name>-other.html", True, "report.html"),
    "db": ConfigKey(str, False, None, "Database path for hashing cache", True, "~/.findupe/index.db"),
    "scans_dir": ConfigKey(str, False, None, "Directory for archived scans", True, "~/.findupe/scans"),
    "undo_dir": ConfigKey(str, False, None, "Directory for undo manifests", True, "~/.findupe/undo"),
    "trash_dir": ConfigKey(str, False, None, "Plain directory to use instead of macOS Trash", True, "~/.findupe/trash"),
}

class ConfigError(Exception):
    pass

def config_read_path() -> Path | None:
    """Deliberately does not call paths.resolve_data_home() — merely reading
    config must never trigger the one-time ~/.dupefinder -> ~/.findupe migration,
    so that an explicit --db/--scans-dir/--undo-dir flag still bypasses it."""
    env_val = os.environ.get("FINDUPE_CONFIG")
    if env_val:
        p = Path(env_val)
        return p if p.exists() else None
    
    p_new = paths.DATA_HOME / "config.toml"
    if p_new.exists():
        return p_new
        
    p_legacy = paths.LEGACY_DATA_HOME / "config.toml"
    if p_legacy.exists():
        return p_legacy
        
    return None

def config_write_path() -> Path:
    env_val = os.environ.get("FINDUPE_CONFIG")
    if env_val:
        return Path(env_val)
    return paths.resolve_data_home(paths.LEGACY_DATA_HOME, paths.DATA_HOME) / "config.toml"

def load_raw_config() -> dict:
    path = config_read_path()
    if path is None or not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"Could not read config file {path}: {e}")
        
    if not content.strip():
        return {}
        
    try:
        parsed = tomllib.loads(content)
    except Exception as e:
        raise ConfigError(f"Failed to parse TOML in {path}: {e}")
        
    # Validate keys and types
    for key, val in parsed.items():
        if key not in KNOWN_KEYS:
            raise ConfigError(f"Unknown configuration key: {key}")
        spec = KNOWN_KEYS[key]
        if spec.is_list:
            if not isinstance(val, list):
                raise ConfigError(f"Configuration key '{key}' must be a list, got {type(val).__name__}")
            for item in val:
                if not isinstance(item, spec.type):
                    raise ConfigError(f"Elements of configuration key '{key}' must be {spec.type.__name__}, got {type(item).__name__}")
        else:
            # bool is a subclass of int — reject it explicitly so `threshold = true`
            # doesn't silently pass validation and become threshold=1 downstream.
            if isinstance(val, bool) and spec.type is not bool:
                raise ConfigError(f"Configuration key '{key}' must be {spec.type.__name__}, got bool")
            if not isinstance(val, spec.type):
                raise ConfigError(f"Configuration key '{key}' must be {spec.type.__name__}, got {type(val).__name__}")
    return parsed

def load_config() -> dict:
    parsed = load_raw_config()
    expanded = {}
    for key, val in parsed.items():
        spec = KNOWN_KEYS[key]
        if spec.is_path:
            if spec.is_list:
                expanded[key] = [os.path.expanduser(os.path.expandvars(el)) for el in val]
            else:
                if val is not None:
                    expanded[key] = os.path.expanduser(os.path.expandvars(val))
                else:
                    expanded[key] = None
        else:
            expanded[key] = val
    return expanded

def render_template(values: dict) -> str:
    lines = [
        "# findupe configuration file",
        "#",
        "# To change a setting, uncomment the line (remove the leading '# ') and set your value.",
        ""
    ]
    order = list(KNOWN_KEYS.keys())
    for key in order:
        spec = KNOWN_KEYS[key]
        lines.append(f"# {spec.help_text}")
        if key in values:
            val_str = json.dumps(values[key], ensure_ascii=False)
            lines.append(f"{key} = {val_str}")
        else:
            example_str = json.dumps(spec.example)
            lines.append(f"# {key} = {example_str}")
        lines.append("")
    return "\n".join(lines)

def write_values(values: dict) -> None:
    path = config_write_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_template(values)
    path.write_text(content, encoding="utf-8")

def merge_into_args(args: argparse.Namespace, config: dict) -> None:
    # Store config_path in args so cmd_scan can print the safety echo!
    args.config_path = config_read_path()

    # 1. roots / paths: CLI replaces config.
    if hasattr(args, "paths"):
        if not args.paths:  # CLI provided no paths
            args.paths = config.get("roots", [])

    # 2. exclude: CLI adds to config (always stacks).
    if hasattr(args, "exclude"):
        config_excludes = config.get("exclude", [])
        cli_excludes = args.exclude or []
        args.exclude = list(config_excludes) + list(cli_excludes)

    # 3. threshold: CLI > config > built-in default
    if hasattr(args, "threshold"):
        if args.threshold is None:
            if "threshold" in config:
                args.threshold = config["threshold"]
            else:
                from . import grouping
                args.threshold = grouping.THRESHOLD_POSSIBLE

    # 4. output: CLI > config > default "report.html"
    if hasattr(args, "output"):
        if args.output is None:
            if "output" in config:
                args.output = config["output"]
            else:
                args.output = "report.html"

    # 5. no_ocr: CLI (ocr/no-ocr) > config > default False
    if hasattr(args, "no_ocr"):
        cli_ocr = getattr(args, "ocr", False)
        cli_no_ocr = getattr(args, "no_ocr", False)
        if cli_no_ocr:
            cli_val = True
        elif cli_ocr:
            cli_val = False
        else:
            cli_val = None
            
        if cli_val is not None:
            args.no_ocr = cli_val
        elif "no_ocr" in config:
            args.no_ocr = config["no_ocr"]
        else:
            args.no_ocr = False

    # 6. materialize: CLI (materialize/no-materialize) > config > default False
    if hasattr(args, "materialize"):
        cli_mat = getattr(args, "materialize", False)
        cli_no_mat = getattr(args, "no_materialize", False)
        if cli_mat:
            cli_val = True
        elif cli_no_mat:
            cli_val = False
        else:
            cli_val = None
            
        if cli_val is not None:
            args.materialize = cli_val
        elif "materialize" in config:
            args.materialize = config["materialize"]
        else:
            args.materialize = False

    # 7. storage dirs: db, scans_dir, undo_dir, trash_dir
    for key in ("db", "scans_dir", "undo_dir"):
        if hasattr(args, key) and getattr(args, key) is None:
            val = config.get(key)
            if val is not None:
                setattr(args, key, Path(val))
                
    if hasattr(args, "trash_dir") and getattr(args, "trash_dir") is None:
        val = config.get("trash_dir")
        if val is not None:
            setattr(args, "trash_dir", val)
