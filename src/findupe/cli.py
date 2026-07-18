"""findupe CLI: scan / apply / undo / cache clear.

scan never deletes; apply only acts on a reviewed selection JSON and asks for a
typed confirmation; undo restores from the Trash. See README for the workflow.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import grouping
from .cache import Cache
from .discover import discover
from .grouping import build_families
from .hashing import ensure_hashes, group_exact
from .imaging import compute_perceptual
from .ocr import NullOcrBackend, default_backend
from .screenshots import is_screenshot
from .dashboard import render_dashboard_html
from .ledger import list_scans, load_scan, record_scan
from .models import FileRecord, ScanResult
from .report import _is_image_family, generate_reports
from .stats import (
    aggregate_undo_totals,
    applied_scan_ids,
    duplicates_timeline,
    reclaimed_timeline,
    render_stats_text,
)
from .trash import FakeTrasher, FinderTrasher, apply_selection, list_manifests, undo


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n} B"


def _collect_hash_errors(
    records: list[FileRecord], companions: list[FileRecord]
) -> list[tuple[Path, str]]:
    """Dedup by path: discover._attach_companions appends one shared sidecar
    (e.g. an XMP) to EVERY primary in its stem-group, so a companion with the
    same path can appear more than once in `companions` — one real problem
    file must not be counted or reported more than once."""
    seen: dict[Path, str] = {}
    for rec in records + companions:
        if rec.hash_error and rec.path not in seen:
            seen[rec.path] = rec.hash_error
    return list(seen.items())


def cmd_scan(args: argparse.Namespace) -> int:
    roots = [Path(p).expanduser().resolve() for p in args.paths]
    bad_roots = [r for r in roots if not r.is_dir()]
    if bad_roots:
        for r in bad_roots:
            print(f"error: {r}: not a directory or not mounted", file=sys.stderr)
        return 2
    print(f"discovering files under {len(roots)} root(s)…")
    disc = discover(roots, exclude_globs=args.exclude, materialize=args.materialize)
    print(f"  {len(disc.records)} files · {len(disc.skipped_stubs)} cloud stubs skipped · "
          f"{len(disc.skipped_managed)} managed libraries refused · {len(disc.hardlink_notes)} hardlinks · "
          f"{len(disc.zero_byte)} zero-byte · {len(disc.errors)} read errors")
    if disc.skipped_managed:
        print("  refused (managed libraries):")
        for p in disc.skipped_managed:
            print(f"    {p}")
    if disc.errors:
        print(f"  {len(disc.errors)} read errors — see report notes for details")

    with Cache(args.db) as cache:
        print("exact pass (BLAKE2b funnel)…")
        exact = group_exact(disc.records, cache=cache)
        print(f"  {len(exact)} exact-duplicate groups")

        print("perceptual pass (images)…")
        compute_perceptual(disc.records, cache=cache, workers=args.workers)

        ocr_backend = NullOcrBackend() if args.no_ocr else default_backend()
        families, possible = build_families(
            disc.records, exact, threshold_possible=args.threshold,
            ocr_backend=ocr_backend, is_screenshot=is_screenshot,
        )
        ocr_touched = [r for r in disc.records if r.ocr_text is not None]
        if ocr_touched:
            cache.store(ocr_touched)
        members = [r for f in families for p in f.partitions for r in p.files]
        companions = [c for r in members for c in r.companions]
        ensure_hashes(members + companions, cache=cache)

    hash_errors = _collect_hash_errors(disc.records, companions)
    pointer = " — see report notes for details" if hash_errors else ""
    print(f"  {len(hash_errors)} decode/hash errors{pointer}")

    scan_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    scan = ScanResult(
        scan_id=scan_id, roots=roots, families=families,
        skipped_stubs=disc.skipped_stubs, skipped_managed=disc.skipped_managed,
        errors=disc.errors, hardlink_notes=disc.hardlink_notes, zero_byte=disc.zero_byte,
        hash_errors=hash_errors,
    )
    img_path, other_path = generate_reports(scan, possible, Path(args.output))

    img_families = [f for f in families if _is_image_family(f)]
    other_families = [f for f in families if not _is_image_family(f)]

    def _cat_summary(label: str, fams: list, path: Path) -> None:
        surplus = sum(f.surplus_count for f in fams)
        reclaimable = sum(f.surplus_bytes for f in fams)
        print(f"  {label}: {len(fams)} families · {surplus} surplus · "
              f"{_fmt_bytes(reclaimable)} reclaimable — {path.resolve()}")

    print(f"\n{len(families)} duplicate families · {len(possible)} possible matches (review-only)")
    _cat_summary("images", img_families, img_path)
    _cat_summary("other ", other_families, other_path)
    print("next:   open each report, review, Export selection, then run apply once per\n"
          "        exported file, e.g.:\n"
          f"        findupe apply findupe-selection-{scan_id}-images.json\n"
          f"        findupe apply findupe-selection-{scan_id}-other.json")

    try:
        record_scan(scan, possible, img_families, other_families,
                    (img_path, other_path), scans_dir=args.scans_dir)
    except Exception as e:  # archival is a convenience on an already-succeeded scan
        print(f"warning: could not archive this scan to history: {e}", file=sys.stderr)
    return 0


def _demo_root() -> Path:
    """Locate the bundled examples/ dataset: wheel-installed package data first
    (real installs), falling back to the repo-root examples/ for editable/dev
    installs, where force-include never ran."""
    try:
        from importlib.resources import files

        packaged = files("findupe") / "examples"
        if packaged.is_dir():
            return Path(str(packaged))
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    dev_path = Path(__file__).resolve().parents[2] / "examples"
    if dev_path.is_dir():
        return dev_path
    raise FileNotFoundError(
        "bundled examples/ dataset not found — reinstall findupe or run from a full checkout"
    )


def _open_in_finder(path: Path) -> None:
    subprocess.run(["open", str(path)], check=False)


def cmd_demo(args: argparse.Namespace) -> int:
    src = _demo_root()
    scratch = Path(args.demo_dir) if args.demo_dir else Path(tempfile.mkdtemp(prefix="findupe-demo-"))
    dest = scratch / "examples"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    print(f"--demo: copied the bundled sample photos to {dest}")

    args.paths = [str(dest / "inbox"), str(dest / "backup")]
    args.exclude = []
    args.materialize = False
    args.threshold = grouping.THRESHOLD_POSSIBLE
    args.output = str(scratch / "report.html")
    args.no_ocr = False
    args.workers = 0
    rc = cmd_scan(args)
    if rc != 0:
        return rc

    report_path = scratch / "report-images.html"
    print(f"opening {report_path.name} …")
    _open_in_finder(report_path)
    return 0


def _make_trasher(args: argparse.Namespace):
    return FakeTrasher(Path(args.trash_dir)) if args.trash_dir else FinderTrasher()


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        selection = json.loads(Path(args.selection).read_text())
    except OSError as e:
        print(f"cannot read selection file: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"selection file is not valid JSON: {e}", file=sys.stderr)
        return 2
    trasher = _make_trasher(args)

    plan, manifest_path = apply_selection(
        selection, trasher, dry_run=True, undo_dir=args.undo_dir
    )
    if plan.fatal:
        print(f"REFUSED: {plan.fatal}", file=sys.stderr)
        return 2
    for fam, reason in plan.rejected_families.items():
        print(f"  rejected {fam}: {reason}", file=sys.stderr)
    for path, reason in plan.skipped:
        print(f"  skipped  {path}: {reason}", file=sys.stderr)
    if not plan.to_trash:
        print("nothing to do (all entries were skipped or rejected)")
        return 1

    comps = f" + {len(plan.companions)} companion file(s)" if plan.companions else ""
    print(f"will move {len(plan.to_trash)} file(s){comps} "
          f"({_fmt_bytes(plan.bytes_to_trash)}) to the Trash")
    if args.dry_run:
        for e in plan.to_trash:
            print(f"  would trash: {e['path']}")
        for c in plan.companions:
            print(f"  would trash: {c['path']} (companion)")
        return 0

    answer = input("type 'trash' to confirm (anything else aborts): ")
    if answer.strip().lower() != "trash":
        print("aborted — nothing was moved")
        return 1

    plan, manifest_path = apply_selection(
        selection, trasher, dry_run=False, undo_dir=args.undo_dir
    )
    print(f"trashed {len(plan.to_trash)} file(s); "
          f"{len(plan.skipped)} skipped (see above)")
    if manifest_path:
        print(f"undo manifest: {manifest_path}")
        print(f"to restore:    findupe undo {manifest_path.name}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    manifests = list_manifests(args.undo_dir)
    if not args.manifest:
        if not manifests:
            print("no undo manifests")
            return 0
        for m in manifests:
            print(m.name)
        return 0
    match = next(
        (m for m in manifests if m.name == args.manifest or m.stem == args.manifest
         or m.name.startswith(args.manifest)),
        None,
    )
    if match is None:
        candidate = Path(args.manifest)
        match = candidate if candidate.is_file() else None
    if match is None:
        print(f"no manifest matching {args.manifest!r}", file=sys.stderr)
        return 2
    results = undo(match, trasher=_make_trasher(args), undo_dir=args.undo_dir)
    for path, outcome in results:
        print(f"  {outcome}: {path}")
    restored = sum(1 for _, o in results if o == "restored")
    print(f"restored {restored}/{len(results)}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    records = list_scans(args.scans_dir)
    totals = aggregate_undo_totals(args.undo_dir, args.scans_dir)
    applied = applied_scan_ids(args.undo_dir)
    print(render_stats_text(records, totals, applied))
    if args.html is not None:
        html_doc = render_dashboard_html(
            records, totals, applied,
            reclaimed_timeline(args.undo_dir), duplicates_timeline(records),
        )
        args.html.write_text(html_doc, encoding="utf-8")
        print(f"dashboard: {args.html.resolve()}")
    return 0


def _find_scan(scan_id: str, scans_dir: Path):
    """Prefix-tolerant lookup, mirroring cmd_undo's manifest matching."""
    exact = load_scan(scan_id, scans_dir)
    if exact is not None:
        return exact
    return next((r for r in list_scans(scans_dir) if r.scan_id.startswith(scan_id)), None)


