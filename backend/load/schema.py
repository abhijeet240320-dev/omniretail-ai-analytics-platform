"""Infer PostgreSQL column types from the processed CSVs.

WHAT THIS MODULE IS STILL FOR
-----------------------------
The loader no longer builds tables - Alembic does - so ``create_table_sql`` is
gone. But the TYPE RULES live here, and they are still load-bearing, because
they are the reason the types in the baseline migration are the types they are.
``test_migration_types_match_the_documented_rules`` re-runs this inference
against the real CSVs and asserts it agrees with what Alembic declared, so the
rules cannot drift away from the schema without a test failing.

WHY INFERENCE RATHER THAN A HAND-WRITTEN DDL FILE
-------------------------------------------------
There are 30 tables and roughly 250 columns. A hand-written DDL file would be
250 lines that must be kept in step with four ETL modules by hand, and the first
time a column is renamed the two disagree.

So the column list comes from the CSV header - the actual artefact the ETL wrote
- and the types are inferred from the data plus a small, explicit rule set. Any
inference that would be wrong is overridden in ``db_constraints.yaml``, and those
overrides are visible and reviewable rather than spread across 250 hand-typed
lines.

The money rule deserves a note. Values like ``sales``, ``price`` and ``profit``
are stored as NUMERIC rather than DOUBLE PRECISION. Binary floating point cannot
represent 0.10 exactly, and a rounding error of one part in 10^16 is harmless in
one row but accumulates across millions of rows into a visibly wrong total. For
an analytics platform whose whole premise is "prove the number", money is
NUMERIC.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS_PATH = REPO_ROOT / "backend" / "config" / "db_constraints.yaml"


@dataclass(frozen=True)
class Column:
    """One inferred output column."""

    name: str
    pg_type: str
    reason: str


def load_constraints() -> dict:
    """Read the candidate-key spec and its type rules."""
    return yaml.safe_load(CONSTRAINTS_PATH.read_text(encoding="utf-8"))


def _sample_values(path: Path, column: str, limit: int = 2000) -> list[str]:
    """Read a bounded sample of one column's raw text values.

    Sampling rather than loading the file keeps this fast on ``sales_long.csv``
    (4.5 GB). The sample size is deliberately generous enough to catch
    booleans, dates and nulls, which is all the inference needs - the definitive
    type check happens when PostgreSQL accepts or rejects the COPY.
    """
    values: list[str] = []
    with path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return values
        header = [h.lstrip("\ufeff") for h in header]
        if column not in header:
            return values
        index = header.index(column)
        for row in reader:
            if len(row) > index:
                values.append(row[index])
            if len(values) >= limit:
                break
    return values


def infer_type(
    column: str,
    samples: list[str],
    rules: dict,
    override: str | None,
    *,
    qualified: str,
) -> Column:
    """Infer one column's PostgreSQL type and record WHY."""
    if override:
        return Column(column, override, "explicit override in db_constraints.yaml")

    present = [v.strip() for v in samples if v.strip() != ""]
    if not present:
        # Every sampled value was empty. TEXT is the safe choice: a column that
        # is empty in the first 2,000 rows could still hold text later, and
        # guessing a numeric type would risk a failed COPY.
        return Column(column, rules["text_type"], "no non-empty sample; defaulting to text")

    lowered = {v.lower() for v in present}
    if lowered <= {"true", "false", "1", "0"} and any(
        column.startswith(p) for p in rules["boolean_name_prefixes"]
    ):
        return Column(column, rules["boolean_type"], "is_/has_ column holding only true/false")
    if lowered <= {"true", "false"}:
        return Column(column, rules["boolean_type"], "only true/false values")

    def is_int(values: list[str]) -> bool:
        try:
            for value in values:
                int(value)
            return True
        except ValueError:
            return False

    def is_float(values: list[str]) -> bool:
        try:
            for value in values:
                float(value)
            return True
        except ValueError:
            return False

    if is_int(present):
        return Column(column, rules["integer_types"][0], "all sampled values are integers")

    if is_float(present):
        # Money-shaped names become NUMERIC; everything else is a measurement
        # (days, ratios, latitudes) and DOUBLE PRECISION is appropriate.
        for pattern in rules["money_name_patterns"]:
            if pattern in column.lower():
                return Column(
                    column,
                    rules["money_type"],
                    f"float column matching money pattern {pattern!r}",
                )
        return Column(column, rules["float_type"], "all sampled values are numeric")

    # Timestamps: ISO-ish. We only claim a timestamp when the sample is
    # consistently shaped, otherwise text is the honest answer.
    timestampish = all(
        len(v) >= 10 and v[4] == "-" and v[7] == "-" for v in present
    )
    if timestampish and any(
        hint in column.lower() for hint in rules["timestamp_name_hints"]
    ):
        return Column(
            column, rules["timestamp_type"], "ISO-shaped values and a date/time column name"
        )

    return Column(column, rules["text_type"], "values are not uniformly numeric or temporal")


def table_columns(
    csv_path: Path, rules: dict, overrides: dict, schema: str, table: str
) -> list[Column]:
    """Infer the full column list for one processed table."""
    with csv_path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return []
    header = [h.lstrip("\ufeff").strip() for h in header]

    columns: list[Column] = []
    for name in header:
        qualified = f"{schema}.{table}.{name}"
        columns.append(
            infer_type(
                name,
                _sample_values(csv_path, name),
                rules,
                overrides.get(qualified),
                qualified=qualified,
            )
        )
    return columns


def csv_header(path: Path) -> list[str]:
    """The first row of a processed CSV: its column names, in file order.

    Cheap by design - it opens the file and reads one line, which matters
    because the largest CSV here is 4.5 GB. The loader uses this to check the
    file against the table Alembic created; a mismatch is drift, and it should
    be reported as drift rather than quietly resolved by rebuilding the schema.
    """
    with path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            return next(reader)
        except StopIteration:
            return []


def strip_quotes(identifier: str) -> str:
    """Remove surrounding double quotes from a YAML-declared identifier."""
    return identifier.strip().strip('"')
