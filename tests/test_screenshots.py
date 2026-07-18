"""Tests for screenshot detection predicate."""

from pathlib import Path

from findupe.models import FileRecord
from findupe.screenshots import is_screenshot


def mk(
    path: str,
    size: int = 1000,
    mtime: int = 1_000_000,
    width: int | None = None,
    height: int | None = None,
) -> FileRecord:
    """Create a FileRecord for testing."""
    rec = FileRecord(
        path=Path(path), size=size, mtime_ns=mtime, dev=1, inode=hash(path) & 0xFFFF,
        volume="/",
    )
    rec.width, rec.height = width, height
    return rec


def test_png_no_exif_big_enough_is_screenshot():
    """PNG, no camera EXIF, 800×600 → True."""
    rec = mk("/test/screenshot.png", width=800, height=600)
    assert rec.has_camera_exif is None  # verify default
    assert is_screenshot(rec) is True


def test_png_with_camera_exif_is_not_screenshot():
    """PNG, camera EXIF present, 800×600 → False."""
    rec = mk("/test/photo.png", width=800, height=600)
    rec.has_camera_exif = True
    assert is_screenshot(rec) is False


def test_png_too_small_is_not_screenshot():
    """PNG, no camera EXIF, 50×50 → False (too small — icon/emoji)."""
    rec = mk("/test/icon.png", width=50, height=50)
    assert rec.has_camera_exif is None
    assert is_screenshot(rec) is False


def test_jpeg_no_exif_big_enough_is_not_screenshot():
    """JPEG, no camera EXIF, 800×600 → False (wrong format)."""
    rec = mk("/test/image.jpg", width=800, height=600)
    assert rec.has_camera_exif is None
    assert is_screenshot(rec) is False


def test_png_missing_dimensions_is_not_screenshot():
    """PNG, no camera EXIF, width or height None → False."""
    rec = mk("/test/screenshot.png", width=800, height=None)
    assert rec.has_camera_exif is None
    assert is_screenshot(rec) is False
