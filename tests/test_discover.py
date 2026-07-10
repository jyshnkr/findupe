import os
from pathlib import Path

from dupefinder.discover import discover, is_dataless, volume_root
from dupefinder.models import canonical_format


def paths(result):
    return {r.path.name for r in result.records}


def test_basic_walk_and_zero_byte(tree):
    root = tree({"a.txt": "hello", "sub/b.txt": "world", "empty.txt": ""})
    res = discover([root])
    assert paths(res) == {"a.txt", "b.txt"}
    assert [p.name for p in res.zero_byte] == ["empty.txt"]


def test_managed_library_refused(tree):
    root = tree({
        "ok.jpg": "x",
        "My Photos.photoslibrary/originals/img.jpg": "y",
        "Lightroom/cat.lrcat/inner.db": "z",
    })
    res = discover([root])
    assert paths(res) == {"ok.jpg"}
    skipped = {p.name for p in res.skipped_managed}
    assert skipped == {"My Photos.photoslibrary", "cat.lrcat"}


def test_managed_library_as_root_refused(tree):
    root = tree({"lib.photoslibrary/img.jpg": "x"})
    res = discover([root / "lib.photoslibrary"])
    assert res.records == []
    assert len(res.skipped_managed) == 1


def test_symlinks_never_followed(tree):
    root = tree({"real/a.txt": "data"})
    (root / "loop").symlink_to(root)  # would loop forever if followed
    (root / "link.txt").symlink_to(root / "real" / "a.txt")
    res = discover([root])
    assert paths(res) == {"a.txt"}


def test_hardlink_detected_once(tree):
    root = tree({"orig.bin": "payload"})
    os.link(root / "orig.bin", root / "copy.bin")
    res = discover([root])
    recs = {r.path.name: r for r in res.records}
    assert len(recs) == 2
    linked = [r for r in recs.values() if r.hardlink_of is not None]
    assert len(linked) == 1
    assert len(res.hardlink_notes) == 1


def test_companions_attach_and_leave_records(tree):
    root = tree({
        "shoot/IMG_1.CR3": "raw",
        "shoot/IMG_1.xmp": "sidecar",
        "shoot/IMG_2.HEIC": "heic",
        "shoot/IMG_2.MOV": "livephoto",
        "shoot/random.mov": "standalone video",
        "shoot/orphan.xmp": "no primary",
    })
    res = discover([root])
    recs = {r.path.name: r for r in res.records}
    # sidecar and live-photo MOV removed from records, attached as companions
    assert set(recs) == {"IMG_1.CR3", "IMG_2.HEIC", "random.mov", "orphan.xmp"}
    assert [p.name for p in recs["IMG_1.CR3"].companions] == ["IMG_1.xmp"]
    assert [p.name for p in recs["IMG_2.HEIC"].companions] == ["IMG_2.MOV"]


def test_exclude_globs(tree):
    root = tree({"keep.txt": "a", "skip.log": "b", "cache/x.txt": "c"})
    res = discover([root], exclude_globs=["*.log", "cache"])
    assert paths(res) == {"keep.txt"}


def test_unicode_and_emoji_names(tree):
    root = tree({"café 📷.jpg": "x", "sub dir/नमस्ते.png": "y"})
    res = discover([root])
    assert paths(res) == {"café 📷.jpg", "नमस्ते.png"}


def test_overlapping_roots_deduplicated(tree):
    root = tree({"sub/a.txt": "x"})
    res = discover([root, root / "sub"])
    assert len(res.records) == 1


def test_unmounted_root_is_error_not_crash():
    res = discover([Path("/Volumes/definitely-not-mounted-xyz")])
    assert res.records == []
    assert len(res.errors) == 1


def test_is_dataless_flag():
    class FakeStat:
        st_flags = 0x40000000
    assert is_dataless(FakeStat())

    class NormalStat:
        st_flags = 0
    assert not is_dataless(NormalStat())


def test_volume_root():
    assert volume_root(Path("/Users/x/file.txt")) == "/"
    assert volume_root(Path("/Volumes/Extreme SSD/pics/a.heic")) == "/Volumes/Extreme SSD"


def test_canonical_format():
    assert canonical_format(Path("a.JPG")) == "jpeg"
    assert canonical_format(Path("a.jpeg")) == "jpeg"
    assert canonical_format(Path("a.HEIF")) == "heic"
    assert canonical_format(Path("a.CR3")) == "cr3"
