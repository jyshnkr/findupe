"""macOS Vision OCR backend for the screenshot-discrimination demoter (see
grouping.py). Import of the Vision/Foundation frameworks is guarded so this
module — and everything that imports it — works on non-Darwin platforms and
in CI: the real backend is only ever constructed on macOS, and even there,
only when the frameworks actually import cleanly."""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Tiering constants — initial calibration; see the plan's "Risks/notes" on
# tuning these against real screenshot pairs post-implementation.
MIN_WORDS = 3
MIN_CONFIDENCE = 0.5
SIM_LOW = 0.25
SIM_HIGH = 0.6


@dataclass
class OcrResult:
    text: str
    mean_confidence: float
    word_count: int


class OcrBackend(Protocol):
    def recognize_text(self, path: Path) -> OcrResult: ...


class NullOcrBackend:
    """Used on non-Darwin platforms, or when Vision fails to import. Returns
    an empty result — grouping's OCR gate treats an empty/sparse result as
    "not confident", which downgrades to possible rather than breaking the
    edge outright, so a missing OCR backend never wrongly kills a real dup."""

    def recognize_text(self, path: Path) -> OcrResult:
        return OcrResult(text="", mean_confidence=0.0, word_count=0)


_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text.casefold()


def similarity(text_a: str, text_b: str) -> float:
    """Token-set Jaccard similarity, 0.0 (disjoint) .. 1.0 (identical tokens)."""
    tokens_a = set(normalize_text(text_a).split())
    tokens_b = set(normalize_text(text_b).split())
    if not tokens_a and not tokens_b:
        return 1.0
    union = tokens_a | tokens_b
    if not union:
        return 1.0
    return len(tokens_a & tokens_b) / len(union)


def _load_vision_backend():
    if sys.platform != "darwin":
        return None
    try:
        from Foundation import NSURL
        import Vision
    except ImportError:
        return None

    class VisionOcrBackend:
        def recognize_text(self, path: Path) -> OcrResult:
            try:
                url = NSURL.fileURLWithPath_(str(path))
                handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
                request = Vision.VNRecognizeTextRequest.alloc().init()
                request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
                ok, _error = handler.performRequests_error_([request], None)
                if not ok:
                    return OcrResult(text="", mean_confidence=0.0, word_count=0)
                lines: list[str] = []
                confidences: list[float] = []
                for observation in request.results() or []:
                    candidates = observation.topCandidates_(1)
                    if not candidates:
                        continue
                    top = candidates[0]
                    lines.append(str(top.string()))
                    confidences.append(float(top.confidence()))
                text = "\n".join(lines)
                mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
                word_count = len(normalize_text(text).split())
                return OcrResult(text=text, mean_confidence=mean_conf, word_count=word_count)
            except Exception:
                # OCR is a demoter, never load-bearing for correctness — a
                # failure here must degrade to "no signal", never crash a scan.
                return OcrResult(text="", mean_confidence=0.0, word_count=0)

    return VisionOcrBackend()


def default_backend() -> OcrBackend:
    """Real Vision backend on macOS when the frameworks import cleanly, a
    null backend everywhere else (non-Darwin, or a broken pyobjc install)."""
    return _load_vision_backend() or NullOcrBackend()