def cmd_history(args: argparse.Namespace) -> int:
    if not args.scan_id:
        records = list_scans(args.scans_dir)
        if not records:
            print("no archived scans")
            return 0
        applied = applied_scan_ids(args.undo_dir)
        for r in records:
            tag = "applied" if r.scan_id in applied else "not applied"
            print(f"{r.scan_id}  {r.duplicate_families} families  "
                  f"{_fmt_bytes(r.surplus_bytes)} reclaimable — [{tag}]")
        return 0
    rec = _find_scan(args.scan_id, args.scans_dir)
    if rec is None:
        print(f"no archived scan matching {args.scan_id!r}", file=sys.stderr)
        return 2
    applied = rec.scan_id in applied_scan_ids(args.undo_dir)
    print(f"{rec.scan_id}  ({'applied' if applied else 'not applied'})")
    print(f"  {rec.duplicate_families} duplicate families · {rec.possible_matches} possible matches")
    for category, path in rec.report_paths.items():
        print(f"  {category}: {path.resolve() if path else '(report copy missing)'}")
    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    with Cache(args.db) as cache:
        cache.clear()
        db_path = cache.db_path
    print(f"cache cleared: {db_path}")
    return 0


NO_ARGS_MESSAGE = """\
findupe — safe duplicate finder & reviewer for macOS

  1. findupe scan <folder> [<folder> ...]   find duplicates, write an HTML report
  2. open the report, review, Export selection
  3. findupe apply <selection>.json         move checked files to the Trash

New here? Run `findupe --demo` for a zero-setup walkthrough on bundled sample photos.
See `findupe --help` for all commands."""

