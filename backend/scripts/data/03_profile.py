"""Profile the real downloaded data and write reports.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/data/03_profile.py
    .venv/Scripts/python.exe backend/scripts/data/03_profile.py --source olist
    .venv/Scripts/python.exe backend/scripts/data/03_profile.py --source m5 --file calendar.csv

WHAT IT PRODUCES
----------------
    docs/profiling/<source>__<file>.md    human-readable report per file
    docs/profiling/_summary.json          machine-readable, all files

WHY PROFILE AT ALL
------------------
Cleaning code written against assumptions is how you get silent nonsense. This
step measures the actual columns before any transformation is written, so the
cleaning code is driven by observed facts: real null rates, real cardinality,
real ranges, real duplicate counts.

THE MEMORY PROBLEM, AND HOW IT IS HANDLED
-----------------------------------------
The raw data totals ~1.5 GB and one file is 36.8 million rows. Loading
everything at once would need several gigabytes of RAM and would fail on an
ordinary laptop. So:

* Small files are read whole - it is faster and the numbers are exact.
* Large files are read in CHUNKS: we accumulate counts, per-column null counts,
  numeric min/max and a capped set of distinct values, then discard each chunk.
  Peak memory stays roughly constant regardless of file size.

The cost of chunking is that distinct-value counts are capped. That is
disclosed in the report rather than hidden: a capped number is reported as
">= N" so it is never mistaken for an exact figure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from etl.registry import (  # noqa: E402
    REPO_ROOT,
    Manifest,
    Source,
    human_bytes,
    load_manifest,
    raw_dir,
)

OUTPUT_DIR = REPO_ROOT / "docs" / "profiling"

# Above this size we stream in chunks instead of loading the file whole.
WHOLE_FILE_LIMIT = 120 * 1024 * 1024  # 120 MB
CHUNK_ROWS = 250_000

# Distinct-value estimation is capped so a 37-million-row file cannot exhaust
# memory by accumulating every unique key.
DISTINCT_CAP = 50_000


@dataclass
class ColumnProfile:
    """Everything we measured about one column."""

    name: str
    dtype: str
    non_null: int = 0
    nulls: int = 0
    distinct: int = 0
    distinct_capped: bool = False
    minimum: str | None = None
    maximum: str | None = None
    samples: list[str] = field(default_factory=list)

    @property
    def null_pct(self) -> float:
        total = self.non_null + self.nulls
        return (self.nulls / total * 100) if total else 0.0

    @property
    def distinct_ratio(self) -> float:
        return (self.distinct / self.non_null) if self.non_null else 0.0


@dataclass
class FileProfile:
    """Everything we measured about one file."""

    source_id: str
    filename: str
    bytes: int
    rows: int
    chunked: bool
    columns: list[ColumnProfile]
    duplicate_rows: int | None
    notes: list[str] = field(default_factory=list)


def _stringify(value) -> str:
    """Render any scalar as a short, stable string for min/max/samples."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = str(value)
    return text[:80]


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile the raw datasets.")
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument("--file", dest="filename")
    args = parser.parse_args()

    manifest = load_manifest()
    wanted = (
        [manifest.by_id(s) for s in args.sources]
        if args.sources
        else [s for s in manifest.selected() if s.files]
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    print("OmniRetail data profiling")
    print(f"  output: {OUTPUT_DIR}")

    for source in wanted:
        target = raw_dir(manifest, source.id)
        csvs = sorted(p for p in target.glob("*.csv"))
        if args.filename:
            csvs = [p for p in csvs if p.name == args.filename]
        print(f"\n=== {source.id}: {len(csvs)} file(s)")

        for path in csvs:
            size = path.stat().st_size
            chunked = size > WHOLE_FILE_LIMIT
            encoding = "latin-1" if source.id == "dataco" else "utf-8"

            print(
                f"  {'chunked' if chunked else 'whole  '} "
                f"{path.name} ({human_bytes(size)})"
            )

            # ---------------------------------------------------------
            # Measure with pandas
            # ---------------------------------------------------------
            if chunked:
                rows = 0
                nulls: dict[str, int] = {}
                nonnull: dict[str, int] = {}
                distinct: dict[str, set] = {}
                capped: set[str] = set()
                mins: dict[str, str] = {}
                maxs: dict[str, str] = {}
                dtypes: dict[str, str] = {}

                reader = pd.read_csv(
                    path,
                    encoding=encoding,
                    chunksize=CHUNK_ROWS,
                    low_memory=False,
                    on_bad_lines="skip",
                )
                for chunk in reader:
                    rows += len(chunk)
                    for name in chunk.columns:
                        series = chunk[name]
                        dtypes.setdefault(name, str(series.dtype))
                        n_ok = int(series.notna().sum())
                        nulls[name] = nulls.get(name, 0) + int(series.isna().sum())
                        nonnull[name] = nonnull.get(name, 0) + n_ok
                        if name not in capped:
                            bucket = distinct.setdefault(name, set())
                            bucket.update(series.dropna().unique().tolist())
                            if len(bucket) >= DISTINCT_CAP:
                                capped.add(name)
                        try:
                            good = series.dropna()
                            if len(good):
                                lo, hi = _stringify(good.min()), _stringify(good.max())
                                if lo and (name not in mins or lo < mins[name]):
                                    mins[name] = lo
                                if hi and (name not in maxs or hi > maxs[name]):
                                    maxs[name] = hi
                        except (TypeError, ValueError):
                            pass

                columns = [
                    ColumnProfile(
                        name=name,
                        dtype=dtypes.get(name, "unknown"),
                        non_null=nonnull.get(name, 0),
                        nulls=nulls.get(name, 0),
                        distinct=len(distinct.get(name, ())),
                        distinct_capped=name in capped,
                        minimum=mins.get(name),
                        maximum=maxs.get(name),
                    )
                    for name in dtypes
                ]
                duplicate_rows = None
            else:
                df = pd.read_csv(
                    path, encoding=encoding, low_memory=False, on_bad_lines="skip"
                )
                rows = len(df)
                columns = []
                for name in df.columns:
                    series = df[name]
                    try:
                        good = series.dropna()
                        lo = _stringify(good.min()) if len(good) else None
                        hi = _stringify(good.max()) if len(good) else None
                    except (TypeError, ValueError):
                        lo = hi = None
                    nunique = int(series.nunique(dropna=True))
                    columns.append(
                        ColumnProfile(
                            name=str(name),
                            dtype=str(series.dtype),
                            non_null=int(series.notna().sum()),
                            nulls=int(series.isna().sum()),
                            # Reading the file whole gives an EXACT distinct
                            # count, so `distinct_capped` stays False here. The
                            # cap only exists on the chunked path, where values
                            # are accumulated across chunks and memory must stay
                            # bounded. Marking an exact number as "capped" would
                            # understate what we actually know.
                            distinct=nunique,
                            distinct_capped=False,
                            minimum=lo,
                            maximum=hi,
                            samples=[_stringify(v) for v in good.head(3)]
                            if len(good)
                            else [],
                        )
                    )
                duplicate_rows = int(df.duplicated().sum())
                del df

            profile = FileProfile(
                source_id=source.id,
                filename=path.name,
                bytes=size,
                rows=rows,
                chunked=chunked,
                columns=columns,
                duplicate_rows=duplicate_rows,
            )
            if chunked:
                profile.notes.append(
                    "Profiled in chunks; distinct counts are capped at "
                    f"{DISTINCT_CAP:,} and duplicate-row detection was skipped "
                    "(it needs the whole frame to be meaningful)."
                )

            write_markdown(profile)
            summary[f"{source.id}/{path.name}"] = {
                "source_id": source.id,
                "filename": path.name,
                "bytes": size,
                "rows": rows,
                "columns": len(columns),
                "chunked": chunked,
                "duplicate_rows": duplicate_rows,
                "notes": profile.notes,
                "column_details": [
                    {
                        "name": c.name,
                        "dtype": c.dtype,
                        "non_null": c.non_null,
                        "nulls": c.nulls,
                        "null_pct": round(c.null_pct, 4),
                        "distinct": c.distinct,
                        "distinct_capped": c.distinct_capped,
                        "min": c.minimum,
                        "max": c.maximum,
                    }
                    for c in columns
                ],
            }

    (OUTPUT_DIR / "_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {OUTPUT_DIR / '_summary.json'}")
    return 0


def write_markdown(profile: FileProfile) -> None:
    """Write one human-readable profiling report."""
    out = OUTPUT_DIR / f"{profile.source_id}__{profile.filename}.md"
    lines: list[str] = []
    lines.append(f"# Profiling: `{profile.source_id}/{profile.filename}`")
    lines.append("")
    lines.append(
        f"- **Size:** {profile.bytes:,} bytes ({human_bytes(profile.bytes)})"
    )
    lines.append(f"- **Rows:** {profile.rows:,}")
    lines.append(f"- **Columns:** {len(profile.columns)}")
    if profile.duplicate_rows is not None:
        lines.append(
            f"- **Exact duplicate rows:** {profile.duplicate_rows:,}"
            f" ({profile.duplicate_rows / profile.rows * 100:.2f}% of rows)"
            if profile.rows
            else "- **Exact duplicate rows:** 0"
        )
    lines.append(
        f"- **Method:** {'streamed in chunks' if profile.chunked else 'read whole'}"
    )
    for note in profile.notes:
        lines.append(f"- _Note:_ {note}")
    lines.append("")
    lines.append("## Columns")
    lines.append("")
    lines.append(
        "| # | Column | dtype | non-null | nulls | null % | distinct | min | max |"
    )
    lines.append("|---:|---|---|---:|---:|---:|---:|---|---|")
    for index, col in enumerate(profile.columns, start=1):
        distinct = f">={col.distinct:,}" if col.distinct_capped else f"{col.distinct:,}"
        lines.append(
            f"| {index} | `{col.name}` | {col.dtype} | {col.non_null:,} | "
            f"{col.nulls:,} | {col.null_pct:.2f}% | {distinct} | "
            f"{_md(col.minimum)} | {_md(col.maximum)} |"
        )
    lines.append("")

    worst = sorted(
        (c for c in profile.columns if c.nulls),
        key=lambda c: c.null_pct,
        reverse=True,
    )[:10]
    lines.append("## Columns with missing values (top 10 by null %)")
    lines.append("")
    if not worst:
        lines.append("None - every column is fully populated.")
    else:
        for col in worst:
            lines.append(
                f"- `{col.name}`: {col.nulls:,} nulls ({col.null_pct:.2f}%)"
            )
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")


def _md(value: str | None) -> str:
    """Escape a value for a Markdown table cell."""
    if value is None or value == "":
        return "-"
    return value.replace("|", "\\|")


if __name__ == "__main__":
    raise SystemExit(main())
