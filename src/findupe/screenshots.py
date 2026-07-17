"""Metadata-only screenshot heuristic — deliberately simple. Over-triggering
(OCR runs on a non-screenshot PNG) only costs a wasted OCR call; the demotion
rule downstream still requires disagreeing text to act, so a false positive
here is never unsafe, just a no-op. Under-triggering (a real screenshot pair
never gets OCR'd) just means today's pHash-only behavior, also safe."""

from __future__ import annotations

from .models import FileRecord

MIN_SCREENSHOT_DIM = 200  # below this, PNGs are icons/emoji/graphics, not screen captures


def is_screenshot(rec: FileRecord) -> bool:
    if rec.format != "png":
        return False
    if rec.has_camera_exif:
        return False
    if not rec.width or not rec.height:
        return False
    return rec.width >= MIN_SCREENSHOT_DIM and rec.height >= MIN_SCREENSHOT_DIM