EPILOG = """\
example:
  findupe scan ~/Pictures/inbox
  open report-images.html            # review thumbnails, Export selection
  findupe apply findupe-selection-<id>-images.json --dry-run
  findupe apply findupe-selection-<id>-images.json

new here? `findupe --demo` runs this whole flow on bundled sample photos.
full docs: https://github.com/jyshnkr/findupe#readme"""


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("findupe requires macOS — it relies on Finder/AppleScript for Trash "
              "integration and APFS-specific behavior.", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        prog="findupe",
        description="Safe duplicate finder: scan -> review HTML report -> apply -> (undo)",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # default=None, not a pre-resolved path: each is resolved lazily inside the
    # function that actually needs it, only when still None, so passing an
    # explicit flag here genuinely bypasses the one-time
    # ~/.dupefinder -> ~/.findupe migration check — it never fires just
    # because argparse filled in a default.
    parser.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--undo-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--scans-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--trash-dir", help="use a plain directory instead of the macOS Trash")
    parser.add_argument("--demo", action="store_true",
                        help="copy bundled sample photos to a scratch dir, scan them, and open the report")
    parser.add_argument("--demo-dir", default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=False)

    p_scan = sub.add_parser("scan", help="find duplicates and write the review report")
    p_scan.add_argument("paths", nargs="+")
    p_scan.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p_scan.add_argument("--materialize", action="store_true",
                        help="download iCloud stubs instead of skipping them")
    p_scan.add_argument("--threshold", type=int, default=grouping.THRESHOLD_POSSIBLE,
                        help="max pHash distance for the review-only 'possible' tier")
    p_scan.add_argument("-o", "--output", default="report.html",
                        help="base report path; writes <name>-images.html and <name>-other.html")
    p_scan.add_argument("--no-ocr", action="store_true",
                        help="skip the screenshot-text demoter (default: on, macOS only)")
    p_scan.add_argument("--workers", type=int, default=4, help=argparse.SUPPRESS)
    p_scan.set_defaults(func=cmd_scan)

    p_apply = sub.add_parser("apply", help="move a reviewed selection to the Trash")
    p_apply.add_argument("selection")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_undo = sub.add_parser("undo", help="restore a previous apply (no arg: list manifests)")
    p_undo.add_argument("manifest", nargs="?")
    p_undo.set_defaults(func=cmd_undo)

    p_cache = sub.add_parser("cache", help="cache maintenance")
    cache_sub = p_cache.add_subparsers(dest="cache_command", required=True)
    p_clear = cache_sub.add_parser("clear")
    p_clear.set_defaults(func=cmd_cache_clear)

    p_stats = sub.add_parser("stats", help="all-time totals across every scan and apply")
    p_stats.add_argument("--html", nargs="?", const=Path("findupe-dashboard.html"),
                         default=None, type=Path, metavar="PATH",
                         help="also write an HTML dashboard (optional output path)")
    p_stats.set_defaults(func=cmd_stats)

    p_history = sub.add_parser("history", help="list archived scans, or show one by id")
    p_history.add_argument("scan_id", nargs="?")
    p_history.set_defaults(func=cmd_history)

    args = parser.parse_args(argv)
    if args.demo:
        return cmd_demo(args)
    if args.command is None:
        print(NO_ARGS_MESSAGE)
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted — nothing partial was deleted; cache keeps completed work",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
