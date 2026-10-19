"""Shared ETL infrastructure: read raw, clean, write processed, record lineage.

WHY THIS FILE EXISTS
--------------------
All four sources go through the same shape of work:

    load raw file  ->  validate  ->  clean  ->  derive  ->  write processed

If each source module re-implemented that, the four stages would drift: one
would write UTF-8 and another latin-1, one would record its row deltas and
another would not. So the mechanics live here once, and each source module
contains only the part that is genuinely specific to that dataset - the columns,
the keys and the business rules.

THE TWO LAWS OF THIS STAGE
--------------------------
1. RAW IS NEVER MODIFIED. Everything here reads from ``data/raw`` and writes to
   ``data/processed``. Raw files are evidence; the moment they become an input
   that also gets written to, the evidence is gone.
2. EVERY CHANGE IS RECORDED. Each table reports rows in, rows out and a list of
   the cleaning actions applied. That list is the raw material for
   ``docs/data_lineage.md``, and it is written to ``_report.json`` next to the
   output so a number can always be traced back to what was done to it.

OUTPUT FORMAT
-------------
Processed tables are written as UTF-8 CSV with a header and no index. CSV was
chosen over Parquet for one practical reason: PostgreSQL's ``COPY`` reads it
directly, so the load stage needs no extra conversion step or extra library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from etl.registry import Manifest, Source, processed_dir, raw_dir


# ---------------------------------------------------------------------
# Lineage / cleaning report
# ---------------------------------------------------------------------
@dataclass
class TableReport:
    """What happened to one output table."""

    table: str
    rows_in: int = 0
    rows_out: int = 0
    actions: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    # True for AGGREGATE/DERIVED tables (RFM, order-level rollups). For those,
    # "rows dropped" is meaningless - one row out per many rows in is the whole
    # point of the aggregation, not data loss. Reporting it as a negative drop
    # count would look like a bug in the report itself.
    derived: bool = False
    # The raw file(s) this table was built from, declared at the point the table
    # is created. This is what makes docs/data_lineage.md GENERATED rather than
    # hand-written: the documentation reads this field, so the documented link
    # between a processed table and its raw source cannot drift from the code.
    raw_inputs: list[str] = field(default_factory=list)

    def note(self, action: str) -> None:
        """Record one cleaning or derivation decision."""
        self.actions.append(action)

    @property
    def rows_dropped(self) -> int:
        if self.derived:
            return 0
        return max(self.rows_in - self.rows_out, 0)


class EtlReport:
    """The full cleaning report for one source."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.tables: list[TableReport] = []

    def new_table(
        self,
        name: str,
        rows_in: int = 0,
        *,
        derived: bool = False,
        raw_inputs: list[str] | None = None,
    ) -> TableReport:
        report = TableReport(
            table=name,
            rows_in=rows_in,
            derived=derived,
            raw_inputs=list(raw_inputs or []),
        )
        self.tables.append(report)
        return report

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "tables": [
                {
                    "table": t.table,
                    "rows_in": t.rows_in,
                    "rows_out": t.rows_out,
                    "rows_dropped": t.rows_dropped,
                    "derived": t.derived,
                    "raw_inputs": t.raw_inputs,
                    "columns": t.columns,
                    "actions": t.actions,
                }
                for t in self.tables
            ],
        }

    def summarise(self, source_id: str) -> None:
        """Print a compact table of what happened."""
        print(f"\n  {'table':<28} {'rows in':>12} {'rows out':>12} {'dropped':>10}")
        print(f"  {'-' * 28} {'-' * 12} {'-' * 12} {'-' * 10}")
        for t in self.tables:
            dropped = "n/a (derived)" if t.derived else f"{t.rows_dropped:,}"
            print(
                f"  {t.table:<28} {t.rows_in:>12,} {t.rows_out:>12,} "
                f"{dropped:>16}"
            )


# ---------------------------------------------------------------------
# Reading raw data
# ---------------------------------------------------------------------
def read_raw(
    manifest: Manifest,
    source: Source,
    filename: str,
    **kwargs,
) -> pd.DataFrame:
    """Read one raw file, defaulting to the encoding the manifest specifies.

    We never guess the encoding. DataCo ships latin-1 bytes that are not valid
    UTF-8, and silently decoding them as UTF-8 would corrupt accented text;
    the manifest records the correct encoding per file, so it is applied here
    for every reader rather than remembered per call site.
    """
    encoding = "utf-8"
    for remote in source.files:
        if remote.filename == filename:
            encoding = remote.encoding
            break

    path = raw_dir(manifest, source.id) / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run backend/scripts/data/01_download.py first."
        )

    options = {"encoding": encoding, "low_memory": False, "on_bad_lines": "skip"}
    options.update(kwargs)
    return pd.read_csv(path, **options)


def read_raw_chunked(
    manifest: Manifest,
    source: Source,
    filename: str,
    chunksize: int,
    **kwargs,
):
    """Iterate a raw file in chunks - for files too large to load whole."""
    encoding = "utf-8"
    for remote in source.files:
        if remote.filename == filename:
            encoding = remote.encoding
            break

    path = raw_dir(manifest, source.id) / filename
    options = {"encoding": encoding, "low_memory": False, "on_bad_lines": "skip"}
    options.update(kwargs)
    return pd.read_csv(path, chunksize=chunksize, **options)


