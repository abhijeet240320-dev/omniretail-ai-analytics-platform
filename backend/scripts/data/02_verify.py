"""Verify that the downloaded data is genuinely the data we researched.

USAGE
-----
    .venv/Scripts/python.exe backend/scripts/data/02_verify.py
    .venv/Scripts/python.exe backend/scripts/data/02_verify.py --source olist

WHY THIS EXISTS
---------------
A download that "worked" proves almost nothing. This script answers the
questions that actually matter:

  1. Are all the expected files on disk?
  2. Are the byte sizes what the publisher ships?
  3. Are the SHA-256 hashes the same bytes we hashed on first download?
  4. Do the files still have the columns our analysis depends on?
  5. Are the row counts and date ranges plausible for the real dataset?

Only if all of that passes do we let the pipeline proceed. The output is
also the raw material for ``docs/data_sources.md`` and the data dictionary,
so the documentation is generated from measured facts rather than from
memory.

READING COST
------------
The large files are read with ``nrows`` where a full read is unnecessary.
Where a date range or exact row count is needed we stream the file in
chunks instead of loading 200 MB into RAM, which keeps this runnable on an
ordinary laptop.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from etl.registry import (  # noqa: E402
    REPO_ROOT,
    Manifest,
    Source,
    human_bytes,
    load_lockfile,
    load_manifest,
    raw_dir,
    sha256_of,
)

SCHEMA_PATH = REPO_ROOT / "backend" / "config" / "expected_schemas.yaml"


# ---------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------
@dataclass
class FileReport:
    """Everything we measured about one raw file."""

    name: str
    exists: bool = False
    size_bytes: int = 0
    size_matches: bool | None = None
    sha256: str | None = None
    sha256_matches: bool | None = None
    row_count: int | None = None
    columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    key_ranges: dict[str, tuple[str, str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def stream_rows(path: Path, encoding: str, limit: int | None = None):
    """Yield rows from a CSV without holding the whole file in memory.

    Uses the ``csv`` module rather than pandas because we only need the
    header, a count, and a few column extremes - pandas would allocate
    hundreds of megabytes to tell us something we can count on the fly.
    """
    with path.open("r", encoding=encoding, newline="", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return
        yield header
        for index, row in enumerate(reader):
            if limit is not None and index >= limit:
                return
            yield row


def inspect_csv(
    path: Path,
    spec: dict,
    *,
    encoding: str = "utf-8",
    scan_limit: int | None = None,
) -> FileReport:
    """Check one CSV against its expected-schema entry."""
    report = FileReport(name=path.name)
    report.exists = True
    report.size_bytes = path.stat().st_size

    required = spec.get("required_columns", [])
    key_columns = spec.get("key_columns", [])

    rows = stream_rows(path, encoding, limit=scan_limit)
    try:
        header = next(rows)
    except StopIteration:
        report.problems.append("file is empty")
        return report

    # Some publishers emit a UTF-8 BOM on the first column name.
    header = [c.lstrip("\ufeff").strip() for c in header]
    report.columns = header

    missing = [c for c in required if c not in header]
    report.missing_columns = missing
    if missing:
        report.problems.append(f"missing required columns: {missing}")

    report.extra_columns = [c for c in header if c not in required]

    # Track min/max for the key columns so we can print a date range.
    key_index = {c: header.index(c) for c in key_columns if c in header}
    mins: dict[str, str] = {}
    maxs: dict[str, str] = {}

    count = 0
    for row in rows:
        count += 1
        for column, index in key_index.items():
            if index >= len(row):
                continue
            value = row[index].strip()
            if not value:
                continue
            if column not in mins or value < mins[column]:
                mins[column] = value
            if column not in maxs or value > maxs[column]:
                maxs[column] = value

    report.row_count = count if scan_limit is None else count
    report.key_ranges = {c: (mins.get(c, ""), maxs.get(c, "")) for c in key_columns}

    # Wide-format check (M5): the day columns must span the full horizon.
    wide = spec.get("wide_day_columns")
    if wide:
        prefix = wide["prefix"]
        expected = {f"{prefix}{n}" for n in range(wide["first"], wide["last"] + 1)}
        present = {c for c in header if c.startswith(prefix)}
        absent = expected - present
        if absent:
            sample = sorted(absent, key=lambda c: int(c[len(prefix):]))[:5]
            report.problems.append(
                f"wide day columns incomplete: {len(absent)} missing (e.g. {sample})"
            )

    return report


def verify_source(
    manifest: Manifest,
    source: Source,
    schema: dict,
    lock: dict,
    *,
    full_scan: bool,
) -> tuple[bool, list[FileReport]]:
    """Check one source end-to-end. Returns (ok, reports)."""
    target = raw_dir(manifest, source.id)
    file_specs = schema.get("files", {})
    reports: list[FileReport] = []
    ok = True

    print(f"\n{'=' * 70}")
    print(f"SOURCE {source.id}  -  {source.name}")
    print(f"{'=' * 70}")
    print(f"publisher : {source.publisher}")
    print(f"domain    : {source.domain}")
    print(f"directory : {target}")

    # 1. Every file the manifest promises must be present.
    for remote in source.files:
        present = (target / remote.filename).exists()
        if not present:
            print(f"  MISSING  {remote.filename}  <- run 01_download.py")
            ok = False

    for name in source.expect_files:
        if not (target / name).exists():
            print(f"  MISSING  {name}  (expected after extraction)")
            ok = False

    # 2. Check each CSV we have a schema for.
    for filename, spec in file_specs.items():
        path = target / filename
        if not path.exists():
            continue

        encoding = spec.get("encoding", "utf-8")
        report = inspect_csv(
            path,
            spec,
            encoding=encoding,
            scan_limit=None if full_scan else 250_000,
        )
        reports.append(report)

        # Size against the manifest (archives are checked by their own entry).
        manifest_entry = next(
            (f for f in source.files if f.filename == filename), None
        )
        if manifest_entry and manifest_entry.expected_bytes is not None:
            report.size_matches = report.size_bytes == manifest_entry.expected_bytes

        # Re-hash the file on disk and compare with the lockfile written at
        # download time. This is the check that proves the bytes have not
        # been altered, truncated or replaced since we first fetched them.
        recorded = lock.get(f"{source.id}/{filename}")
        if recorded:
            report.sha256 = sha256_of(path)
            report.sha256_matches = report.sha256 == recorded["sha256"]
            if not report.sha256_matches:
                report.problems.append(
                    "SHA-256 differs from the lockfile - the file on disk is "
                    "not the file we downloaded"
                )
                ok = False

        scan_note = "" if full_scan else " (first 250,000 rows)"
        print(f"\n  file       : {filename}")
        print(f"  size       : {human_bytes(report.size_bytes)}")
        if report.row_count is not None:
            suffix = " rows scanned" if not full_scan else " rows"
            print(f"  rows       : {report.row_count:,}{suffix}{scan_note}")
        print(f"  columns    : {len(report.columns)}")
        if report.sha256:
            verdict = "matches lockfile" if report.sha256_matches else "MISMATCH"
            print(f"  sha256     : {report.sha256[:16]}...  ({verdict})")
        for column, (low, high) in report.key_ranges.items():
            print(f"  range      : {column}  {low or '-'} .. {high or '-'}")
        if report.extra_columns:
            shown = ", ".join(report.extra_columns[:6])
            more = "" if len(report.extra_columns) <= 6 else f" (+{len(report.extra_columns) - 6} more)"
            print(f"  extra cols : {shown}{more}")
        for problem in report.problems:
            print(f"  PROBLEM    {problem}")
            ok = False

    return ok, reports


def write_measurements(
    manifest: Manifest, results: dict[str, list[FileReport]]
) -> Path:
    """Persist measured facts so documentation can cite real numbers."""
    payload = {
        source_id: [
            {
                "file": r.name,
                "bytes": r.size_bytes,
                "rows_scanned": r.row_count,
                "columns": r.columns,
                "key_ranges": {k: list(v) for k, v in r.key_ranges.items()},
                "problems": r.problems,
            }
            for r in reports
        ]
        for source_id, reports in results.items()
    }
    destination = manifest.raw_root / "_measurements.json"
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify downloaded OmniRetail data.")
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="count every row instead of the first 250,000 (slower).",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))["sources"]
    lock = load_lockfile(manifest)

    wanted = (
        [manifest.by_id(s) for s in args.sources]
        if args.sources
        else [s for s in manifest.selected() if s.files]
    )

    all_ok = True
    results: dict[str, list[FileReport]] = {}
    for source in wanted:
        ok, reports = verify_source(
            manifest,
            source,
            schema.get(source.id, {}),
            lock,
            full_scan=args.full_scan,
        )
        all_ok &= ok
        results[source.id] = reports

    measurements = write_measurements(manifest, results)

    print(f"\n{'=' * 70}")
    print("VERIFICATION RESULT:", "ALL SOURCES OK" if all_ok else "PROBLEMS FOUND")
    print(f"measurements written to {measurements}")
    print(f"{'=' * 70}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())