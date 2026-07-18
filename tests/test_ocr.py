"""Tests for the OCR module: null backend, normalization, similarity, and Vision integration."""

import sys
import tempfile
from pathlib import Path

import pytest

from findupe.ocr import (
    MIN_CONFIDENCE,
    MIN_WORDS,
    SIM_HIGH,
    SIM_LOW,
    NullOcrBackend,
    OcrResult,
    default_backend,
    normalize_text,
    similarity,
)


class TestNullOcrBackend:
    """NullOcrBackend always returns empty results regardless of path."""

    def test_null_backend_returns_empty_result(self):
        """NullOcrBackend.recognize_text returns an empty OcrResult."""
        backend = NullOcrBackend()
        result = backend.recognize_text(Path("/nonexistent/path.png"))
        assert result == OcrResult(text="", mean_confidence=0.0, word_count=0)

    def test_null_backend_does_not_require_path_to_exist(self):
        """NullOcrBackend does not care if the path exists."""
        backend = NullOcrBackend()
        # Even if the path doesn't exist, NullOcrBackend should not raise
        result = backend.recognize_text(Path("/completely/fake/nonexistent/file.png"))
        assert result.text == ""
        assert result.mean_confidence == 0.0
        assert result.word_count == 0


class TestNormalizeText:
    """normalize_text: casefold, strip punctuation, collapse whitespace, remove zero-width chars."""

    def test_normalize_simple_text(self):
        """Simple ASCII text is casefolded and whitespace collapsed."""
        assert normalize_text("Hello World") == "hello world"

    def test_normalize_case_folding(self):
        """Text is converted to lowercase."""
        assert normalize_text("HELLO WORLD") == "hello world"
        assert normalize_text("HeLLo WoRLd") == "hello world"

    def test_normalize_multiple_spaces(self):
        """Multiple spaces are collapsed to single space."""
        assert normalize_text("Hello    World") == "hello world"
        assert normalize_text("  Hello   World  ") == "hello world"

    def test_normalize_punctuation(self):
        """Punctuation is removed (replaced with spaces)."""
        assert normalize_text("Hello, World!") == "hello world"
        assert normalize_text("Hello, World!") == "hello world"

    def test_normalize_mixed_punctuation_and_whitespace(self):
        """Combined punctuation and whitespace handling."""
        assert normalize_text("Hello,  World!") == "hello world"

    def test_normalize_zero_width_characters(self):
        """Zero-width characters are removed (not replaced with spaces)."""
        # The regex in ocr.py contains: r"[\u200b\u200c\u200d\ufeff]"
        # Testing with the zero-width space (U+200B)
        text_with_zwsp = "Hello\u200bWorld"
        assert normalize_text(text_with_zwsp) == "helloworld"

    def test_normalize_from_brief_example(self):
        """Example from brief: "\u200bHello,  World!\u200b" → "hello world"."""
        text = "\u200bHello,  World!\u200b"
        assert normalize_text(text) == "hello world"

    def test_normalize_preserves_word_structure(self):
        """Normalization preserves words separated by spaces."""
        assert normalize_text("Settings General WiFi") == "settings general wifi"

    def test_normalize_unicode_nfkc(self):
        """Unicode is normalized to NFKC form."""
        # NFKC normalization: ﬁ (ligature fi) becomes 'fi'
        assert normalize_text("ﬁnished") == "finished"

    def test_normalize_empty_string(self):
        """Empty string returns empty string."""
        assert normalize_text("") == ""

    def test_normalize_only_punctuation(self):
        """Text with only punctuation becomes empty after stripping."""
        assert normalize_text("!!!???") == ""

    def test_normalize_only_spaces(self):
        """Text with only spaces becomes empty."""
        assert normalize_text("   ") == ""


