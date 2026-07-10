"""Image decoding and perceptual fingerprinting.

Every image is EXIF-orientation-normalized before hashing (a rotated copy of the
same photo must hash identically). RAW files are fingerprinted via their embedded
JPEG preview (rawpy, exiftool fallback) plus a capture_key of shot metadata —
the grouping guard that stops two different captures with similar previews from
ever being called duplicates.

Decode is CPU-bound, so compute_perceptual() fans out to processes; workers=0
runs inline (tests, small scans). SQLite stays on the main thread.
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import imagehash
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .cache import Cache
from .models import RAW_EXTS, FileRecord

register_heif_opener()

Image.MAX_IMAGE_PIXELS = 500_000_000  # photographs, not decompression bombs — but keep a lid

_EXIF_DT_ORIGINAL = 0x9003
_TIFF_DATETIME = 0x0132  # RAW previews (LibRaw-rewritten) store capture time here
_EXIF_EXPOSURE = 0x829A
_EXIF_FNUMBER = 0x829D
_EXIF_ISO = 0x8827
_EXIF_FOCAL = 0x920A
_EXIF_IFD = 0x8769


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _raw_preview_bytes(path: Path) -> bytes:
    """Embedded JPEG preview from a RAW file: rawpy first, exiftool fallback."""
    try:
        import rawpy

        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            return thumb.data
        buf = io.BytesIO()
        Image.fromarray(thumb.data).save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:
        exiftool = shutil.which("exiftool")
        if exiftool:
            out = subprocess.run(
                [exiftool, "-b", "-PreviewImage", str(path)],
                capture_output=True, timeout=60,
            )
            if out.returncode == 0 and out.stdout:
                return out.stdout
        raise


def load_image(path: Path) -> Image.Image:
    """Open any supported image, orientation-normalized. Raises on failure."""
    if path.suffix.lower() in RAW_EXTS:
        img = Image.open(io.BytesIO(_raw_preview_bytes(path)))
    else:
        img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img.load()
    return img


def capture_key(img: Image.Image) -> str | None:
    """Shot fingerprint: capture time + exposure params. None if EXIF absent."""
    try:
        exif = img.getexif()
        ifd = exif.get_ifd(_EXIF_IFD)
        dt = ifd.get(_EXIF_DT_ORIGINAL) or exif.get(_TIFF_DATETIME)
        if not dt:
            return None
        parts = [str(dt)] + [
            str(ifd.get(tag, "")) for tag in (_EXIF_EXPOSURE, _EXIF_FNUMBER, _EXIF_ISO, _EXIF_FOCAL)
        ]
        return "|".join(parts)
    except Exception:
        return None


def _perceptual_worker(path_str: str) -> dict:
    """Runs in a worker process; returns plain picklable values only."""
    path = Path(path_str)
    try:
        img = load_image(path)
        return {
            "path": path_str,
            "phash": int(str(imagehash.phash(img)), 16),
            "dhash": int(str(imagehash.dhash(img)), 16),
            "width": img.width,
            "height": img.height,
            "capture_key": capture_key(img) if path.suffix.lower() in RAW_EXTS else None,
        }
    except Exception as e:  # decode failures are per-file findings, never fatal
        return {"path": path_str, "error": f"{type(e).__name__}: {e}"}


def compute_perceptual(
    records: list[FileRecord],
    cache: Cache | None = None,
    workers: int = 4,
) -> None:
    """Fill phash/dhash/width/height/capture_key on every image record, in place."""
    images = [r for r in records if r.is_image and r.hash_error is None]

    todo: list[FileRecord] = []
    for rec in images:
        cached = cache.lookup(rec) if cache is not None else None
        if cached is not None and cached.phash is not None:
            rec.phash, rec.dhash = cached.phash, cached.dhash
            rec.width, rec.height = cached.width, cached.height
            rec.capture_key = cached.capture_key
        else:
            todo.append(rec)

    by_path = {str(r.path): r for r in todo}
    if workers > 0 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_perceptual_worker, by_path, chunksize=8))
    else:
        results = [_perceptual_worker(p) for p in by_path]

    done: list[FileRecord] = []
    for res in results:
        rec = by_path[res["path"]]
        if "error" in res:
            rec.hash_error = f"decode: {res['error']}"
            continue
        rec.phash, rec.dhash = res["phash"], res["dhash"]
        rec.width, rec.height = res["width"], res["height"]
        rec.capture_key = res["capture_key"]
        done.append(rec)

    if cache is not None and done:
        cache.store(done)


def thumbnail_b64(path: Path, max_px: int = 256) -> str | None:
    """Small base64 JPEG for the HTML report; None if the file can't be decoded."""
    try:
        img = load_image(path)
        img.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None
