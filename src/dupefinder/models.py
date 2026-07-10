"""Core data types shared by every dupefinder module."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Formats the perceptual pass can decode. RAW formats go through embedded-preview
# extraction; everything else through Pillow (pillow-heif registered for HEIF/HEIC).
RAW_EXTS = {".cr3", ".cr2", ".crw", ".craw", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}
PILLOW_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".webp", ".gif", ".bmp"}
IMAGE_EXTS = RAW_EXTS | PILLOW_EXTS

# Sidecar/companion extensions: these files belong to a primary and are never
# duplicate candidates themselves.
SIDECAR_EXTS = {".xmp", ".aae", ".thm"}
LIVE_PHOTO_VIDEO_EXTS = {".mov", ".mp4"}

# Managed stores: scanning inside these corrupts app catalogs if files are removed.
# Hard denylist — no override in v1.
MANAGED_LIBRARY_SUFFIXES = (
    ".photoslibrary", ".lrlib", ".lrdata", ".lrcat", ".lrcat-data",
    ".aplibrary", ".migratedphotolibrary",
)

# Directory names never worth scanning.
SKIP_DIR_NAMES = {
    ".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__",
    ".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    ".TemporaryItems", ".MobileBackups",
}


def canonical_format(path: Path) -> str:
    """Normalize an extension to a format label: 'x.JPEG' and 'y.jpg' -> 'jpeg'."""
    ext = path.suffix.lower().lstrip(".")
    return {"jpg": "jpeg", "tif": "tiff", "heif": "heic"}.get(ext, ext)


def norm_path(path: Path) -> str:
    """NFC-normalized absolute path string; APFS may hand back NFD names."""
    return unicodedata.normalize("NFC", str(path))


@dataclass
class FileRecord:
    path: Path
    size: int
    mtime_ns: int
    dev: int
    inode: int
    volume: str  # volume mount root, e.g. "/" or "/Volumes/Extreme SSD"
    volume_uuid: str = ""  # filesystem identity — cache keys use this, not the mount path
    is_cloud_stub: bool = False
    cloud_synced: bool = False  # lives in an iCloud/Dropbox-synced tree: deletion propagates
    is_image: bool = field(init=False)
    format: str = field(init=False)
    exact_hash: str | None = None       # full-file BLAKE2b hex
    phash: int | None = None            # 64-bit perceptual hash
    dhash: int | None = None
    width: int | None = None
    height: int | None = None
    capture_key: str | None = None      # DateTimeOriginal + exposure params (1s granularity)
    capture_subsec: str | None = None   # SubSecTimeOriginal: distinguishes burst frames
    hash_error: str | None = None       # unreadable/undecodable — reported, never fatal
    # sidecar/Live-Photo records riding along — full FileRecords so they carry
    # size + hash all the way into the selection JSON and undo manifest
    companions: list["FileRecord"] = field(default_factory=list)
    hardlink_of: Path | None = None     # same (dev, inode) as an earlier record

    def __post_init__(self) -> None:
        self.format = canonical_format(self.path)
        self.is_image = self.path.suffix.lower() in IMAGE_EXTS


@dataclass
class Cluster:
    """Files of one format that are DIRECTLY exact/strong-connected — true copies
    of one image. Surplus lives only here: transitive family membership alone
    never makes a file deletable."""
    cluster_id: str
    files: list[FileRecord]
    keeper: FileRecord
    surplus: list[FileRecord]  # files minus keeper: the only pre-checkable candidates


@dataclass
class FormatPartition:
    """All files of one format within a family. Members outside any cluster are
    informational (e.g. the cross-format sibling that joined via a RAW↔JPEG edge)."""
    format: str
    files: list[FileRecord]
    clusters: list[Cluster]

    @property
    def clustered(self) -> set[int]:
        return {id(f) for c in self.clusters for f in c.files}


@dataclass
class Family:
    """One visual image across formats, or one exact-duplicate group of non-images."""
    family_id: str
    kind: str                    # "exact" | "visual" | "possible"
    partitions: list[FormatPartition]
    flags: list[str] = field(default_factory=list)  # "possible-burst", "low-entropy", …

    @property
    def surplus_bytes(self) -> int:
        return sum(f.size for p in self.partitions for c in p.clusters for f in c.surplus)

    @property
    def surplus_count(self) -> int:
        return sum(len(c.surplus) for p in self.partitions for c in p.clusters)


@dataclass
class ScanResult:
    scan_id: str
    roots: list[Path]
    families: list[Family]
    skipped_stubs: list[Path] = field(default_factory=list)
    skipped_managed: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    hardlink_notes: list[tuple[Path, Path]] = field(default_factory=list)
    zero_byte: list[Path] = field(default_factory=list)
