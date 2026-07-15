"""Self-contained HTML dashboard: all-time totals + a per-scan history table
+ two single-series trend charts (space reclaimed, duplicates found).

Two SEPARATE charts, not one two-series chart: bytes-reclaimed and
duplicates-found are different units and different scales. Sharing one
y-axis (or worse, a dual-axis chart) would misrepresent one series relative
to the other — the classic "two measures, one axis" chart mistake. Each
series gets its own single-axis chart instead (small multiples).

No external CSS/JS/CDN, same as report.py — `color-scheme: light dark` +
`color-mix(in srgb, currentColor N%, transparent)` for theme adaptation,
reusing report.py's exact accent green so the dashboard reads as the same
tool, not a bolt-on. A single series needs no legend (the chart title names
it); hover-on-point uses a native SVG <title> tooltip rather than a custom
crosshair/JS layer — proportionate to a handful of data points in a
personal-scale tool, not a dense analytics surface.
"""

from __future__ import annotations

import html

from .ledger import ScanRecord
from .report import _fmt_bytes
from .stats import Totals

_ACCENT = "#2e7d32"  # matches report.py's #bar border / .badge.keep

_CSS = """
:root { color-scheme: light dark; font-family: -apple-system, system-ui, sans-serif; }
body { margin: 2rem auto; max-width: 70rem; padding: 0 1rem; }
h1, h2 { margin-bottom: .3rem; }
.totals { display: flex; flex-wrap: wrap; gap: 1.5rem; margin: 1rem 0 2rem; }
.stat { border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
        border-radius: 8px; padding: .6rem 1rem; min-width: 9rem; }
.stat .n { font-size: 1.4rem; font-weight: 700; display: block; }
.stat .l { font-size: .75rem; opacity: .7; }
.stat .sub { font-size: .7rem; opacity: .55; display: block; }
.chart { margin: 1.5rem 0; }
.chart svg { max-width: 100%; height: auto; }
.axis { stroke: color-mix(in srgb, currentColor 25%, transparent); stroke-width: 1; }
.gridline { stroke: color-mix(in srgb, currentColor 12%, transparent); stroke-width: 1; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { text-align: left; padding: .3rem .6rem; border-bottom: 1px solid
         color-mix(in srgb, currentColor 15%, transparent); font-size: .9rem; }
.badge { border-radius: 4px; padding: 0 .4rem; font-size: .7rem; font-weight: 600; }
.badge.applied { background: #2e7d3233; color: #2e7d32; }
.badge.notapplied { background: #45455533; color: #78788c; }
.caveat { font-size: .8rem; opacity: .7; }
"""


def _svg_line_chart(points: list[tuple[str, int]], title: str,
                     value_fmt=str, width: int = 640, height: int = 180) -> str:
    """One series, one axis. Thin 2px polyline, >=8px hit circles with a
    native <title> hover tooltip, rounded data-end, recessive gridlines.

    A single point isn't a trend — it always computes to the axis max
    regardless of its actual value, which reads as broken/misleading rather
    than informative. Since the ledger is forward-only, the very first
    `stats --html` a user runs will typically have exactly 0 or 1 scans, so
    this is the *common* first impression, not a rare edge case."""
    if len(points) < 2:
        msg = "no data yet" if not points else "only 1 scan so far — check back after your next scan for a trend"
        return f'<div class="chart"><h2>{html.escape(title)}</h2><p class="caveat">{msg}</p></div>'

    pad_l, pad_r, pad_t, pad_b = 50, 20, 20, 30
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    values = [v for _, v in points]
    vmax = max(values) or 1

    def x_of(i: int) -> float:
        return pad_l + (i / max(len(points) - 1, 1)) * plot_w

    def y_of(v: int) -> float:
        return pad_t + plot_h - (v / vmax) * plot_h

    poly_points = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, (_, v) in enumerate(points))
    circles = "".join(
        f'<circle cx="{x_of(i):.1f}" cy="{y_of(v):.1f}" r="4" fill="{_ACCENT}">'
        f"<title>{html.escape(d)}: {html.escape(value_fmt(v))}</title></circle>"
        for i, (d, v) in enumerate(points)
    )
    # a handful of x-axis date labels (first, last, and up to 3 in between)
    label_idxs = sorted(set([0, len(points) - 1] + list(range(0, len(points), max(len(points) // 4, 1)))))
    labels = "".join(
        f'<text x="{x_of(i):.1f}" y="{height - 8}" font-size="10" text-anchor="middle" '
        f'fill="currentColor" opacity=".7">{html.escape(points[i][0])}</text>'
        for i in label_idxs
    )

    svg = f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)} trend chart">
  <line class="axis" x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}"/>
  <line class="axis" x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}"/>
  <polyline points="{poly_points}" fill="none" stroke="{_ACCENT}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"/>
  {circles}
  {labels}
</svg>"""
    return f'<div class="chart"><h2>{html.escape(title)}</h2>{svg}</div>'


def render_dashboard_html(
    records: list[ScanRecord],
    totals: Totals,
    applied_ids: set[str],
    reclaimed_series: list[tuple[str, int]],
    dup_series: list[tuple[str, int]],
) -> str:
    stats_html = f"""
<div class="totals">
  <div class="stat"><span class="n">{totals.scans_recorded}</span><span class="l">scans recorded</span></div>
  <div class="stat"><span class="n">{totals.files_trashed_net}</span><span class="l">files currently trashed</span></div>
  <div class="stat"><span class="n">{html.escape(_fmt_bytes(totals.bytes_reclaimed_net))}</span><span class="l">moved to Trash (net of restores)</span><span class="sub">frees space once you empty the Trash</span></div>
  <div class="stat"><span class="n">{totals.duplicates_found_total}</span><span class="l">surplus files flagged across {totals.scans_recorded} scans</span></div>
</div>
<p class="caveat">"Flagged" totals are cumulative across scans, not deduplicated — a
still-unresolved duplicate found again in a later scan counts again.</p>
"""

    reclaimed_chart = _svg_line_chart(reclaimed_series, "Space moved to Trash over time", _fmt_bytes)
    dup_chart = _svg_line_chart(dup_series, "Duplicates found per scan (not reclaimed)", str)

    rows = []
    for r in records:
        tag = "applied" if r.scan_id in applied_ids else "not applied"
        badge_class = "applied" if tag == "applied" else "notapplied"
        rows.append(
            f"<tr><td>{html.escape(r.scan_id)}</td><td>{r.duplicate_families}</td>"
            f"<td>{html.escape(_fmt_bytes(r.surplus_bytes))}</td>"
            f'<td><span class="badge {badge_class}">{tag}</span></td></tr>'
        )
    table_html = (
        "<table><thead><tr><th>scan</th><th>families</th><th>reclaimable (flagged)</th>"
        "<th>status</th></tr></thead><tbody>"
        + ("".join(rows) if rows else '<tr><td colspan="4">no archived scans</td></tr>')
        + "</tbody></table>"
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>findupe dashboard</title>
<style>{_CSS}</style></head>
<body>
<h1>findupe — history dashboard</h1>
{stats_html}
{reclaimed_chart}
{dup_chart}
<h2>Scan history</h2>
{table_html}
<p class="caveat">"Reclaimable (flagged)" means findupe found a surplus copy — it only
frees space once you delete it <em>and</em> empty the Trash. findupe moves files to
the Trash; it does not measure disk space actually freed, and can't: APFS clones are
indistinguishable from true copies without deep extent inspection, so trashing a clone
reclaims no space even after the Trash is emptied.</p>
</body></html>"""
