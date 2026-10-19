"""Phase 6: load the processed data into PostgreSQL and verify the real constraints.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe -m alembic upgrade head     # schema FIRST
    .venv/Scripts/python.exe backend/scripts/db/04_load.py
    .venv/Scripts/python.exe backend/scripts/db/04_load.py --source olist
    .venv/Scripts/python.exe backend/scripts/db/04_load.py --source m5 --table calendar
    .venv/Scripts/python.exe backend/scripts/db/04_load.py --resume
    .venv/Scripts/python.exe backend/scripts/db/04_load.py --verify-only

THE OWNERSHIP SPLIT, AND WHY IT IS THIS WAY
-------------------------------------------
    bootstrap_db.py  owns  the database, the roles, the GRANTs
    Alembic          owns  schemas, tables, columns, PK/UQ/FK constraints
    this loader      owns  the ROWS, and the evidence that the keys hold

Until the Alembic baseline landed, this script did all three jobs: it dropped
and recreated every table, then ALTER TABLE ADD CONSTRAINT for each key that
tested true. Two scripts creating the same schema means they will eventually
disagree, and there is no way to tell which one the database matches. So the
loader no longer issues a single DDL statement. It refuses to run if the schema
is missing, empties the table with TRUNCATE, COPYs the data in, and then
MEASURES the constraints the migration declared.

WHAT "VERIFY" MEANS HERE, PRECISELY
-----------------------------------
The constraints in ``db_constraints.yaml`` are the SPEC: what was proposed.
``pg_constraint`` is the FACT: what Alembic created. This script tests the facts
against the rows that were just loaded and records the verdict.

That is not busywork. It means "29 primary keys hold" is a statement about the
data as it is right now, not a claim inherited from whichever build first ran
them. And it is what keeps ``dunnhumby.coupon_redemptions`` honest: the migration
correctly declares no primary key, and the candidate
(household_key, day, coupon_upc) is still tested against the real rows so the
rejection stays recorded with live numbers rather than a memory.

Nothing is deleted to make a constraint pass. Not then, not now.

THE ORDER OF OPERATIONS, AND WHY IT IS NOT CIRCULAR
---------------------------------------------------
The keys were chosen by examining the data, so "Alembic creates the key, then the
loader checks the key" looks circular. It is not: the migration is a frozen
artifact under version control, checked into git and reviewed. Re-running the
measurement against 106 million freshly loaded rows is what detects the day the
data stops matching that artifact. A circular check would be regenerating the
migration from the data on every load; this does the opposite and compares.

WHAT IT DOES, PER TABLE
-----------------------
    1. confirm the table exists - Alembic's job, not this script's
    2. check the CSV header against the table's columns; a mismatch is drift
    3. TRUNCATE, then COPY     (or skip both under --resume / --verify-only)
    4. TEST every declared PK / UQ / FK against the real rows
    5. TEST each rejected candidate key so its rejection stays on record

Then it writes ``data/processed/_load_report.json`` so the database section of
the documentation can be generated from measured facts rather than from prose.

That report describes the WHOLE DATABASE, not just the tables this invocation
happened to touch. A scoped run (``--source m5``, ``--table calendar``) merges
into the previous report instead of overwriting it, keyed by table and by
constraint. Without the merge, fixing one candidate key would silently delete the
recorded evidence for the other 29 tables. Constraints are keyed by
``(kind, table, columns)`` with the newest result winning, so a key that was
rejected by the data on one run and accepted on a later, corrected run is
recorded as accepted rather than leaving both verdicts behind.

WHY --resume AND --verify-only EXIST
------------------------------------
Step 3 is the expensive part: ``m5.sales_long`` alone is a 4.5 GB COPY of
59 million rows. If the process is interrupted the COPY is rolled back in full,
and a plain re-run starts that from zero.

``--resume`` compares each table's live row count against the row count the ETL
recorded in its own report, and skips the COPY when they already agree.

``--verify-only`` is stricter: it never copies at all. It re-measures every
constraint against the rows already in the database, which is the cheapest
possible way to answer "do the keys still hold?" - seconds rather than an hour.

Both run the key tests. They make the check cheap; they do not make the evidence
weaker.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from load.loader import (  # noqa: E402
    KeyResult,
    LoadReport,
    alembic_revision,
    connect,
    copy_csv,
    declared_constraints,
    foreign_key_name,
    primary_key_name,
    table_columns_from_db,
    table_exists,
    table_row_count,
    test_foreign_key,
    test_primary_key,
    truncate_table,
    unique_name,
)
from load.schema import (  # noqa: E402
    csv_header,
    load_constraints,
)


def expected_rows(source_id: str, table: str) -> int | None:
    """How many rows the ETL says this table should hold.

    Read from the ETL's own report rather than counted from the CSV, so the
    resume check compares the database against the number the pipeline
    *claimed* to produce. If the ETL under-delivered, the counts disagree and
    the table is reloaded - which is the behaviour we want.
    """
    path = REPO_ROOT / "data" / "processed" / source_id / "_report.json"
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    for entry in report.get("tables", []):
        if entry.get("table") == table:
            return int(entry.get("rows_out", 0))
    return None


def check_schema_matches_csv(
    columns: list, csv_path: Path, source_id: str, table: str
) -> None:
    """Fail loudly when the file and the table disagree.

    Previously the loader inferred the table from the file, so a renamed or
    added column silently became a schema change. Now Alembic owns the table and
    this comparison is the only thing standing between a drifted CSV and a COPY
    that puts the wrong values in the wrong columns - or aborts halfway through
    a 4.5 GB file.

    Order is deliberately NOT compared: ``\\copy`` addresses columns by name, so
    file order is irrelevant, and demanding it would fail on a harmless ETL
    reordering. Only the SET of names has to agree.
    """
    header = csv_header(csv_path)
    if not header:
        return
    in_table = {column.name for column in columns}
    in_file = set(header)
    missing = sorted(in_table - in_file)
    extra = sorted(in_file - in_table)
    if not missing and not extra:
        return
    parts = []
    if missing:
        parts.append(f"table has columns the CSV does not: {missing}")
    if extra:
        parts.append(f"CSV has columns the table does not: {extra}")
    raise SystemExit(
        f"{source_id}.{table}: the processed CSV and the table disagree - "
        + "; ".join(parts)
        + ".\n  The table comes from the Alembic baseline; the CSV comes from the ETL.\n"
        "  Re-run the ETL, or write a migration. Do not let the loader reconcile "
        "them, because one of the two is wrong and it is not this script's job to "
        "guess which."
    )


def load_one(
    conn,
    report: LoadReport,
    constraints: dict,
    source_id: str,
    table: str,
    csv_path: Path,
    resume: bool = False,
    verify_only: bool = False,
) -> None:
    """Load one table's rows, then measure the constraints Alembic declared.

    Raises SystemExit if the table does not exist. That is not a crash: it is
    the loader declining to do work that is no longer its job, with the fix in
    the message.
    """
    spec = constraints.get("tables", {}).get(source_id, {}).get(table, {})

    # --- 1. The schema is Alembic's. Confirm it exists. ---------------
    if not table_exists(conn, source_id, table):
        raise SystemExit(
            f"{source_id}.{table}: no such table.\n"
            "  This loader no longer creates tables - Alembic does. Run:\n"
            "      .venv/Scripts/python.exe -m alembic upgrade head"
        )

    columns = table_columns_from_db(conn, source_id, table)
    if not columns:
        raise SystemExit(f"{source_id}.{table}: the table exists but declares no columns.")
    check_schema_matches_csv(columns, csv_path, source_id, table)

    # --- 2. Resume / verify-only: is a COPY needed at all? -------------
    wanted = expected_rows(source_id, table)
    present = table_row_count(conn, source_id, table)
    rows_already_right = (
        present is not None and wanted is not None and present == wanted
    )
    if verify_only:
        seconds = 0.0
        report.loaded.append((f"{source_id}.{table}", present or 0, seconds))
        report.methods[f"{source_id}.{table}"] = "verified in place, no COPY"
        report.reused.append(f"{source_id}.{table}")
        print(f"  {source_id}.{table}: {present:,} rows, verifying in place")
    elif resume and rows_already_right:
        seconds = 0.0
        report.loaded.append((f"{source_id}.{table}", present, seconds))
        report.methods[f"{source_id}.{table}"] = "reused, no COPY"
        report.reused.append(f"{source_id}.{table}")
        print(
            f"  {source_id}.{table}: reusing existing rows "
            f"({present:,} matches the ETL report), skipping COPY"
        )
    else:
        # --- 3. TRUNCATE, then COPY. Structure is untouched. ----------
        truncate_table(conn, source_id, table)
        seconds, method = copy_csv(conn, source_id, table, csv_path, columns)
        rows = table_row_count(conn, source_id, table)
        report.loaded.append((f"{source_id}.{table}", rows, seconds))
        report.methods[f"{source_id}.{table}"] = method
        print(
            f"  {source_id}.{table}: {len(columns)} columns declared by Alembic, "
            f"{rows:>13,} rows loaded in {seconds:>6.1f}s via {method}"
        )

    # --- 4. VERIFY the declared constraints against the real rows -----
    # This is the step that used to CREATE them. Now it asks a different and
    # better question: are the constraints the migration declared actually
    # satisfied by the data that was just loaded?
    declared = declared_constraints(conn, source_id, table)

    for entry in declared["primary key"]:
        result = test_primary_key(conn, source_id, table, entry["columns"])
        report.keys.append(result)
        state = "holds" if result.passed else "DOES NOT HOLD"
        print(
            f"        PK {entry['name']} on ({', '.join(entry['columns'])}): {state}"
            + ("" if result.passed else f" - {result.detail}")
        )

    for entry in declared["unique"]:
        result = test_primary_key(conn, source_id, table, entry["columns"])
        result.kind = "unique"
        report.keys.append(result)
        state = "holds" if result.passed else "DOES NOT HOLD"
        print(
            f"        UNIQUE {entry['name']} on ({', '.join(entry['columns'])}): {state}"
            + ("" if result.passed else f" - {result.detail}")
        )

    for entry in declared["foreign key"]:
        ref = entry.get("ref_table", "")
        ref_columns = entry.get("ref_columns", [])
        ref_schema = ref.split(".")[0] if "." in ref else source_id
        if ref_schema != source_id:
            # test_foreign_key joins within one schema. A cross-schema reference
            # would silently test the WRONG parent table, so refuse rather than
            # report a number about something other than the constraint.
            raise SystemExit(
                f"{source_id}.{table}: constraint {entry['name']} references "
                f"{ref}, a different schema. This project forbids cross-source "
                "joins; fix the migration rather than skipping the test."
            )
        result = test_foreign_key(
            conn, source_id, table, entry["columns"], ref.split(".")[-1], ref_columns
        )
        report.keys.append(result)
        state = "holds" if result.passed else "DOES NOT HOLD"
        print(
            f"        FK {entry['name']} ({', '.join(entry['columns'])}) -> "
            f"{ref}({', '.join(ref_columns)}): {state}"
            + ("" if result.passed else f" - {result.detail}")
        )

    # --- 5. Re-test the candidate keys, so rejections stay on record ---
    # dunnhumby.coupon_redemptions has no declared primary key because its
    # candidate was rejected. If that rejection is not re-measured, it becomes a
    # story someone told once, and the next person to look sees a table with no
    # key and no reason. These tests are why the reason has live numbers.
    declared_pk_columns = {tuple(e["columns"]) for e in declared["primary key"]}
    candidate_pk = spec.get("candidate_pk")
    if candidate_pk and tuple(candidate_pk) not in declared_pk_columns:
        result = test_primary_key(conn, source_id, table, candidate_pk)
        report.keys.append(result)
        if result.passed:
            # The data now supports a key the migration does not declare. That is
            # a finding, not a success to paper over: the migration needs a new
            # revision, and silently accepting it here would hide that.
            report.skipped.append(
                f"{source_id}.{table}: candidate key ({', '.join(candidate_pk)}) now "
                "holds but is NOT declared by the migration. Alembic is out of date - "
                "write a revision."
            )
            print(
                f"        CANDIDATE PK ({', '.join(candidate_pk)}) holds but is not "
                "declared by the migration - write an Alembic revision"
            )
        else:
            print(f"        candidate PK rejected again - {result.detail}")

    for fk in spec.get("candidate_fks", []):
        name = foreign_key_name(source_id, table, fk["columns"])
        if any(e["name"] == name for e in declared["foreign key"]):
            continue  # already verified in step 4, from the catalog
        result = test_foreign_key(
            conn, source_id, table, fk["columns"], fk["ref_table"], fk["ref_columns"]
        )
        report.keys.append(result)
        if result.passed:
            report.skipped.append(
                f"{source_id}.{table}: candidate FK ({', '.join(fk['columns'])}) -> "
                f"{fk['ref_table']} holds but is NOT declared by the migration. "
                "Alembic is out of date - write a revision."
            )
            print(
                f"        CANDIDATE FK ({', '.join(fk['columns'])}) holds but is not "
                "declared by the migration"
            )


def key_record(k: KeyResult) -> dict:
    """A constraint verdict, as it is recorded in the load report."""
    return {
        "kind": k.kind,
        "table": k.table,
        "columns": list(k.columns),
        "detail": k.detail,
        "action": k.action,
    }


def alembic_head() -> str | None:
    """The revision Alembic's on-disk history ends at, without touching the database.

    Read from the ScriptDirectory so the loader can say "the database is stamped
    at X, the code expects Y" instead of just noticing that something is wrong.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = REPO_ROOT / "alembic.ini"
    if not ini.exists():
        return None
    config = Config(str(ini))
    config.set_main_option("script_location", str(REPO_ROOT / "backend" / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    return heads[0] if heads else None


def key_identity(record: dict) -> tuple:
    """What makes two constraint records 'the same constraint'.

    A primary key is singular per table, so two primary-key verdicts on the same
    table supersede each other even when the columns differ. That matters when a
    wrong key is corrected: once `olist.reviews` holds a PK on
    (review_id, order_id), a stale 'rejected: review_id' line would otherwise
    survive in the report and claim the table has both.
    """
    if record["kind"] == "primary key":
        return (record["kind"], record["table"])
    return (record["kind"], record["table"], tuple(record["columns"]))


def write_report(report, elapsed: float, args, alembic_rev: str | None) -> Path:
    """Write _load_report.json describing the whole database, merging this run in.

    A scoped run must not erase the record of the tables it did not touch, so
    tables and constraints are merged into any previous report by name, newest
    verdict winning. Without this, correcting one candidate key would silently
    delete the evidence for the other tables.

    BUCKET NAMES CHANGED, AND THE OLD ONES ARE GONE
    -------------------------------------------------
    This used to publish ``constraints_added`` and
    ``constraints_already_present``. Both described work the loader no longer
    does - it never creates a constraint - so keeping them would have left a
    report claiming an action that does not happen. They are replaced by
    ``constraints_verified``, which is the honest description of what a
    post-Alembic run measures: every constraint Alembic declared, re-tested
    against the rows that are in the table.

    The old names are not read as fallbacks anywhere. A report that silently
    rendered stale vocabulary would be worse than one that fails to regenerate.
    """
    destination = REPO_ROOT / "data" / "processed" / "_load_report.json"

    previous: dict = {}
    if destination.exists():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            previous = {}

    # tables: keyed by name so a re-measured table replaces its earlier entry
    merged_tables: dict[str, dict] = {t["table"]: t for t in previous.get("tables", [])}
    for name, rows, seconds in report.loaded:
        merged_tables[name] = {"table": name, "rows": rows, "seconds": round(seconds, 2)}
    tables = [merged_tables[name] for name in sorted(merged_tables)]

    # constraints: keyed by identity so a later verdict supersedes an earlier one
    buckets = {
        "constraints_verified": list(previous.get("constraints_verified", [])),
        "constraints_rejected": list(previous.get("constraints_rejected", [])),
    }
    for bucket, results in (
        ("constraints_verified", report.verified),
        ("constraints_rejected", report.rejected),
    ):
        for record in (key_record(k) for k in results):
            identity = key_identity(record)
            # A constraint can only be in one bucket. If this run moved it
            # (e.g. a rejected key corrected into an accepted one) the stale
            # verdict from the earlier run has to go, or the report would claim
            # both that a key holds and that the data refused it.
            for other in buckets:
                buckets[other] = [
                    r for r in buckets[other] if key_identity(r) != identity
                ]
            buckets[bucket].append(record)

    methods: dict[str, str] = dict(previous.get("copy_methods", {}))
    methods.update(report.methods)

    scope = {"sources": sorted(args.sources) if args.sources else None, "table": args.table}
    runs = list(previous.get("runs", []))
    runs.append(
        {
            "scope": scope,
            "resumed": bool(args.resume),
            "verify_only": bool(args.verify_only),
            "alembic_revision": alembic_rev,
            "elapsed_seconds": round(elapsed, 1),
            "tables": len(report.loaded),
            "tables_reused": sorted(report.reused),
        }
    )

    destination.write_text(
        json.dumps(
            {
                # The whole-database state: summed over every run that contributed
                "elapsed_seconds": round(sum(r["elapsed_seconds"] for r in runs), 1),
                "tables": tables,
                "total_rows": sum(t["rows"] for t in tables),
                "copy_methods": methods,
                # The schema this report describes, so a reader can tell which
                # revision the constraint verdicts belong to.
                "alembic_revision": alembic_rev,
                **buckets,
                "skipped": report.skipped,
                # Per-run provenance: what each invocation actually did
                "last_run": runs[-1],
                "runs": runs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"report -> {destination}")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Load processed data into PostgreSQL.")
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument("--table", dest="table", help="only this table (with --source)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip the COPY for any table that already holds exactly the number "
        "of rows the ETL reported. Key tests still run, so the report is "
        "measured fresh. Use this after an interrupted load instead of "
        "starting the 4.5 GB copy again.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="never COPY or TRUNCATE. Re-measure every declared constraint "
        "against the rows already in the database. This is the cheapest way to "
        "answer 'do the keys still hold?' - seconds rather than an hour - and it "
        "cannot destroy data by construction.",
    )
    args = parser.parse_args()

    constraints = load_constraints()
    load_order = constraints["load_order"]

    sources = args.sources or list(load_order.keys())
    report = LoadReport()

    print("OmniRetail load - processed CSV -> PostgreSQL")
    print(f"  target: {get_settings_label()}")
    if args.verify_only:
        print("  mode:   --verify-only (no COPY, no TRUNCATE; re-measure keys)")

    head = alembic_head()
    started = time.monotonic()
    alembic_rev: str | None = None
    with connect() as conn:
        stamped = alembic_revision(conn)
        alembic_rev = stamped
        print(f"  alembic: database stamped at {stamped!r}, code head is {head!r}")
        if stamped is None:
            raise SystemExit(
                "This database has no alembic_version row, so the schema was not "
                "built by Alembic.\n"
                "  The loader no longer creates tables, and it will not guess at one. "
                "Either:\n"
                "    .venv/Scripts/python.exe -m alembic stamp head   "
                "# if the tables are already correct\n"
                "  or, for a genuinely empty database:\n"
                "    .venv/Scripts/python.exe -m alembic upgrade head"
            )
        if head and stamped != head:
            raise SystemExit(
                f"The database is stamped at {stamped!r} but the code's head is "
                f"{head!r}.\n"
                "  Run: .venv/Scripts/python.exe -m alembic upgrade head"
            )

        for source_id in sources:
            tables = load_order.get(source_id, [])
            if args.table:
                tables = [t for t in tables if t == args.table]

            print(f"\n=== {source_id} ({len(tables)} table(s))")
            for table in tables:
                csv_path = REPO_ROOT / "data" / "processed" / source_id / f"{table}.csv"
                if not csv_path.exists():
                    report.skipped.append(f"{source_id}.{table}: missing {csv_path.name}")
                    print(f"  SKIP  {source_id}.{table} (no CSV)")
                    continue
                try:
                    load_one(
                        conn, report, constraints, source_id, table, csv_path,
                        resume=args.resume, verify_only=args.verify_only,
                    )
                except SystemExit:
                    # A missing table or a CSV/schema mismatch is not a per-table
                    # hiccup to log and continue past; it means the schema and the
                    # pipeline disagree about the whole project. Stopping is the
                    # honest response.
                    raise
                except Exception:  # noqa: BLE001 - report and continue
                    print(f"  FAIL  {source_id}.{table}")
                    traceback.print_exc()
                    report.skipped.append(f"{source_id}.{table}: load failed")
                    # A failed statement leaves the transaction ABORTED, and
                    # every later command then fails with "current transaction
                    # is aborted" instead of its own real error. Rolling back
                    # here is what stops one table's failure from cascading
                    # into a screenful of misleading failures for every table
                    # after it - which is exactly what happened before this
                    # line existed.
                    conn.rollback()

    elapsed = time.monotonic() - started

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    total_rows = sum(r for _, r, _ in report.loaded)
    print(f"\n{'=' * 70}")
    print(f"loaded {len(report.loaded)} table(s), {total_rows:,} rows in {elapsed:,.0f}s")
    if report.reused:
        why = "verified in place" if args.verify_only else "reused, no COPY"
        print(f"rows reused ({why}): {len(report.reused)} table(s)")
    print(f"schema owner: Alembic revision {alembic_rev!r} (this loader changed no DDL)")
    print(f"constraints verified against the live rows: {len(report.holding)}")
    print(f"constraints rejected by the data          : {len(report.rejected)}")
    if report.rejected:
        print("\nrejected candidates (documented, NOT worked around):")
        for result in report.rejected:
            print(f"  - {result.table} ({result.kind} on {result.columns}): {result.detail}")
    if report.skipped:
        print("\nskipped:")
        for note in report.skipped:
            print(f"  - {note}")
    print("=" * 70)

    # A report is evidence. Writing one from a run that failed would replace a
    # complete set of measurements with a partial one, and the merge would make
    # that invisible: the tables that succeeded look exactly like tables that
    # were skipped. Failing loudly is the only way the report stays trustworthy.
    failed = [note for note in report.skipped if note.endswith("load failed")]
    if failed:
        print(
            f"\nNOT writing _load_report.json: {len(failed)} table(s) failed. "
            "The previous report is still valid evidence; this run's partial "
            "results are not."
        )
        return 1

    write_report(report, elapsed, args, alembic_rev)
    return 0


def get_settings_label() -> str:
    from app.core.config import get_settings

    settings = get_settings()
    return (
        f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db} "
        f"as {settings.postgres_user}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
