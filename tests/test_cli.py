from pathlib import Path

from dupefinder.cli import _collect_hash_errors
from test_grouping import mk


def test_collect_hash_errors_dedups_shared_companion():
    """A sidecar shared by two primaries in the same family (e.g. one XMP
    attached to both a RAW and its JPEG sibling — discover._attach_companions
    appends the SAME record to every primary's .companions) must be
    counted/reported once, not once per primary that references it."""
    primary_a = mk("/p/IMG_1.CR3")
    primary_b = mk("/p/IMG_1.jpg")
    sidecar = mk("/p/IMG_1.xmp")
    sidecar.hash_error = "full hash: [Errno 2] No such file or directory"
    companions = [sidecar, sidecar]  # mirrors the real duplication

    result = _collect_hash_errors([primary_a, primary_b], companions)

    assert result == [(Path("/p/IMG_1.xmp"), sidecar.hash_error)]
