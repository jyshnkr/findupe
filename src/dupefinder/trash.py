"""Everything with delete authority, in one auditable module.

Safety contract (spec §safety-model):
- Only a reviewed selection JSON grants authority, and only after re-verification:
  every keeper AND every candidate is re-checked (exists, size, full BLAKE2b)
  against scan-time values. Mismatch = skip (candidate) or reject family (keeper).
- A path appearing in both delete and keep lists rejects the whole selection.
- Survival is enforced here, independently of the report UI: no (family, format)
  loses a file unless a verified keeper for that partition survives.
- Files move to the real macOS Trash via batched Finder AppleScript ("Put Back"
  works on all volumes). If Finder automation is unavailable, we abort loudly —
  there is deliberately no fallback deleter.
- The undo manifest is written atomically BEFORE anything is trashed; `undo`
  locates files in the Trash by size+hash (immune to Finder's collision renames)
  and moves them back, never overwriting.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .hashing import full_hash

UNDO_DIR = Path.home() / ".dupefinder" / "undo"
_BATCH = 100


# ---------------------------------------------------------------- trashers

class FinderTrasher:
    """Move files to the Trash via Finder — Put Back works for every file."""

    def trash(self, paths: list[Path]) -> dict[Path, str | None]:
        results: dict[Path, str | None] = {}
        for i in range(0, len(paths), _BATCH):
            batch = paths[i : i + _BATCH]
            if self._batch(batch):
                results.update({p: None for p in batch})
            else:  # isolate the failure per file
                for p in batch:
                    results[p] = None if self._batch([p]) else "Finder could not trash this file"
        return results

    @staticmethod
    def _escape(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace('"', '\\"')

    def _batch(self, paths: list[Path]) -> bool:
        items = ", ".join(f'POSIX file "{self._escape(p)}"' for p in paths)
        script = f'tell application "Finder"\n  delete {{ {items} }}\nend tell'
        try:
            proc = subprocess.run(
                ["osascript", "-"], input=script, capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0

    @staticmethod
    def preflight(volumes: set[str]) -> dict[str, str]:
        """Per-volume Trash check. Returns {volume: reason} for refused volumes."""
        problems = {}
        for vol in volumes:
            if vol == "/":
                continue  # home Trash always exists
            trashes = Path(vol) / ".Trashes"
            if not trashes.exists():
                problems[vol] = (
                    f"{vol} has no .Trashes directory — Finder would delete PERMANENTLY. "
                    "Refusing. (Eject/remount the drive, or trash these files manually in Finder.)"
                )
        return problems

    @staticmethod
    def trash_locations(volumes: set[str]) -> list[Path]:
        locs = [Path.home() / ".Trash"]
        for vol in volumes:
            if vol != "/":
                locs.append(Path(vol) / ".Trashes" / str(os.getuid()))
        return [loc for loc in locs if loc.is_dir()]


class FakeTrasher:
    """Test double: 'Trash' is a plain directory, collisions get numeric suffixes."""

    def __init__(self, trash_dir: Path) -> None:
        self.trash_dir = trash_dir
        trash_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[list[Path]] = []

    def trash(self, paths: list[Path]) -> dict[Path, str | None]:
        self.calls.append(list(paths))
        results: dict[Path, str | None] = {}
        for p in paths:
            dest = self.trash_dir / p.name
            n = 1
            while dest.exists():
                n += 1
                dest = self.trash_dir / f"{p.stem} {n}{p.suffix}"
            try:
                shutil.move(str(p), dest)
                results[p] = None
            except OSError as e:
                results[p] = str(e)
        return results

    def preflight(self, volumes: set[str]) -> dict[str, str]:
        return {}

    def trash_locations(self, volumes: set[str]) -> list[Path]:
        return [self.trash_dir]


# ---------------------------------------------------------------- verification

@dataclass
class ApplyPlan:
    to_trash: list[dict] = field(default_factory=list)      # verified delete entries
    companions: list[dict] = field(default_factory=list)    # verified, deduplicated ride-alongs
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (path, reason)
    rejected_families: dict[str, str] = field(default_factory=dict)  # family -> reason
    fatal: str | None = None

    @property
    def bytes_to_trash(self) -> int:
        return sum(e["size"] for e in self.to_trash)


def _verify_entry(entry: dict) -> str | None:
    """Re-verify a selection entry against the live file. None = OK."""
    path = Path(entry["path"])
    try:
        st = path.stat()
    except OSError:
        return "no longer exists"
    if st.st_size != entry["size"]:
        return f"size changed ({entry['size']} -> {st.st_size})"
    try:
        if full_hash(path) != entry["blake2b"]:
            return "content changed since scan (hash mismatch)"
    except OSError as e:
        return f"unreadable: {e}"
    return None


def _validate_selection(selection: dict) -> str | None:
    """Structural validation so a malformed/hand-edited file fails loudly, not with
    a traceback. Returns an error string or None."""
    if not isinstance(selection, dict):
        return "selection is not a JSON object"
    if selection.get("schema_version") != "1":
        return "unsupported or missing selection schema_version"
    for section in ("delete", "keep"):
        entries = selection.get(section, [])
        if not isinstance(entries, list):
            return f"'{section}' must be a list"
        for e in entries:
            if not isinstance(e, dict):
                return f"'{section}' contains a non-object entry"
            for field_name, typ in (("path", str), ("size", int), ("blake2b", str),
                                    ("family", str), ("format", str)):
                if not isinstance(e.get(field_name), typ):
                    return f"'{section}' entry missing or invalid '{field_name}': {e.get('path', '?')}"
            comps = e.get("companions", [])
            if not isinstance(comps, list):
                return f"'companions' must be a list on {e['path']}"
            for c in comps:
                if not isinstance(c, dict) or not isinstance(c.get("path"), str) \
                        or not isinstance(c.get("size"), int):
                    return f"invalid companion entry on {e['path']}"
    return None


def _cluster_key(e: dict) -> tuple[str, str, str]:
    return (e["family"], e["format"], e.get("cluster", ""))


def build_apply_plan(selection: dict) -> ApplyPlan:
    plan = ApplyPlan()

    err = _validate_selection(selection)
    if err:
        plan.fatal = err
        return plan
    deletes: list[dict] = selection.get("delete", [])
    keeps: list[dict] = selection.get("keep", [])
    if not deletes:
        plan.fatal = "selection contains no files to delete"
        return plan

    delete_paths = {e["path"] for e in deletes}
    if any(k["path"] in delete_paths for k in keeps):
        plan.fatal = (
            "selection lists a keeper for deletion — refusing the entire selection "
            "(this should be impossible from an unmodified report)"
        )
        return plan

    # keepers first: a cluster whose keeper fails verification loses ALL deletions
    keeper_ok: dict[tuple[str, str, str], bool] = {}
    for k in keeps:
        err = _verify_entry(k)
        key = _cluster_key(k)
        keeper_ok[key] = keeper_ok.get(key, True) and err is None
        if err:
            plan.rejected_families[k["family"]] = f"keeper {k['path']}: {err}"

    seen_companions: dict[str, dict] = {}  # path -> entry, deduplicated across primaries
    for e in deletes:
        key = _cluster_key(e)
        if key not in keeper_ok:
            plan.rejected_families.setdefault(
                e["family"], f"no keeper recorded for cluster {key} — refusing its deletions"
            )
            plan.skipped.append((e["path"], "no verified keeper for its cluster"))
            continue
        if not keeper_ok[key]:
            plan.skipped.append((e["path"], "keeper failed verification"))
            continue
        err = _verify_entry(e)
        if err:
            plan.skipped.append((e["path"], err))
            continue
        plan.to_trash.append(e)
        for c in e.get("companions", []):
            if c["path"] in seen_companions or c["path"] in delete_paths:
                continue
            # companions are verified like any candidate; a modified sidecar is
            # left in place (orphaned but intact) rather than trashed unverified
            cerr = _verify_entry(c) if c.get("blake2b") else (
                None if Path(c["path"]).exists() else "no longer exists"
            )
            if cerr:
                plan.skipped.append((c["path"], f"companion: {cerr}"))
                continue
            seen_companions[c["path"]] = c

    plan.companions = list(seen_companions.values())
    return plan


# ---------------------------------------------------------------- manifest + apply

def _write_json_atomic(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply_selection(
    selection: dict,
    trasher,
    dry_run: bool = False,
    undo_dir: Path = UNDO_DIR,
) -> tuple[ApplyPlan, Path | None]:
    """Verify and execute a selection. Returns (plan, undo_manifest_path)."""
    plan = build_apply_plan(selection)
    if plan.fatal or dry_run or not plan.to_trash:
        return plan, None

    volumes = {_volume_of(Path(e["path"])) for e in plan.to_trash}
    problems = trasher.preflight(volumes)
    if problems:
        blocked = set(problems)
        kept = []
        for e in plan.to_trash:
            vol = _volume_of(Path(e["path"]))
            if vol in blocked:
                plan.skipped.append((e["path"], problems[vol]))
            else:
                kept.append(e)
        plan.to_trash = kept
        plan.companions = [c for c in plan.companions if _volume_of(Path(c["path"])) not in blocked]
        if not plan.to_trash:
            return plan, None

    scan_id = selection.get("scan_id", "unknown")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = undo_dir / f"{scan_id}-{stamp}.json"
    entries = [
        {"path": e["path"], "size": e["size"], "blake2b": e["blake2b"], "status": "pending"}
        for e in plan.to_trash
    ] + [
        # scan-time size/hash, NOT re-statted here: undo must locate what was
        # verified, and hash-matching makes restore immune to Trash renames
        {"path": c["path"], "size": c["size"], "blake2b": c.get("blake2b"),
         "status": "pending", "companion": True}
        for c in plan.companions
    ]
    manifest = {
        "schema_version": "1",
        "scan_id": scan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "volumes": sorted(volumes),
        "entries": entries,
    }
    _write_json_atomic(manifest_path, manifest)  # intent on disk BEFORE any trash call

    all_paths = [Path(e["path"]) for e in plan.to_trash] + [
        Path(c["path"]) for c in plan.companions
    ]
    outcomes = trasher.trash(all_paths)
    for entry in manifest["entries"]:
        err = outcomes.get(Path(entry["path"]), "not attempted")
        entry["status"] = "trashed" if err is None else f"failed: {err}"
    _write_json_atomic(manifest_path, manifest)

    for entry in manifest["entries"]:
        if entry["status"] != "trashed" and not entry.get("companion"):
            plan.skipped.append((entry["path"], entry["status"]))
    plan.to_trash = [
        e for e in plan.to_trash
        if next(m for m in manifest["entries"] if m["path"] == e["path"])["status"] == "trashed"
    ]
    return plan, manifest_path


def _volume_of(path: Path) -> str:
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return f"/{parts[1]}/{parts[2]}"
    return "/"


# ---------------------------------------------------------------- undo

def list_manifests(undo_dir: Path = UNDO_DIR) -> list[Path]:
    if not undo_dir.is_dir():
        return []
    return sorted(undo_dir.glob("*.json"))


def undo(
    manifest_path: Path,
    trasher=None,
    undo_dir: Path = UNDO_DIR,
) -> list[tuple[str, str]]:
    """Restore trashed files to their original paths. Returns [(path, outcome)]."""
    trasher = trasher or FinderTrasher()
    manifest = json.loads(manifest_path.read_text())
    volumes = set(manifest.get("volumes", []))
    locations = trasher.trash_locations(volumes)
    results: list[tuple[str, str]] = []

    for entry in manifest["entries"]:
        if entry["status"] != "trashed":
            results.append((entry["path"], f"not restored ({entry['status']})"))
            continue
        original = Path(entry["path"])
        if original.exists():
            results.append((entry["path"], "skipped: a file already exists at the original path"))
            continue
        found = _find_in_trash(entry, locations)
        if found is None:
            results.append((entry["path"], "not found in Trash (emptied or renamed?)"))
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(found), original)
            entry["status"] = "restored"
            results.append((entry["path"], "restored"))
        except OSError as e:
            results.append((entry["path"], f"restore failed: {e}"))

    _write_json_atomic(manifest_path, manifest)
    return results


def _find_in_trash(entry: dict, locations: list[Path]) -> Path | None:
    """Locate by size, confirm by hash — immune to Trash renames. Entries without
    a hash (shouldn't happen post-v2 selections) additionally require an exact
    name match so a same-size stranger is never restored in a file's place."""
    for loc in locations:
        try:
            candidates = [p for p in loc.iterdir() if p.is_file()]
        except OSError:
            continue
        for cand in candidates:
            try:
                if cand.stat().st_size != entry["size"]:
                    continue
                if entry.get("blake2b"):
                    if full_hash(cand) == entry["blake2b"]:
                        return cand
                elif cand.name == Path(entry["path"]).name:
                    return cand
            except OSError:
                continue
    return None
