"""Self-contained HTML review report.

Everything inline (CSS, vanilla JS, base64 thumbnails) so the file works offline
from file:// with no external requests. The report is the ONLY place selections
are made: checkboxes -> live counter -> "Export selection" downloads a JSON the
`apply` command will re-verify. Keeper checkboxes are disabled in the UI, and
`apply` re-validates survival independently — the UI is not the safety boundary.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path

from .imaging import thumbnail_b64
from .models import Family, FileRecord, ScanResult

Thumbnailer = Callable[[Path], str | None]


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _file_row(
    rec: FileRecord,
    family: Family,
    fmt: str,
    role: str,          # "keeper" | "cand" | "info"
    cluster_id: str,
    prechecked: bool,
    flagged: bool,
    thumb: Thumbnailer,
) -> str:
    p = html.escape(str(rec.path))
    h = html.escape(rec.exact_hash or "")
    img = ""
    if rec.is_image:
        b64 = thumb(rec.path)
        img = (
            f'<img loading="lazy" src="data:image/jpeg;base64,{b64}" alt="">'
            if b64 else '<div class="noimg">no preview</div>'
        )
    else:
        img = f'<div class="noimg fileicon">{html.escape(rec.format.upper())}</div>'
    dims = f"{rec.width}×{rec.height}" if rec.width else ""
    badges = []
    if role == "keeper":
        badges.append('<span class="badge keep">KEEPER</span>')
    if role == "info":
        badges.append('<span class="badge info" title="Related file — not a copy of anything; never deletable here">sibling</span>')
    if rec.cloud_synced:
        badges.append('<span class="badge cloud" title="In a cloud-synced folder: deleting propagates to your other devices">☁ synced</span>')
    if rec.is_clone:
        badges.append('<span class="badge clone" title="Shares physical storage with the keeper (APFS clone) — trashing this frees 0 bytes, even after the Trash is emptied">⧉ clone — 0 B freed</span>')
    for c in rec.companions:
        badges.append(f'<span class="badge comp" title="Trashed together with this file">+ {html.escape(c.path.name)}</span>')

    # data-reclaim is what trashing this file would actually free — 0 for a
    # clone — kept SEPARATE from data-size (the file's real size, which apply
    # re-verifies against the live file; conflating the two would make apply
    # wrongly treat a clone as "changed since scan" and skip it).
    reclaim = 0 if rec.is_clone else rec.size
    common = (
        f'data-family="{family.family_id}" data-format="{fmt}" data-cluster="{cluster_id}" '
        f'data-path="{p}" data-size="{rec.size}" data-reclaim="{reclaim}" data-hash="{h}"'
    )
    if role == "keeper":
        control = (
            f'<input type="checkbox" disabled class="keeper" '
            f'title="This is the suggested survivor — apply refuses to trash the last copy" {common}>'
        )
    elif role == "cand":
        # companions carry size+hash so apply can re-verify them like any candidate
        comps = html.escape(json.dumps([
            {"path": str(c.path), "size": c.size, "blake2b": c.exact_hash}
            for c in rec.companions
        ]))
        flag_attr = ' data-flagged="1"' if flagged else ""
        control = (
            f'<input type="checkbox" class="cand" {"checked" if prechecked else ""} '
            f'{common} data-companions="{comps}"{flag_attr}>'
        )
    else:
        control = ""  # informational rows carry no controls at all
    ocr_html = ""
    if rec.ocr_text:
        conf = f"{rec.ocr_confidence:.0%}" if rec.ocr_confidence is not None else "?"
        ocr_html = (
            f'<details class="ocr"><summary>OCR text ({conf} confidence)</summary>'
            f'<pre>{html.escape(rec.ocr_text)}</pre></details>'
        )
    return (
        f'<div class="file{" iskeeper" if role == "keeper" else ""}">'
        f"{control}{img}"
        f'<div class="meta"><code>{p}</code>'
        f"<small>{_fmt_bytes(rec.size)} {dims} {' '.join(badges)}</small>{ocr_html}</div></div>"
    )


def _family_html(fam: Family, thumb: Thumbnailer, checkable: bool) -> str:
    flags = "".join(
        f'<span class="badge warn">{html.escape(f)}</span>' for f in fam.flags
    )
    flagged = bool(fam.flags)
    precheck = checkable and not flagged  # flagged families are never pre-checked
    parts = []
    for part in fam.partitions:
        rows = []
        clustered = part.clustered if checkable else set()
        for cluster in (part.clusters if checkable else []):
            for rec in cluster.files:
                role = "keeper" if rec is cluster.keeper else "cand"
                rows.append(_file_row(
                    rec, fam, part.format, role, cluster.cluster_id,
                    prechecked=precheck and role == "cand",
                    flagged=flagged, thumb=thumb,
                ))
        for rec in part.files:
            if id(rec) not in clustered or not checkable:
                rows.append(_file_row(
                    rec, fam, part.format, "info", "",
                    prechecked=False, flagged=flagged, thumb=thumb,
                ))
        label = f'<div class="fmt">{html.escape(part.format)}</div>' if checkable else ""
        parts.append(f'<div class="partition">{label}{"".join(rows)}</div>')
    return (
        f'<div class="family" id="{fam.family_id}">'
        f'<div class="famhead">{fam.family_id} {flags}</div>{"".join(parts)}</div>'
    )


def _notes_html(scan: ScanResult) -> str:
    blocks = []

    def block(title: str, items: list[str]) -> None:
        if items:
            lis = "".join(f"<li><code>{html.escape(i)}</code></li>" for i in items[:200])
            more = f"<li>… and {len(items) - 200} more</li>" if len(items) > 200 else ""
            blocks.append(f"<details><summary>{title} ({len(items)})</summary><ul>{lis}{more}</ul></details>")

    block("Skipped: not local (iCloud/Dropbox stubs — rerun with --materialize to include)",
          [str(p) for p in scan.skipped_stubs])
    block("Skipped: managed libraries (Photos/Lightroom manage these internally)",
          [str(p) for p in scan.skipped_managed])
    block("Hardlinks (same physical file — deleting reclaims no space, excluded from candidates)",
          [f"{a} = {b}" for a, b in scan.hardlink_notes])
    block("Zero-byte files (excluded from duplicate detection)",
          [str(p) for p in scan.zero_byte])
    block("Errors (unreadable or undecodable — nothing was done to these)",
          [f"{p}: {e}" for p, e in scan.errors])
    block("Unreadable/undecodable during hashing (read fine, but couldn't be hashed/decoded — excluded from duplicate detection)",
          [f"{p}: {e}" for p, e in scan.hash_errors])
    return "".join(blocks)


_CSS = """
:root { color-scheme: light dark; font-family: -apple-system, system-ui, sans-serif; }
body { margin: 2rem auto; max-width: 70rem; padding: 0 1rem; }
.family { border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
          border-radius: 8px; margin: 1rem 0; padding: .6rem; }
