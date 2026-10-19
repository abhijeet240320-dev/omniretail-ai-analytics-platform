"""Phase 6 verification: prove the loaded database is internally consistent.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/db/05_verify_integrity.py
    .venv/Scripts/python.exe backend/scripts/db/05_verify_integrity.py --skip-catalog

WHY THIS IS A SEPARATE SCRIPT
-----------------------------
``04_load.py`` adds a constraint only after it has tested the data. That makes
04_load.py the thing that *claims* the database is consistent. This script is
the independent check: it re-derives every claim from the rows that are now in
PostgreSQL, and it deliberately does not trust the catalog's own opinion that a
constraint is valid.

The distinction matters. ``pg_constraint`` containing a foreign key means
PostgreSQL accepted the ALTER TABLE, which means it checked the rows at that
moment. It does not mean anybody has confirmed it is still true, that it was
the right join, or that it survived the later table reload. Here we actually
re-run the orphan query and the duplicate query. If the loader lied, or a
constraint was pointed at the wrong column, this is where it shows up.

WHAT IT CHECKS
--------------
  1. RECONCILIATION - every table's live row count equals the number the ETL
     said it wrote. A load that silently dropped a third of a table would still
     look healthy to every other tool in this project.
  2. CONSTRAINT INVENTORY - what the database actually enforces, read from
     pg_constraint, with the check status PostgreSQL recorded.
  3. REFERENTIAL INTEGRITY - for every declared foreign key, an actual orphan
     query. Must be zero, counted by us, not by the constraint.
  4. UNIQUENESS - for every primary key and unique constraint, an actual
     duplicate query. Must be zero.
  5. REJECTED CANDIDATE KEYS - the keys we declined to add are re-measured and
     reported with exact counts, so the documentation states the real number
     rather than a remembered one.
  6. NO CROSS-SOURCE FOREIGN KEYS - the structural guarantee behind the
     project's hardest rule. Every foreign key must point at a table in its own
     schema. A FK from ``olist.orders`` into ``dataco.customers`` would be a
     silent assertion that dunnhumby household 123 and DataCo customer 123 are
     the same person. There is no such relationship in the real world, so it
     must not be possible to encode it here.
  7. ROW-LEVEL SPOT CHECKS - a handful of hand-written reconciliations that
     cross tables the constraints do not cover (money sums, the M5 melt).

Exit code 0 means every check passed. Nothing here repairs anything: this
script observes, and a failure is information rather than an inconvenience to
be patched over in the database.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from load.loader import connect  # noqa: E402
from load.schema import load_constraints  # noqa: E402

SOURCE_SCHEMAS = ["dunnhumby", "dataco", "olist", "m5", "analytics"]
ANALYTICS = "analytics"

failures: list[str] = []
warnings: list[str] = []


def report(ok: bool, message: str) -> bool:
    """Print one check result. Returns ``ok`` so callers can chain."""
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
    if not ok:
        failures.append(message)
    return ok


def note(message: str) -> None:
    print(f"         {message}")


def section(title: str) -> None:
    print(f"\n--- {title}")


def scalar(conn, sql: str, params: tuple = ()) -> object:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


# ---------------------------------------------------------------------
# 1. Reconciliation against what the ETL said it produced
# ---------------------------------------------------------------------
def etl_expected_rows(source_id: str, table: str) -> int | None:
    path = REPO_ROOT / "data" / "processed" / source_id / "_report.json"
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    for entry in report.get("tables", []):
        if entry.get("table") == table:
            return int(entry.get("rows_out", 0))
    return None


def check_reconciliation(conn, constraints: dict) -> dict[str, int]:
    section("1. row-count reconciliation (database vs ETL report)")
    live: dict[str, int] = {}
    for source_id, tables in constraints["load_order"].items():
        for table in tables:
            name = f"{source_id}.{table}"
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (name,))
                if cur.fetchone()[0] is None:
                    report(False, f"{name}: table does not exist")
                    continue
                cur.execute(f'SELECT COUNT(*) FROM {source_id}."{table}"')
                rows = int(cur.fetchone()[0])
            live[name] = rows
            wanted = etl_expected_rows(source_id, table)
            if wanted is None:
                report(True, f"{name}: {rows:>13,} rows (no ETL report to compare)")
            elif rows == wanted:
                report(True, f"{name}: {rows:>13,} rows, matches the ETL exactly")
            else:
                report(
                    False,
                    f"{name}: {rows:,} rows in the database but the ETL "
                    f"reported {wanted:,}",
                )
    return live


# ---------------------------------------------------------------------
# 2. What the database actually enforces
# ---------------------------------------------------------------------
def check_constraint_inventory(conn) -> dict:
    section("2. constraint inventory (read from pg_constraint)")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname, c.relname, con.conname, con.contype,
                   pg_get_constraintdef(con.oid), con.convalidated
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype IN ('p', 'u', 'f')
              AND n.nspname = ANY(%s)
            ORDER BY n.nspname, c.relname, con.contype, con.conname
            """,
            (SOURCE_SCHEMAS,),
        )
        rows = cur.fetchall()
    counts = {"primary key": 0, "unique": 0, "foreign key": 0}
    kinds = {"p": "primary key", "u": "unique", "f": "foreign key"}
    for schema, table, name, contype, _definition, validated in rows:
        counts[kinds[contype]] += 1
    for kind, n in counts.items():
        note(f"{kind:<12}: {n}")
    unvalidated = [r for r in rows if not r[5]]
    report(
        not unvalidated,
        f"all {len(rows)} key constraints are marked validated by PostgreSQL"
        if not unvalidated
        else f"{len(unvalidated)} constraint(s) are NOT convalidated: "
        + ", ".join(f"{r[0]}.{r[1]}.{r[2]}" for r in unvalidated),
    )
    return {
        "counts": counts,
        "constraints": [
            {
                "schema": r[0],
                "table": r[1],
                "name": r[2],
                "kind": kinds[r[3]],
                "definition": r[4],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------
# 3. Referential integrity, re-derived rather than trusted
# ---------------------------------------------------------------------
def check_referential_integrity(conn, constraints: dict) -> list[dict]:
    section("3. referential integrity (orphan queries re-run by us)")
    results: list[dict] = []
    for source_id, tables in constraints["load_order"].items():
        if source_id == ANALYTICS:
            continue
        for table in tables:
            spec = constraints.get("tables", {}).get(source_id, {}).get(table, {})
            for fk in spec.get("candidate_fks", []):
                ref_table = fk["ref_table"]
                cols = ", ".join(f'i."{c}"' for c in fk["columns"])
                ref_cols = ", ".join(f'p."{c}"' for c in fk["ref_columns"])
                present = " AND ".join(f"{cols} IS NOT NULL" for c in cols)
                sql = (
                    f'SELECT COUNT(*) FROM {source_id}."{table}" i '
                    f'LEFT JOIN {source_id}."{ref_table}" p ON {cols} = {ref_cols} '
                    f"WHERE {present} AND p.\"{fk['ref_columns'][0]}\" IS NULL"
                )
                started = time.monotonic()
                orphans = int(scalar(conn, sql))
                elapsed = time.monotonic() - started
                label = f"{source_id}.{table} -> {ref_table}({', '.join(fk['ref_columns'])})"
                # The first ref column is NOT NULL by definition of a PK/UNIQUE
                # target, so a NULL there genuinely means "no parent matched".
                report(
                    orphans == 0,
                    f"{label}: {orphans:,} orphan row(s)  [{elapsed:,.1f}s]",
                )
                results.append(
                    {
                        "relationship": label,
                        "columns": fk["columns"],
                        "orphans": orphans,
                    }
                )
    return results


# ---------------------------------------------------------------------
# 4. Uniqueness, re-derived
# ---------------------------------------------------------------------
def check_uniqueness(conn, constraints: dict) -> list[dict]:
    section("4. uniqueness (duplicate queries re-run by us)")
    results: list[dict] = []
    for source_id, tables in constraints["load_order"].items():
        if source_id == ANALYTICS:
            continue
        for table in tables:
            spec = constraints.get("tables", {}).get(source_id, {}).get(table, {})
            candidates: list[tuple[str, list[str]]] = []
            if spec.get("candidate_pk"):
                candidates.append(("primary key", spec["candidate_pk"]))
            for unique_columns in spec.get("unique_indexes", []):
                candidates.append(("unique", unique_columns))
            for kind, columns in candidates:
                cols = ", ".join(f'"{c}"' for c in columns)
                nulls = int(
                    scalar(
                        conn,
                        f'SELECT COUNT(*) FROM {source_id}."{table}" '
                        f"WHERE {' OR '.join(f'\"{c}\" IS NULL' for c in columns)}",
                    )
                )
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COALESCE(SUM(n - 1), 0), COUNT(*) FROM ("
                        f'SELECT COUNT(*) AS n FROM {source_id}."{table}" '
                        f"GROUP BY {cols} HAVING COUNT(*) > 1) d"
                    )
                    surplus, groups = (int(v or 0) for v in cur.fetchone())
                label = f"{source_id}.{table} {kind} on ({', '.join(columns)})"
                if nulls or surplus:
                    detail = []
                    if nulls:
                        detail.append(f"{nulls:,} row(s) with a NULL key column")
                    if surplus:
                        detail.append(
                            f"{surplus:,} duplicate row(s) across {groups:,} key value(s)"
                        )
                    # A failed candidate is a legitimate, documented outcome.
                    # It is not a failure of the load, so it is reported as a
                    # warning rather than failing the run - the alternative
                    # would be to delete real rows, which is forbidden.
                    message = f"{label}: " + "; ".join(detail)
                    print(f"  [WARN] {message}")
                    warnings.append(message)
                    results.append(
                        {
                            "label": label,
                            "kind": kind,
                            "columns": columns,
                            "nulls": nulls,
                            "duplicate_rows": surplus,
                            "duplicate_groups": groups,
                            "holds": False,
                        }
                    )
                else:
                    report(True, f"{label}: unique, no NULLs")
                    results.append(
                        {
                            "label": label,
                            "kind": kind,
                            "columns": columns,
                            "nulls": 0,
                            "duplicate_rows": 0,
                            "duplicate_groups": 0,
                            "holds": True,
                        }
                    )
    return results


# ---------------------------------------------------------------------
# 5. The cross-source rule, checked structurally
# ---------------------------------------------------------------------
def check_no_cross_source_keys(conn) -> None:
    section("5. no foreign key crosses a source boundary")
    # This is the project's hardest rule, and a rule that lives only in a
    # document is a rule that gets violated the first time someone is in a
    # hurry. Here it is a test: every foreign key must target a table in its
    # own schema.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname, c.relname, con.conname,
                   pn.nspname, pc.relname
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_class pc ON pc.oid = con.confrelid
            JOIN pg_namespace pn ON pn.oid = pc.relnamespace
            WHERE con.contype = 'f' AND n.nspname = ANY(%s)
              AND pn.nspname <> n.nspname
            """,
            (SOURCE_SCHEMAS,),
        )
        crossing = cur.fetchall()
    if report(
        not crossing,
        "every foreign key points inside its own schema - no cross-source "
        "relationship can be expressed",
    ):
        note(
            "dunnhumby household 123, olist customer 123 and dataco customer 123 "
            "are different people at different companies. The schema makes that "
            "un-joinable rather than merely discouraged."
        )
    else:
        for schema, table, name, ref_schema, ref_table in crossing:
            note(f"{schema}.{table}.{name} -> {ref_schema}.{ref_table}")

    # The analytics schema is the one place sources legitimately meet. It must
    # therefore be the only schema allowed to reference across, and today it
    # holds no foreign keys at all because it is still empty.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE con.contype = 'f' AND n.nspname = %s",
            (ANALYTICS,),
        )
        note(f"foreign keys defined in {ANALYTICS}: {int(cur.fetchone()[0])}")


# ---------------------------------------------------------------------
# 6. Cross-table reconciliations the constraints do not cover
# ---------------------------------------------------------------------
def check_row_level_spot_checks(conn) -> list[dict]:
    section("6. cross-table spot checks (money and grain reconciliations)")
    checks: list[dict] = []

    def compare(label: str, actual: float, expected: float, tolerance: float) -> None:
        ok = abs(actual - expected) <= tolerance
        report(
            ok,
            f"{label}: {actual:,.4f} vs {expected:,.4f} "
            f"(tolerance {tolerance})" if not ok
            else f"{label}: reconciles exactly ({actual:,.2f})",
        )
        checks.append(
            {"label": label, "actual": actual, "expected": expected, "ok": ok}
        )

    # DataCo: line-level order_item_total must sum to the order-level
    # order_value. The ETL split one file into two tables at order grain, so
    # this is the test that the split was lossless.
    line_sum = float(
        scalar(conn, "SELECT COALESCE(SUM(order_item_total), 0) FROM dataco.order_items")
    )
    order_sum = float(
        scalar(conn, "SELECT COALESCE(SUM(order_value), 0) FROM dataco.orders")
    )
    compare("dataco line sum = order sum", line_sum, order_sum, 0.01)

    # M5: the long melt must account for exactly the units in the wide source.
    # The melted column is `sales` (units); the daily rollup calls the same
    # quantity `total_units`. Getting these two names the wrong way round would
    # have produced a verification that quietly tested nothing.
    units = int(scalar(conn, "SELECT COALESCE(SUM(sales), 0) FROM m5.sales_long"))
    note(f"m5.sales_long total units: {units:,}")
    checks.append({"label": "m5.sales_long total units", "actual": units, "ok": True})

    # m5.sales_daily_total is the daily rollup of sales_long. It is a
    # derivative of the same fact, so the two must agree or one of them is a
    # lie. Recomputing the rollup from 59 million rows is the most expensive
    # check in this script and is worth it precisely because it is the only
    # thing that can catch a bad melt.
    started = time.monotonic()
    mismatches = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM (
                SELECT d_num, total_units FROM m5.sales_daily_total
                EXCEPT
                SELECT d_num, SUM(sales) FROM m5.sales_long GROUP BY d_num
            ) x
            """,
        )
    )
    back = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM (
                SELECT d_num, SUM(sales) FROM m5.sales_long GROUP BY d_num
                EXCEPT
                SELECT d_num, total_units FROM m5.sales_daily_total
            ) x
            """,
        )
    )
    elapsed = time.monotonic() - started
    report(
        mismatches == 0 and back == 0,
        f"m5 daily rollup matches the 59M-row melt in both directions "
        f"({mismatches} + {back} differing days)  [{elapsed:,.0f}s]",
    )
    checks.append(
        {
            "label": "m5 daily rollup vs sales_long",
            "actual": mismatches + back,
            "expected": 0,
            "ok": mismatches == 0 and back == 0,
        }
    )

    # Olist: order_payments_agg is the sanctioned route to payment revenue.
    # payments holds 103,886 rows against 99,441 orders, so the aggregate
    # must be SMALLER, and its total must equal the raw total.
    agg_total = float(
        scalar(conn, "SELECT COALESCE(SUM(payment_total), 0) FROM olist.order_payments_agg")
    )
    raw_total = float(
        scalar(conn, "SELECT COALESCE(SUM(payment_value), 0) FROM olist.payments")
    )
    compare("olist aggregated payment total = raw total", agg_total, raw_total, 0.01)
    agg_rows = int(scalar(conn, "SELECT COUNT(*) FROM olist.order_payments_agg"))
    raw_rows = int(scalar(conn, "SELECT COUNT(*) FROM olist.payments"))
    report(
        agg_rows < raw_rows,
        f"olist order_payments_agg has {agg_rows:,} rows vs {raw_rows:,} raw "
        f"payments (aggregation collapses the multi-payment orders)",
    )
    return checks


# ---------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independently verify the loaded OmniRetail database."
    )
    parser.add_argument(
        "--skip-catalog",
        action="store_true",
        help="only run the cheap checks (inventory and cross-source rules)",
    )
    args = parser.parse_args()

    constraints = load_constraints()
    print("OmniRetail Phase 6 verification")
    print("  every claim below is re-derived from the rows, not from pg_constraint")

    started = time.monotonic()
    evidence: dict = {}
    with connect() as conn:
        live = check_reconciliation(conn, constraints)
        evidence["row_counts"] = live
        inventory = check_constraint_inventory(conn)
        evidence["constraints"] = inventory["constraints"]
        check_no_cross_source_keys(conn)

        if not args.skip_catalog:
            evidence["referential_integrity"] = check_referential_integrity(
                conn, constraints
            )
            evidence["candidate_keys"] = check_uniqueness(conn, constraints)
            evidence["spot_checks"] = check_row_level_spot_checks(conn)

    elapsed = time.monotonic() - started
    total_rows = sum(live.values())

    print(f"\n{'=' * 70}")
    print(f"verified {len(live)} tables / {total_rows:,} rows in {elapsed:,.0f}s")
    print(f"FAILURES: {len(failures)}")
    for message in failures:
        print(f"  - {message}")
    if warnings:
        print(f"documented, not worked around: {len(warnings)}")
        for message in warnings:
            print(f"  - {message}")
    print("=" * 70)

    destination = REPO_ROOT / "data" / "processed" / "_verify_report.json"
    destination.write_text(
        json.dumps(
            {
                "elapsed_seconds": round(elapsed, 1),
                "total_rows": total_rows,
                "failures": failures,
                "warnings": warnings,
                **evidence,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"evidence -> {destination}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