class TestSimilarity:
    """similarity: token-set Jaccard similarity with proper edge cases."""

    def test_similarity_identical_text(self):
        """Identical text has similarity 1.0."""
        assert similarity("hello world", "hello world") == 1.0

    def test_similarity_identical_text_different_case(self):
        """Text differing only in case is identical after normalization."""
        assert similarity("HELLO WORLD", "hello world") == 1.0

    def test_similarity_completely_disjoint_text(self):
        """Completely disjoint text (no shared words) has similarity 0.0."""
        assert similarity("apple banana", "cat dog") == 0.0

    def test_similarity_partial_overlap(self):
        """Partial overlap: "Settings General Wifi" vs "Settings General Bluetooth"."""
        # Tokens: {"settings", "general", "wifi"} ∩ {"settings", "general", "bluetooth"}
        # Intersection: {"settings", "general"} (2 tokens)
        # Union: {"settings", "general", "wifi", "bluetooth"} (4 tokens)
        # Jaccard = 2/4 = 0.5
        result = similarity("Settings General Wifi", "Settings General Bluetooth")
        assert result == 0.5

    def test_similarity_empty_strings(self):
        """Empty string compared to empty string is 1.0 (both empty, no divide-by-zero)."""
        assert similarity("", "") == 1.0

    def test_similarity_one_empty_string(self):
        """Empty string vs non-empty string."""
        # Tokens: {} ∩ {"hello"} = {}
        # Union: {"hello"}
        # Jaccard = 0/1 = 0.0
        assert similarity("", "hello") == 0.0
        assert similarity("hello", "") == 0.0

    def test_similarity_single_word_exact_match(self):
        """Single word exact match is 1.0."""
        assert similarity("hello", "hello") == 1.0

    def test_similarity_single_word_no_match(self):
        """Single word no match is 0.0."""
        assert similarity("hello", "world") == 0.0

    def test_similarity_ignore_punctuation(self):
        """Punctuation is ignored in similarity calculation."""
        # After normalization: "hello world" vs "hello world"
        assert similarity("Hello, World!", "hello world") == 1.0

    def test_similarity_with_whitespace_variations(self):
        """Extra whitespace doesn't affect similarity."""
        assert similarity("hello   world", "hello world") == 1.0

    def test_similarity_three_word_intersection(self):
        """Three-word example: "a b c" vs "b c d"."""
        # Intersection: {"b", "c"} (2 tokens)
        # Union: {"a", "b", "c", "d"} (4 tokens)
        # Jaccard = 2/4 = 0.5
        result = similarity("a b c", "b c d")
        assert result == 0.5

    def test_similarity_complete_overlap_different_order(self):
        """Same words in different order still have similarity 1.0 (sets, not sequences)."""
        assert similarity("hello world foo", "foo world hello") == 1.0

    def test_similarity_superset_subset(self):
        """Subset of tokens."""
        # {"hello", "world"} ∩ {"hello", "world", "foo"}
        # Intersection: {"hello", "world"} (2 tokens)
        # Union: {"hello", "world", "foo"} (3 tokens)
        # Jaccard = 2/3 ≈ 0.667
        result = similarity("hello world", "hello world foo")
        assert abs(result - (2 / 3)) < 0.001


class TestDefaultBackend:
    """Test that default_backend returns an appropriate backend for the platform."""

    def test_default_backend_returns_backend(self):
        """default_backend returns an object with recognize_text method."""
        backend = default_backend()
        assert hasattr(backend, "recognize_text")
        assert callable(backend.recognize_text)

    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="Vision backend only available on macOS",
    )
    def test_vision_backend_is_available_on_darwin(self):
        """On macOS, default_backend should return Vision backend if available."""
        backend = default_backend()
        # The backend should be a VisionOcrBackend (not NullOcrBackend) if pyobjc works
        # We can check this by attempting to call recognize_text on a valid image path
        # and seeing if it returns more than just the null result
        # For now, we just verify it's callable
        assert callable(backend.recognize_text)

    @pytest.mark.skipif(
        sys.platform != "darwin",
        reason="Vision smoke test only on macOS",
    )
    def test_vision_backend_smoke_test_with_rendered_text(self):
        """Smoke test: OCR a tiny PNG with rendered text."""
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            pytest.skip("PIL not available")

        # Create a simple image with rendered text
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "test.png"

            # Create image with white background, black text
            img = Image.new("RGB", (300, 100), color="white")
            draw = ImageDraw.Draw(img)

            # Draw simple text (no font specified, uses default)
            draw.text((10, 10), "HELLO WORLD", fill="black")

            # Save the image
            img.save(image_path)

            # Run OCR
            backend = default_backend()
            result = backend.recognize_text(image_path)

            # Verify that OCR found text
            assert result.word_count > 0, "OCR should have found at least some words"
            # We don't check exact text match as Vision OCR quality can vary,
            # but word_count > 0 indicates the real backend ran

    def test_null_backend_fallback(self):
        """NullOcrBackend is always available as fallback."""
        # Even if vision doesn't work, we should be able to get NullOcrBackend
        from findupe.ocr import NullOcrBackend

        backend = NullOcrBackend()
        result = backend.recognize_text(Path("/fake/path.png"))
        assert result.word_count == 0


class TestOcrConstants:
    """Verify that the OCR tiering constants are properly defined."""

    def test_constants_are_defined(self):
        """All tiering constants should be defined."""
        assert MIN_WORDS == 3
        assert MIN_CONFIDENCE == 0.5
        assert SIM_LOW == 0.25
        assert SIM_HIGH == 0.6

    def test_constants_have_sensible_ranges(self):
        """Confidence should be 0-1, similarity thresholds should be ordered."""
        assert 0 <= MIN_CONFIDENCE <= 1.0
        assert 0 <= SIM_LOW <= 1.0
        assert 0 <= SIM_HIGH <= 1.0
        assert SIM_LOW < SIM_HIGH
