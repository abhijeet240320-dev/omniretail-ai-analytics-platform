"""Download every real dataset described in ``backend/config/datasets.yaml``.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/data/01_download.py
    .venv/Scripts/python.exe backend/scripts/data/01_download.py --source olist
    .venv/Scripts/python.exe backend/scripts/data/01_download.py --force

DESIGN NOTES
------------
* Raw data is NEVER overwritten. If a file already exists with the right
  size we skip the download. ``--force`` re-downloads it, and is the only
  way to touch an existing raw file.
* Every download is streamed to a ``.part`` file and renamed only after the
  size check passes, so an interrupted download can never masquerade as a
  complete one.
* After downloading we compute the SHA-256 and record it in
  ``data/raw/_checksums.json``. That lockfile is the proof-of-integrity used
  by 02_verify.py and by the lineage documentation.
* The script exits non-zero if anything failed, so it is safe to use in a
  pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# Allow "python backend/scripts/data/01_download.py" from the repo root.
# parents[2] is <root>/backend, which is where the etl package lives.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from etl.registry import (  # noqa: E402
    Manifest,
    RemoteFile,
    Source,
    human_bytes,
    load_lockfile,
    load_manifest,
    raw_dir,
    save_lockfile,
)

# A real browser-ish User-Agent. Some CDNs (including Hugging Face) serve
# a different response to unknown clients.
USER_AGENT = "OmniRetail-AI-Analytics/1.0 (research; contact: local user)"
CHUNK = 1024 * 256  # 256 KiB per read: fast, and keeps memory flat on 200 MB files

# A live progress bar is nice in a terminal and unreadable in a log file:
# every refresh writes a carriage return, which redirect-to-file turns into
# hundreds of near-duplicate lines. So we only animate when stdout is a real
# terminal, and otherwise report progress at sensible milestones.
INTERACTIVE = sys.stdout.isatty()


def sha256_of(path: Path) -> str:
    """Stream a file through SHA-256 without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download_file(remote: RemoteFile, destination: Path) -> None:
    """Stream one URL to ``destination`` with a size check.

    Raises RuntimeError if the server sends a different number of bytes than
    the manifest says it should. That check is the difference between
    "we downloaded the dataset" and "we downloaded *something*".
    """
    partial = destination.with_suffix(destination.suffix + ".part")

    request = urllib.request.Request(remote.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            declared = response.headers.get("Content-Length")
            total = int(declared) if declared else None

            received = 0
            started = time.monotonic()
            next_milestone = 10  # percent
            with partial.open("wb") as out:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    received += len(block)

                    if not total:
                        continue
                    percent = received / total * 100
                    if INTERACTIVE:
                        print(
                            f"\r    {percent:5.1f}%  {human_bytes(received)}"
                            f" / {human_bytes(total)}",
                            end="",
                            flush=True,
                        )
                    elif percent >= next_milestone:
                        print(
                            f"    {next_milestone:3d}%  {human_bytes(received)}",
                            flush=True,
                        )
                        next_milestone += 10
            elapsed = max(time.monotonic() - started, 0.001)
            prefix = "\r    " if INTERACTIVE else "    "
            print(
                f"{prefix}done   {human_bytes(received)}"
                f" in {elapsed:,.0f}s ({human_bytes(received / elapsed)}/s)"
            )
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"HTTP {exc.code} for {remote.url}") from exc
    except urllib.error.URLError as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"network error for {remote.url}: {exc.reason}") from exc

    actual = partial.stat().st_size
    if remote.expected_bytes is not None and actual != remote.expected_bytes:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"size mismatch for {remote.filename}: got {actual:,} bytes, "
            f"manifest expects {remote.expected_bytes:,}. Refusing to keep it."
        )

    partial.replace(destination)


def extract_archive(archive: Path, target_dir: Path) -> list[str]:
    """Extract a zip, flattening any single top-level folder.

    Returns the names of the files that landed in ``target_dir``. We only
    ever read the first path component of a member name, which also stops a
    malicious archive from writing outside the data directory.
    """
    written: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            # Strip any leading folder ("The Complete Journey/foo.csv" -> "foo.csv").
            flat_name = Path(member.filename).name
            if not flat_name or flat_name.startswith("."):
                continue
            target = target_dir / flat_name
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            written.append(flat_name)
    return written


