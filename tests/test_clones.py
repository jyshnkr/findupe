"""shares_physical_extents() / KeeperExtents: real cp -c clone vs. real copy.

These exercise the real F_LOG2PHYS_EXT syscall via a real cp -c clone on
whatever filesystem tmp_path lands on (APFS on any supported macOS setup).
"""

import os
import subprocess

import pytest

from findupe.clones import KeeperExtents, shares_physical_extents


def _write(path, size=2 * 1024 * 1024):
    path.write_bytes(os.urandom(size))


def test_clone_shares_extents_with_original(tmp_path):
    original = tmp_path / "original.bin"
    clone = tmp_path / "clone.bin"
    _write(original)
    subprocess.run(["cp", "-c", str(original), str(clone)], check=True)

    assert shares_physical_extents(original, clone) is True


def test_independent_copy_does_not_share_extents(tmp_path):
    original = tmp_path / "original.bin"
    copy = tmp_path / "copy.bin"
    _write(original)
    subprocess.run(["cp", str(original), str(copy)], check=True)

    assert shares_physical_extents(original, copy) is False


def test_independently_written_identical_content_does_not_share_extents(tmp_path):
    """Rules out any content-based dedup false positive — two files with the
    SAME bytes but written separately (never cloned) must not appear shared."""
    content = os.urandom(1024 * 1024)
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(content)
    b.write_bytes(content)

    assert shares_physical_extents(a, b) is False


def test_nonexistent_file_returns_false_not_raise(tmp_path):
    original = tmp_path / "original.bin"
    _write(original)

    assert shares_physical_extents(original, tmp_path / "does-not-exist.bin") is False
    assert shares_physical_extents(tmp_path / "does-not-exist.bin", original) is False


def test_partially_edited_clone_still_shares_its_unedited_remainder(tmp_path):
    original = tmp_path / "original.bin"
    clone = tmp_path / "clone.bin"
    _write(original, size=5 * 1024 * 1024)
    subprocess.run(["cp", "-c", str(original), str(clone)], check=True)
    with open(clone, "r+b") as f:
        f.write(os.urandom(4096))  # dirty the first page via copy-on-write

    assert shares_physical_extents(original, clone) is True


def test_keeper_extents_reused_across_multiple_comparisons(tmp_path):
    """The KeeperExtents class exists so a cluster's keeper is probed once,
    not once per surplus file — verify it gives the same answers as the
    one-off convenience wrapper."""
    original = tmp_path / "original.bin"
    clone = tmp_path / "clone.bin"
    copy = tmp_path / "copy.bin"
    _write(original)
    subprocess.run(["cp", "-c", str(original), str(clone)], check=True)
    subprocess.run(["cp", str(original), str(copy)], check=True)

    keeper_extents = KeeperExtents(original)

    assert keeper_extents.shares_with(clone) is True
    assert keeper_extents.shares_with(copy) is False


def test_empty_file_never_flagged_as_clone(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"")
    b.write_bytes(b"")

    assert shares_physical_extents(a, b) is False
