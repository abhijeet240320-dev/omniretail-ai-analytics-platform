"""Dataset manifest loading and repository path resolution.

WHY THIS FILE EXISTS
--------------------
Every stage of the pipeline - download, verify, profile, clean, transform and
load - needs the same two things: the dataset manifest, and the correct
absolute repository paths. Duplicating that logic is how a project ends up
with one script writing to ``data/raw`` and another reading from ``./raw``.

So the manifest is parsed here exactly once, and every path is derived from the
location of this file rather than from the current working directory. That means
all stages behave identically whether you run them from the repository root,
from ``backend/``, or from a task scheduler.

It lives in ``etl`` rather than under ``scripts`` because it is shared
infrastructure, not a script: the CLI scripts in ``backend/scripts/data`` and
the per-source cleaning modules in ``backend/etl`` both import it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

# Read size for streaming hashes and large CSV scans: 256 KiB keeps memory
# flat on multi-hundred-megabyte files while still being a fast sequential read.
CHUNK_SIZE = 1024 * 256

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
# This module lives at <root>/backend/etl/registry.py, so walking up three
# levels lands on the repository root.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
MANIFEST_PATH: Path = REPO_ROOT / "backend" / "config" / "datasets.yaml"


# ---------------------------------------------------------------------
# Manifest objects
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class RemoteFile:
    """One downloadable file belonging to a source."""

    filename: str
    url: str
    expected_bytes: int | None
    sha256: str | None
    kind: str  # "csv" (data), "archive" (needs extraction) or "doc" (publisher notes)
    encoding: str = "utf-8"
    extract: bool = False

    @property
    def is_archive(self) -> bool:
        return self.kind == "archive"


@dataclass(frozen=True)
class Source:
    """One real-world dataset (dunnhumby, dataco, olist, m5)."""

    id: str
    name: str
    publisher: str
    domain: str
    business_purpose: str
    homepage: str
    license: str
    citation: str
    acquisition: str  # "automatic" | "manual" | "not_selected"
    files: tuple[RemoteFile, ...]
    expect_files: tuple[str, ...]
    known_limitations: str

    @property
    def is_selected(self) -> bool:
        """True when this dataset is actually part of the build."""
        return self.acquisition != "not_selected"


@dataclass(frozen=True)
class Manifest:
    """The whole ``datasets.yaml`` file, typed."""

    version: int
    raw_root: Path
    processed_root: Path
    lockfile: Path
    sources: tuple[Source, ...]

    def selected(self) -> tuple[Source, ...]:
        """Only the sources we actually build on."""
        return tuple(s for s in self.sources if s.is_selected)

    def by_id(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(f"unknown source id: {source_id!r}")


def load_manifest(path: Path | None = None) -> Manifest:
    """Read and validate ``datasets.yaml``.

    Paths inside the manifest are relative to the repository root, which is
    what lets the project stay portable across machines.
    """
    manifest_path = path or MANIFEST_PATH
    raw: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    paths = raw["paths"]

    sources: list[Source] = []
    for entry in raw["sources"]:
        files = tuple(
            RemoteFile(
                filename=f["filename"],
                url=f["url"],
                expected_bytes=f.get("expected_bytes"),
                sha256=f.get("sha256"),
                kind=f.get("kind", "csv"),
                encoding=f.get("encoding", "utf-8"),
                extract=bool(f.get("extract", False)),
            )
            for f in entry.get("files", [])
        )
        sources.append(
            Source(
                id=entry["id"],
                name=entry["name"],
                publisher=entry["publisher"],
                domain=entry["domain"],
                business_purpose=entry["business_purpose"].strip(),
                homepage=entry["homepage"],
                license=entry["license"].strip(),
                citation=entry["citation"].strip(),
                acquisition=entry.get("acquisition", "automatic"),
                files=files,
                expect_files=tuple(entry.get("expect_files", [])),
                known_limitations=entry["known_limitations"].strip(),
            )
        )

    return Manifest(
        version=raw.get("version", 1),
        raw_root=REPO_ROOT / paths["raw_root"],
        processed_root=REPO_ROOT / paths["processed_root"],
        lockfile=REPO_ROOT / paths["lockfile"],
        sources=tuple(sources),
    )


# ---------------------------------------------------------------------
# Checksum lockfile
# ---------------------------------------------------------------------
def load_lockfile(manifest: Manifest) -> dict[str, Any]:
    """Return ``{ "<source>/<filename>": {"sha256": ..., "bytes": ...} }``.

    The lockfile is what turns "we downloaded some files once" into "we can
    prove the bytes on disk are the same bytes we analysed". It is written
    on first download and re-checked by 02_verify.py on every later run.
    """
    if not manifest.lockfile.exists():
        return {}
    return json.loads(manifest.lockfile.read_text(encoding="utf-8"))


def save_lockfile(manifest: Manifest, data: dict[str, Any]) -> None:
    manifest.lockfile.parent.mkdir(parents=True, exist_ok=True)
    manifest.lockfile.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def raw_dir(manifest: Manifest, source_id: str) -> Path:
    """``data/raw/<source_id>`` - created if missing."""
    path = manifest.raw_root / source_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def processed_dir(manifest: Manifest, source_id: str) -> Path:
    """``data/processed/<source_id>`` - created if missing."""
    path = manifest.processed_root / source_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_of(path: Path) -> str:
    """Hash a file without loading it into memory.

    Lives here rather than in a script because both the downloader (which
    records the hash) and the verifier (which re-checks it) must compute it
    exactly the same way. One implementation, one definition of "the same file".
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def human_bytes(num: float) -> str:
    """Render a byte count the way a human reads it (MB / GB)."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0:
            return f"{num:,.1f} {unit}"
        num /= 1024.0
    return f"{num:,.1f} TB"