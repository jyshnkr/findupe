"""dupefinder CLI: scan / apply / undo / cache clear.

scan never deletes; apply only acts on a reviewed selection JSON and asks for a
typed confirmation; undo restores from the Trash. See README for the workflow.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import grouping
from .cache import DEFAULT_DB, Cache
from .discover import discover
from .grouping import build_families
from .hashing import ensure_hashes, group_exact
from .imaging import compute_perceptual
from .models import FileRecord, ScanResult
from .report import _is_image_family, generate_reports
from .trash import UNDO_DIR, FakeTrasher, FinderTrasher, apply_selection, list_manifests, undo


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

        families, possible = build_families(
            disc.records, exact, threshold_possible=args.threshold
        )
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
          f"        dupefinder apply dupefinder-selection-{scan_id}-images.json\n"
          f"        dupefinder apply dupefinder-selection-{scan_id}-other.json")
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
        print(f"to restore:    dupefinder undo {manifest_path.name}")
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


def cmd_cache_clear(args: argparse.Namespace) -> int:
    with Cache(args.db) as cache:
        cache.clear()
    print(f"cache cleared: {args.db}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dupefinder",
        description="Safe duplicate finder: scan -> review HTML report -> apply -> (undo)",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=argparse.SUPPRESS)
    parser.add_argument("--undo-dir", type=Path, default=UNDO_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--trash-dir", help="use a plain directory instead of the macOS Trash")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="find duplicates and write the review report")
    p_scan.add_argument("paths", nargs="+")
    p_scan.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    p_scan.add_argument("--materialize", action="store_true",
                        help="download iCloud stubs instead of skipping them")
    p_scan.add_argument("--threshold", type=int, default=grouping.THRESHOLD_POSSIBLE,
                        help="max pHash distance for the review-only 'possible' tier")
    p_scan.add_argument("-o", "--output", default="report.html",
                        help="base report path; writes <name>-images.html and <name>-other.html")
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

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted — nothing partial was deleted; cache keeps completed work",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