.famhead { font-weight: 600; margin-bottom: .4rem; }
.partition { border-top: 1px dashed color-mix(in srgb, currentColor 20%, transparent); padding: .3rem 0; }
.fmt { font-size: .8rem; text-transform: uppercase; opacity: .7; }
.file { display: flex; align-items: center; gap: .8rem; padding: .3rem 0; }
.file img { max-width: 128px; max-height: 96px; border-radius: 4px; }
.noimg { width: 128px; height: 96px; display: flex; align-items: center; justify-content: center;
         background: color-mix(in srgb, currentColor 10%, transparent); border-radius: 4px; font-size: .7rem; }
.fileicon { font-weight: 700; letter-spacing: .05em; opacity: .6; }
.meta code { font-size: .8rem; word-break: break-all; }
.meta small { display: block; opacity: .75; }
.ocr pre { font-size: .7rem; max-height: 200px; overflow-y: auto; margin: .3rem 0 0 0; }
.badge { border-radius: 4px; padding: 0 .4rem; font-size: .7rem; font-weight: 600; }
.badge.keep { background: #2e7d3233; color: #2e7d32; }
.badge.cloud { background: #1565c033; color: #1565c0; }
.badge.comp { background: #6a1b9a33; color: #8e24aa; }
.badge.warn { background: #e6510033; color: #e65100; }
.badge.info { background: #45455533; color: #78788c; }
.badge.clone { background: #ef6c0033; color: #ef6c00; }
#bar { position: sticky; top: 0; background: Canvas; border-bottom: 2px solid #2e7d32;
       padding: .8rem 0; display: flex; gap: 1rem; align-items: center; z-index: 5; }
button { font: inherit; padding: .4rem .9rem; border-radius: 6px; cursor: pointer; }
.pager { margin: .5rem 0; }
input.cand, input.keeper { width: 1.1rem; height: 1.1rem; }
"""

_JS_TEMPLATE = """
const SCAN_ID = %SCAN_ID%;
const CATEGORY = %CATEGORY%;
function fmtBytes(n) {
  const u = ['B','KB','MB','GB','TB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + ' ' + u[i];
}
function updateCounter() {
  const checked = [...document.querySelectorAll('input.cand:checked')];
  // data-reclaim, not data-size: a checked clone contributes 0 here even
  // though its own file size is nonzero — trashing it frees no space.
  const bytes = checked.reduce((s, cb) => s + (+cb.dataset.reclaim), 0);
  document.getElementById('count').textContent =
    checked.length + ' files selected — ' + fmtBytes(bytes) + ' reclaimable';
}
function clusterKey(el) {
  return el.dataset.family + '\\u0000' + el.dataset.format + '\\u0000' + el.dataset.cluster;
}
function exportSelection() {
  const keeperMap = new Map();
  document.querySelectorAll('input.keeper').forEach(k => keeperMap.set(clusterKey(k), k));
  const del = [], keep = new Map();
  document.querySelectorAll('input.cand:checked').forEach(cb => {
    del.push({ path: cb.dataset.path, size: +cb.dataset.size,
               blake2b: cb.dataset.hash, family: cb.dataset.family,
               format: cb.dataset.format, cluster: cb.dataset.cluster,
               companions: JSON.parse(cb.dataset.companions || '[]') });
    const k = keeperMap.get(clusterKey(cb));
    if (k) keep.set(k.dataset.path, { path: k.dataset.path, size: +k.dataset.size,
                                      blake2b: k.dataset.hash, family: k.dataset.family,
                                      format: k.dataset.format, cluster: k.dataset.cluster });
  });
  const payload = { schema_version: '1', scan_id: SCAN_ID,
                    exported_at: new Date().toISOString(),
                    delete: del, keep: [...keep.values()] };
  const blob = new Blob([JSON.stringify(payload, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'findupe-selection-' + SCAN_ID + '-' + CATEGORY + '.json';
  a.click();
}
function setAll(state) {
  // checking en masse must never reach flagged (burst/low-entropy) families;
  // unchecking is always allowed
  const sel = state ? 'input.cand:not([data-flagged])' : 'input.cand';
  document.querySelectorAll(sel).forEach(cb => { cb.checked = state; });
  updateCounter();
}
function paginate(sectionId, pageSize) {
  const sec = document.getElementById(sectionId);
  if (!sec) return;
  const fams = [...sec.querySelectorAll('.family')];
  if (fams.length <= pageSize) return;
  let page = 0;
  const pages = Math.ceil(fams.length / pageSize);
  const nav = document.createElement('div');
  nav.className = 'pager';
  const label = document.createElement('span');
  const prev = document.createElement('button'); prev.textContent = '← Prev';
  const next = document.createElement('button'); next.textContent = 'Next →';
  function show() {
    fams.forEach((f, i) => {
      f.style.display = (i >= page * pageSize && i < (page + 1) * pageSize) ? '' : 'none';
    });
    label.textContent = ' page ' + (page + 1) + '/' + pages + ' ';
  }
  prev.onclick = () => { if (page > 0) { page--; show(); } };
  next.onclick = () => { if (page < pages - 1) { page++; show(); } };
  nav.append(prev, label, next);
  sec.prepend(nav);
  show();
}
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('input.cand').forEach(cb => cb.addEventListener('change', updateCounter));
  ['exact-sec', 'visual-sec', 'possible-sec'].forEach(id => paginate(id, 50));
  updateCounter();
});
"""


def category_output_paths(base: Path) -> tuple[Path, Path]:
    """report.html -> (report-images.html, report-other.html)."""
    return (base.with_stem(base.stem + "-images"), base.with_stem(base.stem + "-other"))


def _is_image_family(fam: Family) -> bool:
    return any(rec.is_image for p in fam.partitions for rec in p.files)


def _write_report(
    scan: ScanResult,
    families: list[Family],
    possible: list[Family],
    out_path: Path,
    category: str,
    thumb: Thumbnailer = thumbnail_b64,
) -> None:
    exact = [f for f in families if f.kind == "exact"]
    visual = [f for f in families if f.kind == "visual"]
    total_surplus = sum(f.surplus_count for f in families)
    total_bytes = sum(f.surplus_bytes for f in families)
    sel_name = f"findupe-selection-{scan.scan_id}-{category}.json"

    def section(sec_id: str, title: str, fams: list[Family], checkable: bool, hint: str) -> str:
        if not fams:
            return ""
        body = "".join(_family_html(f, thumb, checkable) for f in fams)
        return f'<section id="{sec_id}"><h2>{title} ({len(fams)})</h2><p>{hint}</p>{body}</section>'

    empty_note = (
        f"<p>No duplicate {html.escape(category)} found in this scan.</p>"
        if not exact and not visual and not possible else ""
    )
    js = (
        _JS_TEMPLATE
        .replace("%SCAN_ID%", json.dumps(scan.scan_id))
        .replace("%CATEGORY%", json.dumps(category))
    )

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>findupe — {html.escape(category)} — {html.escape(scan.scan_id)}</title>
<style>{_CSS}</style>
<script>{js}</script>
</head><body>
<h1>findupe report — {html.escape(category)}</h1>
<p>scan <code>{html.escape(scan.scan_id)}</code> — roots: {", ".join(f"<code>{html.escape(str(r))}</code>" for r in scan.roots)}<br>
{len(families)} duplicate families · {total_surplus} surplus files · {_fmt_bytes(total_bytes)} reclaimable (flagged) if all suggestions are accepted and the Trash is emptied</p>
<div id="bar">
  <strong id="count"></strong>
  <button onclick="setAll(true)">Check all suggested</button>
  <button onclick="setAll(false)">Uncheck all</button>
  <button onclick="exportSelection()" style="font-weight:700">⬇ Export selection</button>
</div>
<p>Review below, then export your selection and run
<code>findupe apply {sel_name}</code>.
Files go to the macOS <b>Trash</b> (recoverable), never deleted directly — this only
frees space once you empty the Trash.
Note: files marked <span class="badge clone">⧉ clone — 0 B freed</span> share physical
storage with their keeper (an APFS clone) — trashing them frees no space, so they're
excluded from the reclaimable total above (though still checkable, if you want them
gone for organizational reasons). This detection isn't foolproof: on a volume or setup
where it can't run, an undetected clone still reclaims no space when trashed, same as
before — this note is the fallback for that case. Also: if two of your OWN checked
candidates turn out to be clones of <em>each other</em> rather than of the keeper,
their shared bytes aren't excluded above (only clone-of-keeper is checked) — trashing
just one won't free that shared space either.</p>
{empty_note}
{section("exact-sec", "Exact duplicates", exact, True,
         "Byte-identical files. The suggested keeper is pre-selected to survive; checked copies go to the Trash.")}
{section("visual-sec", "Same image, multiple versions", visual, True,
         "Perceptually identical (re-encodes, exports, format conversions). Keeping one per format is intentional — "
         "cross-format siblings are shown together but never suggested for deletion. Flagged families are never pre-checked: review them carefully.")}
{section("possible-sec", "Possible matches — review only", possible, False,
         "Visually similar but NOT confirmed duplicates (bursts, brackets, similar shots). Shown for your eyes only; "
         "this tool will not delete these. Handle them manually in Finder if you decide they are duplicates.")}
<h2>Notes</h2>
{_notes_html(scan)}
</body></html>"""
    out_path.write_text(doc, encoding="utf-8")


def generate_reports(
    scan: ScanResult,
    possible: list[Family],
    base_out_path: Path,
    thumb: Thumbnailer = thumbnail_b64,
) -> tuple[Path, Path]:
    """Render one scan into two category-scoped reports: images and other.

    Classification is per-FAMILY, not per-cluster: a family is "images" if
    ANY of its records is an image. For the overwhelming majority of families
    this is also candidate-homogeneous, since surplus lives only within
    per-format clusters and cross-format membership is otherwise
    informational-only. The one edge case where it is not: if byte-identical
    content exists under both an image and a non-image extension (e.g. a
    ".jpg" and a ".bak" sharing one BLAKE2b hash), the whole family —
    including a real non-image surplus candidate — renders in the images
    report. This is one-directional (an image record always forces the
    images bucket; a non-image candidate can never leak the other way) and
    fails safe: the candidate still appears in exactly one report and is
    still re-verified at apply, so the only consequence is a reviewer who
    only opens report-other.html could miss it (under-deletion, never an
    unwanted deletion). `possible` is images-only by construction (perceptual
    hashing never runs on non-images), so it always renders in the images
    report.
    """
    img_families = [f for f in scan.families if _is_image_family(f)]
    other_families = [f for f in scan.families if not _is_image_family(f)]
    img_path, other_path = category_output_paths(base_out_path)
    _write_report(scan, img_families, possible, img_path, "images", thumb)
    _write_report(scan, other_families, [], other_path, "other", thumb)
    return img_path, other_path
