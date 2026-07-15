from findupe.ledger import ScanRecord
from findupe.stats import Totals


def _rec(scan_id, surplus_count=5, surplus_bytes=1000):
    return ScanRecord(
        scan_id=scan_id, created_at=f"{scan_id}T00:00:00+00:00", roots=["/p"],
        duplicate_families=1, possible_matches=0, surplus_count=surplus_count,
        surplus_bytes=surplus_bytes, categories={}, problems={},
    )


def test_dashboard_html_contains_total_figures():
    from findupe.dashboard import render_dashboard_html

    totals = Totals(scans_recorded=2, applies=1, files_trashed_net=3,
                     bytes_reclaimed_net=204800, files_restored=0, files_failed=0,
                     duplicates_found_total=9)
    html = render_dashboard_html([], totals, set(), [], [])

    assert "200.0 KB" in html  # _fmt_bytes(204800)
    assert "3" in html  # files_trashed_net


def test_dashboard_html_labels_trash_moves_not_reclaimed_space():
    """Same honesty requirement as the terminal stats text: 'moved to Trash',
    never 'reclaimed', for bytes that have only been moved, not freed."""
    from findupe.dashboard import render_dashboard_html

    totals = Totals(scans_recorded=2, applies=1, files_trashed_net=3,
                     bytes_reclaimed_net=204800, files_restored=0, files_failed=0,
                     duplicates_found_total=9)
    html = render_dashboard_html([], totals, set(), [], [])

    assert "moved to Trash" in html
    assert "reclaimed (net of restores)" not in html  # the old, dishonest label
    assert "reclaimable (flagged)" in html
    assert "empty the Trash" in html


def test_dashboard_html_has_svg_charts():
    from findupe.dashboard import render_dashboard_html

    totals = Totals()
    reclaimed = [("2026-07-01", 1000), ("2026-07-02", 2000)]
    dup = [("2026-07-01", 5), ("2026-07-02", 8)]
    html = render_dashboard_html([], totals, set(), reclaimed, dup)

    assert html.count("<svg") >= 2  # one chart per series (different units/scales)
    assert "<polyline" in html


def test_dashboard_html_is_self_contained():
    from findupe.dashboard import render_dashboard_html

    html = render_dashboard_html([], Totals(), set(), [], [])

    assert "http://" not in html and "https://" not in html
    assert "<script src=" not in html
    assert "color-scheme" in html and "color-mix" in html


def test_dashboard_single_point_shows_placeholder_not_pinned_dot():
    """A lone data point isn't a trend — the first real-world use of `stats
    --html` will often have exactly 1 scan (forward-only ledger), and a
    single-point line chart always renders pinned to the axis max
    regardless of the actual value, which reads as broken/misleading."""
    from findupe.dashboard import render_dashboard_html

    html = render_dashboard_html([], Totals(), set(), [("2026-07-01", 1000)], [])

    assert "<svg" not in html
    assert "check back" in html or "not enough" in html


def test_dashboard_history_table_shows_applied_badge():
    from findupe.dashboard import render_dashboard_html

    records = [_rec("20260712-100000"), _rec("20260712-110000")]
    html = render_dashboard_html(records, Totals(), {"20260712-100000"}, [], [])

    assert html.count("applied") >= 1
    assert "not applied" in html