# ---------------------------------------------------------------------
# Writing processed data
# ---------------------------------------------------------------------
def write_processed(
    manifest: Manifest,
    source_id: str,
    table: str,
    frame: pd.DataFrame,
    report: TableReport,
) -> Path:
    """Write one processed table and finalise its report entry.

    ``lineterminator="\n"`` is not cosmetic. pandas defaults to the platform
    line separator, which on Windows is CRLF. PostgreSQL's COPY ... FORMAT csv
    rejects a bare carriage return ("unquoted carriage return found in data"),
    so a CRLF file cannot be loaded. Pinning LF makes the processed data
    byte-identical on every operating system, which is what a data artefact
    should be - the machine you ran the ETL on must not change the bytes.
    """
    destination = processed_dir(manifest, source_id) / f"{table}.csv"
    frame.to_csv(
        destination, index=False, encoding="utf-8", lineterminator="\n"
    )
    report.table = table
    report.rows_out = len(frame)
    report.columns = [str(c) for c in frame.columns]
    print(f"    wrote {table:<28} {len(frame):>12,} rows")
    return destination


def write_processed_chunked(
    manifest: Manifest,
    source_id: str,
    table: str,
    chunks,
    report: TableReport,
    *,
    first_chunk_has_header: bool = True,
) -> Path:
    """Write a table produced as a stream of chunks.

    Used for outputs that are too large to hold in memory (the M5 melt produces
    roughly 59 million rows). Each chunk is appended to the file rather than
    accumulated, so peak memory stays flat.
    """
    destination = processed_dir(manifest, source_id) / f"{table}.csv"
    rows = 0
    header_written = False
    columns: list[str] = []

    # newline="" stops Python from translating "\n" into the platform
    # separator, and lineterminator="\n" stops pandas from doing it too. Both
    # are needed: the first controls the file object, the second controls what
    # pandas hands to it. See write_processed for why LF is mandatory here.
    with destination.open("w", encoding="utf-8", newline="") as handle:
        for chunk in chunks:
            if not header_written:
                columns = [str(c) for c in chunk.columns]
                handle.write(",".join(columns) + "\n")
                header_written = True
            chunk.to_csv(handle, index=False, header=False, lineterminator="\n")
            rows += len(chunk)

    report.table = table
    report.rows_out = rows
    report.columns = columns
    # A streamed write keeps every row, so rows_in equals rows_out. Setting it
    # here means the report does not claim "36,786,524 rows dropped" simply
    # because the count was unknown before the stream started.
    if report.rows_in == 0:
        report.rows_in = rows
    print(f"    wrote {table:<28} {rows:>12,} rows (streamed)")
    return destination


def write_report(manifest: Manifest, report: EtlReport) -> Path:
    """Persist the cleaning/lineage report next to the processed data."""
    destination = processed_dir(manifest, report.source_id) / "_report.json"
    destination.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"    report -> {destination.name}")
    return destination


# ---------------------------------------------------------------------
# Common cleaning operations
# ---------------------------------------------------------------------
def to_snake_case(name: str) -> str:
    """``"Order Item Total"`` -> ``"order_item_total"``.

    Raw data keeps whatever the publisher shipped; the analytics layer uses one
    naming convention. This is the single place that conversion is defined, so
    two tables can never disagree about how to spell the same column.
    """
    cleaned = (
        str(name)
        .strip()
        .replace("(", " ")
        .replace(")", " ")
        .replace("%", " pct ")
    )
    for char in "-/\\.":
        cleaned = cleaned.replace(char, " ")
    parts = [p for p in cleaned.split() if p]
    return "_".join(parts).lower()


def parse_datetimes(series: pd.Series, fmt: str | None = None) -> pd.Series:
    """Parse a date column, tolerating unparseable values.

    ``errors="coerce"`` turns bad values into ``NaT`` rather than raising. That
    is deliberate but must always be paired with a count of what turned into
    NaT, so a broken format shows up as a number in the report instead of
    silently becoming missing data.
    """
    return pd.to_datetime(series, format=fmt, errors="coerce")


def drop_all_null_columns(
    frame: pd.DataFrame, report: TableReport, *, keep: set[str] | None = None
) -> pd.DataFrame:
    """Remove columns that are entirely empty.

    A 100%-null column is not data, it is a placeholder. Carrying it into
    PostgreSQL would create a column nothing can ever populate. The name of
    every dropped column is recorded, because "this field does not exist in the
    source" is itself a finding worth keeping.
    """
    keep = keep or set()
    empty = [
        column
        for column in frame.columns
        if column not in keep and frame[column].isna().all()
    ]
    if empty:
        report.note(
            "dropped 100%-null columns (no data exists in the source): "
            + ", ".join(sorted(str(c) for c in empty))
        )
        frame = frame.drop(columns=empty)
    return frame


def drop_constant_columns(
    frame: pd.DataFrame, report: TableReport, *, keep: set[str] | None = None
) -> pd.DataFrame:
    """Remove columns with a single distinct value.

    A column whose every row is identical carries no information and cannot
    vary in any analysis. Keeping it invites a query that groups by it and
    returns one meaningless row. Recorded, never silently removed.
    """
    keep = keep or set()
    constant = [
        column
        for column in frame.columns
        if column not in keep and frame[column].nunique(dropna=False) <= 1
    ]
    if constant:
        details = ", ".join(
            f"{c} (always {frame[c].dropna().iloc[0]!r})" if frame[c].notna().any()
            else f"{c} (always empty)"
            for c in constant
        )
        report.note(f"dropped constant columns (single distinct value): {details}")
        frame = frame.drop(columns=constant)
    return frame


def deduplicate(
    frame: pd.DataFrame,
    report: TableReport,
    subset: list[str] | None = None,
    *,
    keep: str = "first",
) -> pd.DataFrame:
    """Drop duplicate rows, recording how many were removed and on what key."""
    before = len(frame)
    frame = frame.drop_duplicates(subset=subset, keep=keep)
    removed = before - len(frame)
    if removed:
        key = ", ".join(subset) if subset else "all columns"
        report.note(f"removed {removed:,} duplicate rows on ({key})")
    return frame