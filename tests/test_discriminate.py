"""OCR-based screenshot-text demotion tests. Verifies the safety model:
OCR can only demote a match (strong→possible/none), never promote."""

from pathlib import Path

from findupe.grouping import build_families
from findupe.models import FileRecord
from findupe.ocr import OcrResult


class FakeOcrBackend:
    """Fake OCR backend for testing. Tracks calls and returns configurable results."""
    def __init__(self, texts: dict[str, str], confidence: float = 0.9) -> None:
        self.texts = texts  # {path_str: text}
        self.confidence = confidence
        self.calls: list[str] = []

    def recognize_text(self, path: Path) -> OcrResult:
        self.calls.append(str(path))
        text = self.texts.get(str(path), "")
        return OcrResult(text, self.confidence, len(text.split()))


def mk(
    path: str,
    size: int = 1000,
    mtime: int = 1_000_000,
    phash: int | None = None,
    dhash: int | None = None,
    capture_key: str | None = None,
    capture_subsec: str | None = None,
    exact_hash: str | None = None,
    width: int | None = None,
    height: int | None = None,
    has_camera_exif: bool = False,
) -> FileRecord:
    """Construct a test FileRecord. Reuses mk() style from test_grouping.py."""
    rec = FileRecord(
        path=Path(path), size=size, mtime_ns=mtime, dev=1, inode=hash(path) & 0xFFFF,
        volume="/",
    )
    rec.phash, rec.dhash = phash, dhash
    rec.capture_key, rec.capture_subsec = capture_key, capture_subsec
    rec.exact_hash = exact_hash
    rec.width, rec.height = width, height
    rec.has_camera_exif = has_camera_exif
    return rec


PH = 0x0123_4567_89AB_CDEF  # arbitrary base hash


def fake_is_screenshot(rec: FileRecord) -> bool:
    """Simple screenshot gate for testing: must be PNG with dimensions >= 200."""
    if rec.format != "png":
        return False
    if rec.has_camera_exif:
        return False
    if not rec.width or not rec.height:
        return False
    return rec.width >= 200 and rec.height >= 200


def test_confident_different_text_breaks_edge():
    """Scenario 1: Strong pHash/dHash match, but OCR texts with NO shared words
    (confidence high, similarity low) → edge is broken entirely (no families, no possible)."""
    a = mk(
        "/ss/shot1.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )
    b = mk(
        "/ss/shot2.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )

    fake = FakeOcrBackend({
        str(a.path): "apple banana cherry date",
        str(b.path): "whiskey xray yankee zulu",
    })

    families, possible = build_families([a, b], {}, ocr_backend=fake, is_screenshot=fake_is_screenshot)

    # Edge is fully broken by confident-different OCR
    assert families == []
    assert possible == []
    # Verify OCR was called (proves the gate ran)
    assert len(fake.calls) == 2


def test_uncertain_similarity_downgrades_to_possible_with_flag():
    """Scenario 2: Strong pHash/dHash match, OCR texts share SOME but not
    enough words (SIM_LOW < similarity < SIM_HIGH) → lands in possible with "text-differs" flag."""
    a = mk(
        "/ss/shot1.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )
    b = mk(
        "/ss/shot2.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )

    # Texts with moderate overlap: "apple banana cherry" vs "apple banana dog"
    # Jaccard: shared {apple, banana} / union {apple, banana, cherry, dog} = 2/4 = 0.5
    # This is between SIM_LOW (0.25) and SIM_HIGH (0.6), so "uncertain"
    fake = FakeOcrBackend({
        str(a.path): "apple banana cherry desert",
        str(b.path): "apple banana dog eagle",
    })

    families, possible = build_families([a, b], {}, ocr_backend=fake, is_screenshot=fake_is_screenshot)

    # Pair is downgraded to possible
    assert families == []
    assert len(possible) == 1
    # Check that "text-differs" flag is present
    assert "text-differs" in possible[0].flags


def test_confident_similar_text_stays_strong():
    """Scenario 3: Strong pHash/dHash match, OCR texts are near-identical
    (similarity >= SIM_HIGH) → pair stays in families (strong), no demotion."""
    a = mk(
        "/ss/shot1.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )
    b = mk(
        "/ss/shot2.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )

    # Texts with high overlap: "apple banana cherry" vs "apple banana cherry"
    # Jaccard: 3/3 = 1.0, well above SIM_HIGH (0.6)
    fake = FakeOcrBackend({
        str(a.path): "apple banana cherry desert",
        str(b.path): "apple banana cherry desert",
    })

    families, possible = build_families([a, b], {}, ocr_backend=fake, is_screenshot=fake_is_screenshot)

    # Pair stays strong
    assert len(families) == 1
    assert possible == []


def test_sparse_text_downgrades_to_possible_with_flag():
    """Scenario 4: One or both sides have sparse text (< MIN_WORDS=3) → lands in
    possible with "text-differs" (the "uncertain" tier), regardless of how
    identical the sparse text is."""
    a = mk(
        "/ss/shot1.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )
    b = mk(
        "/ss/shot2.png", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=False,
    )

    # a has only 2 words (sparse), b has enough words
    fake = FakeOcrBackend({
        str(a.path): "apple banana",  # only 2 words, below MIN_WORDS=3
        str(b.path): "apple banana cherry desert",  # enough words, high confidence
    })

    families, possible = build_families([a, b], {}, ocr_backend=fake, is_screenshot=fake_is_screenshot)

    # Sparse text → uncertain → possible with "text-differs"
    assert families == []
    assert len(possible) == 1
    assert "text-differs" in possible[0].flags


def test_non_screenshot_pair_never_calls_ocr():
    """Scenario 5: A non-screenshot pair (real photos or wrong format) with strong
    pHash match stays strong, and the OCR backend is NEVER invoked at all.
    This is the test that proves the gate actually gates."""
    a = mk(
        "/pics/photo1.jpg", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=True,  # has camera exif → not a screenshot
    )
    b = mk(
        "/pics/photo2.jpg", phash=PH, dhash=PH, width=800, height=600,
        has_camera_exif=True,
    )

    fake = FakeOcrBackend({
        str(a.path): "text that would demote",
        str(b.path): "completely different text",
    })

    families, possible = build_families([a, b], {}, ocr_backend=fake, is_screenshot=fake_is_screenshot)

    # Strong edge is preserved
    assert len(families) == 1
    assert possible == []
    # CRITICAL: OCR was never called
    assert fake.calls == []


def test_default_behavior_unchanged():
    """Scenario 6: Calling build_families(records, exact_groups) with NO
    ocr_backend/is_screenshot args (the default) behaves identically to before
    this feature — reuse an existing scenario from test_grouping.py."""
    # Reuse the "same_subsec_reencode_stays_strong" scenario: a real dup that
    # should stay strong without any OCR involvement
    a = mk("/p/X.jpg", phash=PH, dhash=PH, capture_key="t|1", capture_subsec="75")
    b = mk("/p/X copy.jpg", phash=PH, dhash=PH, capture_key="t|1", capture_subsec="75")

    # Call with NO ocr_backend or is_screenshot args (the original signature)
    families, _ = build_families([a, b], {})

    assert len(families) == 1
    assert families[0].surplus_count == 1