def fetch_source(
    manifest: Manifest,
    source: Source,
    lock: dict,
    *,
    force: bool,
    keep_archives: bool = True,
) -> bool:
    """Download (and extract) one source. Returns True when it is complete."""
    target = raw_dir(manifest, source.id)
    print(f"\n=== {source.id}: {source.name}")
    print(f"    -> {target}")

    if source.acquisition != "automatic":
        print(f"    SKIPPED: acquisition mode is {source.acquisition!r}")
        return False

    ok = True
    for remote in source.files:
        destination = target / remote.filename
        key = f"{source.id}/{remote.filename}"

        already = destination.exists()
        if already and not force:
            size = destination.stat().st_size
            if remote.expected_bytes is None or size == remote.expected_bytes:
                print(f"    present  {remote.filename} ({human_bytes(size)})")
            else:
                print(
                    f"    STALE    {remote.filename} is {human_bytes(size)}, "
                    f"expected {human_bytes(remote.expected_bytes)} "
                    f"(re-run with --force)"
                )
                ok = False
                continue
        else:
            print(f"    fetch    {remote.filename}")
            try:
                download_file(remote, destination)
            except RuntimeError as exc:
                print(f"    FAILED   {exc}")
                ok = False
                continue

        # Record the checksum once; later runs re-read it from the lockfile.
        digest = sha256_of(destination)
        lock[key] = {
            "sha256": digest,
            "bytes": destination.stat().st_size,
            "source_url": remote.url,
        }

        if remote.extract and remote.is_archive:
            # Only extract when something is actually missing. Re-extracting a
            # 128 MB archive on every run is pure waste, and (worse) it would
            # silently overwrite extracted files that a human had already
            # inspected.
            already_extracted = all(
                (target / name).exists() for name in source.expect_files
            )
            if already_extracted and not force:
                print(
                    f"    present  {len(source.expect_files)} file(s) already "
                    f"extracted from {remote.filename}"
                )
            else:
                names = extract_archive(destination, target)
                print(f"    extract  {len(names)} file(s) from {remote.filename}")
                for name in names:
                    extracted = target / name
                    lock[f"{source.id}/{name}"] = {
                        "sha256": sha256_of(extracted),
                        "bytes": extracted.stat().st_size,
                        "source_url": remote.url,
                        "extracted_from": remote.filename,
                    }
            # The checksum of the extracted files must exist on every run, not
            # just on the first one, otherwise verification loses its baseline.
            for name in source.expect_files:
                extracted = target / name
                if extracted.exists():
                    lock.setdefault(
                        f"{source.id}/{name}",
                        {
                            "sha256": sha256_of(extracted),
                            "bytes": extracted.stat().st_size,
                            "source_url": remote.url,
                            "extracted_from": remote.filename,
                        },
                    )
            if not keep_archives:
                destination.unlink(missing_ok=True)

    # Final completeness check against expect_files.
    for name in source.expect_files:
        if not (target / name).exists():
            print(f"    MISSING  {name} was expected after extraction")
            ok = False

    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the OmniRetail datasets.")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="only this source id (repeatable). Default: all selected sources.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even if a file is already present.",
    )
    parser.add_argument(
        "--drop-archives",
        action="store_true",
        help="delete the .zip after extracting (saves disk, loses the original).",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    lock = load_lockfile(manifest)

    if args.sources:
        wanted = [manifest.by_id(s) for s in args.sources]
    else:
        wanted = list(manifest.selected())

    print("OmniRetail data acquisition")
    print(f"  repository root : {manifest.raw_root.parent.parent}")
    print(f"  raw root        : {manifest.raw_root}")
    print(f"  sources         : {', '.join(s.id for s in wanted)}")

    results: dict[str, bool] = {}
    for source in wanted:
        results[source.id] = fetch_source(
            manifest,
            source,
            lock,
            force=args.force,
            keep_archives=not args.drop_archives,
        )

    save_lockfile(manifest, lock)

    print("\n--- summary ---")
    for source_id, ok in results.items():
        print(f"  {'OK  ' if ok else 'FAIL'}  {source_id}")
    print(f"  lockfile        : {manifest.lockfile}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())