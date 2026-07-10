"""Shared fixtures: build throwaway file trees and tiny real images."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image


def make_tree(base: Path, spec: dict[str, bytes | str]) -> None:
    """Create files from {relative_path: content}; parents auto-created."""
    for rel, content in spec.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode()
        p.write_bytes(content)


def jpeg_bytes(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def png_bytes(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def gradient_image(size: tuple[int, int] = (256, 256)) -> Image.Image:
    """Deterministic non-uniform image so perceptual hashes are meaningful."""
    img = Image.new("RGB", size)
    px = img.load()
    for x in range(size[0]):
        for y in range(size[1]):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)
    return img


def gradient_jpeg(quality: int = 90, size: tuple[int, int] = (256, 256)) -> bytes:
    buf = io.BytesIO()
    gradient_image(size).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


@pytest.fixture
def tree(tmp_path: Path):
    def _build(spec: dict[str, bytes | str]) -> Path:
        make_tree(tmp_path, spec)
        return tmp_path
    return _build
