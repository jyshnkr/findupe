import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from dupefinder.discover import discover
from dupefinder.imaging import compute_perceptual, hamming, load_image, thumbnail_b64
from conftest import gradient_image

REAL_CR3 = Path("/Users/jayashankarmangina/Documents/DCIMZ_2/ARJUN3/JSCL2022.CR3")


def save(img: Image.Image, path: Path, fmt: str, **kw) -> None:
    img.save(path, fmt, **kw)


def perceptual_records(root):
    recs = discover([root]).records
    compute_perceptual(recs, workers=0)
    return {r.path.name: r for r in recs}


def test_cross_format_same_image_hashes_match(tmp_path):
    img = gradient_image()
    save(img, tmp_path / "a.jpg", "JPEG", quality=90)
    save(img, tmp_path / "a.png", "PNG")
    recs = perceptual_records(tmp_path)
    assert hamming(recs["a.jpg"].phash, recs["a.png"].phash) <= 2
    assert hamming(recs["a.jpg"].dhash, recs["a.png"].dhash) <= 2


def test_heic_jpeg_pair_matches(tmp_path):
    img = gradient_image()
    save(img, tmp_path / "x.heic", "HEIF", quality=80)
    save(img, tmp_path / "x.jpg", "JPEG", quality=90)
    recs = perceptual_records(tmp_path)
    assert hamming(recs["x.heic"].phash, recs["x.jpg"].phash) <= 2


def test_rotated_copy_matches_original(tmp_path):
    img = gradient_image()
    save(img, tmp_path / "orig.jpg", "JPEG", quality=90)
    # store pixels rotated 90° CCW with EXIF Orientation=6 ("rotate 90 CW to view")
    rotated = img.rotate(90, expand=True)
    exif = Image.Exif()
    exif[274] = 6
    save(rotated, tmp_path / "rot.jpg", "JPEG", quality=90, exif=exif)
    recs = perceptual_records(tmp_path)
    assert hamming(recs["orig.jpg"].phash, recs["rot.jpg"].phash) <= 2


def test_different_images_are_far(tmp_path):
    save(gradient_image(), tmp_path / "grad.jpg", "JPEG")
    noise = Image.effect_noise((256, 256), 64).convert("RGB")
    save(noise, tmp_path / "noise.jpg", "JPEG")
    recs = perceptual_records(tmp_path)
    assert hamming(recs["grad.jpg"].phash, recs["noise.jpg"].phash) > 8


def test_corrupt_image_sets_error_not_crash(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"not really a jpeg")
    (tmp_path / "fake.cr3").write_bytes(b"not a raw file either")
    recs = perceptual_records(tmp_path)
    assert recs["broken.jpg"].hash_error and "decode" in recs["broken.jpg"].hash_error
    assert recs["fake.cr3"].hash_error


def test_capture_key_from_exif(tmp_path):
    img = gradient_image()
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = "2026:07:09 12:00:00"
    ifd[0x8827] = 400
    ifd[0x9291] = "42"  # SubSecTimeOriginal
    save(img, tmp_path / "shot.jpg", "JPEG", exif=exif)
    from dupefinder.imaging import capture_key
    key, subsec = capture_key(load_image(tmp_path / "shot.jpg"))
    assert key is not None and key.startswith("2026:07:09 12:00:00")
    assert "400" in key
    assert subsec == "42"


def test_thumbnail_b64(tmp_path):
    save(gradient_image(), tmp_path / "t.jpg", "JPEG")
    b64 = thumbnail_b64(tmp_path / "t.jpg", max_px=64)
    thumb = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(thumb.size) <= 64
    assert thumbnail_b64(tmp_path / "missing.jpg") is None


def test_process_pool_path(tmp_path):
    img = gradient_image()
    save(img, tmp_path / "p1.jpg", "JPEG")
    save(img, tmp_path / "p2.png", "PNG")
    recs = discover([tmp_path]).records
    compute_perceptual(recs, workers=2)  # exercises the real ProcessPool branch
    assert all(r.phash is not None for r in recs)


@pytest.mark.skipif(not REAL_CR3.exists(), reason="real CR3 sample not available")
def test_real_cr3_preview_and_capture_key():
    img = load_image(REAL_CR3)
    assert img.width > 1000
    from dupefinder.imaging import capture_key
    key, _subsec = capture_key(img)
    assert key  # Canon embeds shot EXIF in the preview (LibRaw-rewritten header)
