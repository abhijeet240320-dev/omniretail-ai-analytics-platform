"""Load processed CSVs into PostgreSQL and VERIFY the constraints that hold.

THE LOAD IS TWO STEPS NOW, NOT THREE
-------------------------------------
1. TRUNCATE the table (structure untouched) and COPY the data in.
2. TEST every declared constraint against the real rows and record the verdict.

This used to be `DROP TABLE IF EXISTS ... CASCADE` + `CREATE TABLE` with inferred
types, followed by adding the keys that tested true. That was correct when the
loader was the only thing that built the schema. It no longer is: Alembic owns
the DDL, so the loader must not issue CREATE TABLE, DROP TABLE or ALTER TABLE
ADD CONSTRAINT. See the ownership split in 04_load.py and in
backend/alembic/README.md.

WHAT DID NOT CHANGE, AND WHY IT STILL MATTERS
---------------------------------------------
The key tests are still the point of this module. They used to decide whether to
add a constraint; now they DECIDE WHETHER ALEMBIC WAS RIGHT. The declared keys
were chosen when the data was first examined, and the loader re-measures them on
every run, so "29 primary keys hold" is a fact about the rows as they are now, not
a claim inherited from an earlier build.

That is also what keeps `dunnhumby.coupon_redemptions` honest. Alembic declares no
primary key for it; the loader still tests the candidate
(household_key, day, coupon_upc) against the real rows and records that the data
refused it. Nothing is ever deleted to make a constraint pass - deleting real rows
to satisfy a key a human guessed at would leave the data quietly wrong and the
schema looking clean.

TRUNCATE, NOT DROP
------------------
TRUNCATE empties the table without touching its structure or constraints, which is
what "the loader owns the rows, Alembic owns the shape" has to mean in practice.
CASCADE is required because PostgreSQL refuses to truncate a table that a
foreign key points at, and the load order deliberately fills parents before
children. Truncating a parent therefore empties its children too - which is
correct here, because the next COPY refills them, and it is exactly what the old
`DROP ... CASCADE` did.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from load.schema import Column, load_constraints  # noqa: E402

COPY_CHUNK = 4 * 1024 * 1024  # 4 MiB: large enough to keep the pipe busy


@dataclass
class KeyResult:
    """Outcome of testing one constraint against the rows that are actually loaded.

``passed`` is a measurement, never an intention. ``action`` records how the
verdict is reported:

* "verified"  - the constraint is declared in the database (Alembic put it
  there) and the real rows support it. This is the normal case.
* "rejected"  - a CANDIDATE key was proposed and the data refused it. The
  constraint is correctly absent.

The old values "added" and "already present" are gone because the loader no
    longer creates constraints. Keeping them would have made a report claim work
    this module does not do.
    """

    kind: str  # "primary key" | "unique" | "foreign key"
    table: str
    columns: list[str]
    passed: bool
    detail: str
    action: str = "verified"  # "verified" | "rejected"


@dataclass
class LoadReport:
    """Everything the load did, so it can be documented from facts."""

    loaded: list[tuple[str, int, float]] = field(default_factory=list)
    keys: list[KeyResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)

    @property
    def verified(self) -> list[KeyResult]:
        """Declared constraints the real rows support."""
        return [k for k in self.keys if k.passed]

    @property
    def rejected(self) -> list[KeyResult]:
        """Candidate keys the data refused. Documented, never worked around."""
        return [k for k in self.keys if not k.passed]

    @property
    def holding(self) -> list[KeyResult]:
        """Every constraint that is in the database and holds."""
        return [k for k in self.keys if k.passed]


def connect():
    """Connect with the read/write application role (the AI role cannot write)."""
    settings = get_settings()
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=15,
    )


def find_psql() -> str | None:
    """Locate the psql executable, the same way pg_ctl.sh does.

    Prefers whatever is already on PATH, then falls back to the usual Windows
    and Linux install locations. Returns None if psql cannot be found, which is
    the signal to use the slower in-process COPY instead.
    """
    from shutil import which

    found = which("psql")
    if found:
        return found
    patterns = [
        "C:/Program Files/PostgreSQL/*/bin/psql.exe",
        "/usr/lib/postgresql/*/bin/psql",
        "/usr/pgsql-*/bin/psql",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def copy_via_psql(schema: str, table: str, csv_path: Path, columns: list[Column],
                  settings) -> tuple[bool, str]:
    """Load a CSV with psql's client-side ``\\copy``, and report what happened.

    Returns ``(ok, detail)``. ``ok`` is False when psql could not be run, in
    which case the caller falls back to the in-process COPY - a missing psql
    must degrade performance, never correctness.

    WHY PSQL AND NOT ``COPY ... FROM '<file>'``
    -------------------------------------------
    The server-side form reads the file with PostgreSQL's own process and is
    faster still, but it is gated behind the ``pg_read_server_files`` role.
    Granting that to the application role would hand the API the ability to
    read any file the server can read - including ``.env`` and the raw data.
    Buying load speed with the security model is a bad trade in a project whose
    whole point is that the read-only role provably cannot do things it must not
    do. ``\\copy`` streams from the client, so it needs no extra privilege: the
    file is read by psql, exactly as the Python path read it, only about
    nineteen times faster because it is not running an interpreter.

    WHY IT IS SO MUCH FASTER
    ------------------------
    Measured on this machine: streaming the same 203 MB / 6,841,121-row file,
    ``\\copy`` took 14.9 s (13.6 MB/s) while the psycopg copy path managed about
    0.7 MB/s and burned a full CPU core doing it - on a 4.5 GB file that is the
    difference between five minutes and eight hours. The disk is not the
    problem: the same NVMe does 165 MB/s sequential writes and 1.4 GB/s reads.
    The cost is per-chunk work in the client, and ``\\copy`` does the same job
    in C.
    """
    psql = find_psql()
    if not psql:
        return False, "psql not found on PATH or in the usual install locations"

    column_list = ", ".join(f'"{c.name}"' for c in columns)
    # Forward slashes throughout: psql parses the filename itself and a
    # backslash in a \copy argument is an escape character, not a path
    # separator. The workspace path also contains a space and a non-ASCII
    # character, both of which this quoting handles.
    absolute = str(csv_path.resolve()).replace("\\", "/")
    meta = (
        f"\\copy {schema}.\"{table}\" ({column_list}) FROM '{absolute}' "
        "WITH (FORMAT csv, HEADER true)"
    )
    # The password goes in the environment, never on the command line, so it
    # stays out of the process table exactly as the bootstrap script intends.
    env = dict(os.environ)
    env["PGPASSWORD"] = settings.postgres_password
    command = [
        psql,
        "-X", "-q", "-v", "ON_ERROR_STOP=1",
        "-h", settings.postgres_host,
        "-p", str(settings.postgres_port),
        "-U", settings.postgres_user,
        "-d", settings.postgres_db,
        "-c", meta,
    ]
    try:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=None
        )
    except OSError as exc:  # pragma: no cover - depends on the host
        return False, f"could not run psql ({exc})"

    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "psql failed").strip()[:500]
    return True, "psql \\copy"


def copy_csv(conn, schema: str, table: str, csv_path: Path, columns: list[Column]):
    """Load a CSV with the fastest COPY path available.

    COPY is used rather than INSERT because it is roughly two orders of
    magnitude faster, and this pipeline moves 106 million rows including a
    4.5 GB file.

    Two paths, in order of preference:

    1. ``psql \\copy`` - a C client streaming the file over the connection.
    2. psycopg's ``copy.write()`` in this process, chunked at 4 MiB.

    The second is kept because a missing psql must not stop the load; it is
    also the honest record of what this pipeline did before ``\\copy`` existed,
    and it is dramatically slower, which is why it is not the default.

    Reading the file in binary chunks keeps memory flat regardless of file size.
    The CSV header is supplied by the file itself (``HEADER true``), so the load
    and the DDL always agree on column order without either restating it.

    Returns ``(seconds, method)``.
    """
    settings = get_settings()
    started = time.monotonic()

    ok, detail = copy_via_psql(schema, table, csv_path, columns, settings)
    if ok:
        return time.monotonic() - started, detail

    # Fall back to streaming inside this process.
    print(f"        (falling back to psycopg COPY for {schema}.{table}: {detail})")
    column_list = ", ".join(f'"{c.name}"' for c in columns)
    statement = (
        f'COPY {schema}."{table}" ({column_list}) '
        "FROM STDIN WITH (FORMAT csv, HEADER true)"
    )
    with conn.cursor() as cur:
        with cur.copy(statement) as copy:
            with csv_path.open("rb") as handle:
                while True:
                    chunk = handle.read(COPY_CHUNK)
                    if not chunk:
                        break
                    copy.write(chunk)
    conn.commit()
    return time.monotonic() - started, "psycopg copy (fallback)"


def table_row_count(conn, schema: str, table: str) -> int | None:
    """Exact row count, or None when the table does not exist.

    ``to_regclass`` is used rather than catching an undefined-table error
    because it is a single cheap lookup and returns NULL instead of raising,
    which keeps the resume path free of exception handling.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",))
        if cur.fetchone()[0] is None:
            return None
        cur.execute(f'SELECT COUNT(*) FROM {schema}."{table}"')
        return int(cur.fetchone()[0])


def table_exists(conn, schema: str, table: str) -> bool:
    """Does Alembic's schema actually contain this table?

    The loader cannot create it any more, so this is the difference between
    "run alembic upgrade head" and a confusing failure deep inside a COPY.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{table}",))
        return bool(cur.fetchone()[0])


def alembic_revision(conn) -> str | None:
    """The revision this database is stamped with, or None if it is unmanaged.

    Read rather than assumed. A database that was built by the old loader has
    tables but no alembic_version row, and saying so plainly is more useful
    than a COPY that fails on a constraint the loader never had to create.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        if not cur.fetchone()[0]:
            return None
        cur.execute("SELECT version_num FROM alembic_version")
        rows = cur.fetchall()
    return rows[0][0] if rows else None


def table_columns_from_db(conn, schema: str, table: str) -> list[Column]:
    """The table's columns, in declaration order, as PostgreSQL reports them.

    The DATABASE is now the authority on structure, not the CSV header. That
    reversal is the whole point of the Alembic split: previously the loader
    inferred types from the file and created the table to match, so a changed
    file silently meant a changed schema. Now a mismatch between file and table
    is a drift error the caller reports, not a rebuild.

    ``format_type`` is used rather than ``data_type`` so NUMERIC(18,4) and
    VARCHAR(32) keep their modifiers; ``information_schema.data_type`` throws
    them away and would report both as "numeric" and "character varying".
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (schema, table),
        )
        rows = cur.fetchall()
    return [Column(name, pg_type, "declared by Alembic") for name, pg_type in rows]


def truncate_table(conn, schema: str, table: str) -> None:
    """Empty the table. Structure, columns and constraints are untouched.

    CASCADE empties any table that a foreign key points at this one, because
    PostgreSQL will not truncate a referenced table otherwise. In load order the
    parent is always filled before its children, so the rows lost here are
    exactly the rows the next COPY restores - which is what the old
    `DROP TABLE ... CASCADE` did, minus the schema churn.
    """
    with conn.cursor() as cur:
        cur.execute(f'TRUNCATE TABLE {schema}."{table}" CASCADE')
    conn.commit()


def declared_constraints(conn, schema: str, table: str) -> dict[str, list[dict]]:
    """Every constraint PostgreSQL says this table has, grouped by kind.

    Read from ``pg_constraint`` rather than from ``db_constraints.yaml`` on
    purpose. The YAML is the SPEC - what was proposed, and why. The catalog is
    the FACT - what the migration actually created. Verifying the declared set
    means verifying the migration, which is the thing that is supposed to be
    authoritative.

    ``connamespace`` is the constraint's own schema and ``relnamespace`` does not
    exist on pg_constraint in PostgreSQL 18; joining through pg_class is the
    portable route and also gives the table name.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.contype,
                   c.conname,
                   COALESCE((
                       SELECT array_agg(a.attname ORDER BY k.ord)
                       FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                       JOIN pg_attribute a
                         ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                   ), '{}'),
                   COALESCE((
                       SELECT array_agg(a.attname ORDER BY k.ord)
                       FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)
                       JOIN pg_attribute a
                         ON a.attrelid = c.confrelid AND a.attnum = k.attnum
                   ), '{}'),
                   (SELECT rn.nspname FROM pg_namespace rn WHERE rn.oid = c.confrelid),
                   (SELECT rt.relname FROM pg_class rt WHERE rt.oid = c.confrelid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = %s AND t.relname = %s
            ORDER BY c.contype, c.conname
            """,
            (schema, table),
        )
        rows = cur.fetchall()

    grouped: dict[str, list[dict]] = {"primary key": [], "unique": [], "foreign key": []}
    names = {"p": "primary key", "u": "unique", "f": "foreign key"}
    for contype, conname, cols, refcols, refschema, reftable in rows:
        kind = names.get(contype)
        if kind is None:
            # CHECK constraints have no equivalent in db_constraints.yaml and
            # nothing in this project declares one.
            continue
        entry = {
            "name": conname,
            "table": f"{schema}.{table}",
            "columns": list(cols),
            "kind": kind,
        }
        if kind == "foreign key":
            entry["ref_table"] = f"{refschema}.{reftable}" if refschema else reftable
            entry["ref_columns"] = list(refcols)
        grouped[kind].append(entry)
    return grouped


def constraint_exists(conn, name: str) -> bool:
    """Is a constraint of this exact name already defined in the database?

    Needed by the resume path: re-adding a constraint that survived an earlier
    run raises DuplicateObject, and treating that as a failure would report a
    healthy database as broken.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", (name,))
        return cur.fetchone() is not None


def count_duplicates(
    conn, schema: str, table: str, columns: list[str]
) -> tuple[int, int]:
    """How many rows exceed the first occurrence of this candidate key?

    Returns ``(surplus_rows, offending_groups)``.

    ``count(*) - count(distinct ...)`` is not quite right because SQL DISTINCT
    on multiple columns is a row-level distinct; grouping and summing the
    surplus is exact and also tells us how many offending groups exist.
    """
    cols = ", ".join(f'"{c}"' for c in columns)
    query = (
        f"SELECT COALESCE(SUM(n - 1), 0), COUNT(*) "
        f"FROM (SELECT COUNT(*) AS n FROM {schema}.\"{table}\" "
        f"GROUP BY {cols} HAVING COUNT(*) > 1) AS dup"
    )
    with conn.cursor() as cur:
        cur.execute(query)
        surplus, groups = cur.fetchone()
        return int(surplus or 0), int(groups or 0)


def test_primary_key(conn, schema: str, table: str, columns: list[str]) -> KeyResult:
    """A candidate primary key must be unique AND not contain NULLs."""
    # A composite key is violated if ANY component is NULL, so the predicate
    # must be OR-ed. Writing `WHERE "a", "b" IS NULL` is a syntax error, and
    # `WHERE ("a", "b") IS NULL` does not do what it looks like - it is a
    # row-value test, not a per-column one.
    null_predicate = " OR ".join(f'"{c}" IS NULL' for c in columns)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {schema}.\"{table}\" WHERE {null_predicate}"
        )
        nulls = int(cur.fetchone()[0])

    surplus, groups = count_duplicates(conn, schema, table, columns)

    if nulls:
        return KeyResult(
            "primary key", f"{schema}.{table}", columns, False,
            f"rejected: {nulls:,} row(s) have a NULL in the key",
        )
    if surplus:
        return KeyResult(
            "primary key", f"{schema}.{table}", columns, False,
            f"rejected: {surplus:,} duplicate row(s) across {groups:,} key value(s)",
        )
    return KeyResult("primary key", f"{schema}.{table}", columns, True, "unique, no NULLs")


def test_foreign_key(
    conn, schema: str, table: str, columns: list[str], ref_table: str, ref_columns: list[str]
) -> KeyResult:
    """Count rows whose FK value has no matching parent row."""
    # A row only violates the FK if EVERY part of the key is non-null (a
    # partially-null composite key is not a reference, and PostgreSQL does not
    # enforce MATCH FULL here). The orphan test must mirror that exactly.
    all_present = " AND ".join(f'i."{c}" IS NOT NULL' for c in columns)
    join = " AND ".join(
        f'i."{c}" = p."{r}"' for c, r in zip(columns, ref_columns)
    )
    query = (
        f'SELECT COUNT(*) FROM {schema}."{table}" i '
        f'LEFT JOIN {schema}."{ref_table}" p ON {join} '
        f"WHERE {all_present} AND p.\"{ref_columns[0]}\" IS NULL"
    )
    with conn.cursor() as cur:
        cur.execute(query)
        orphans = int(cur.fetchone()[0])
    if orphans:
        return KeyResult(
            "foreign key", f"{schema}.{table}", columns, False,
            f"rejected: {orphans:,} row(s) reference a non-existent "
            f"{schema}.{ref_table} row",
        )
    return KeyResult(
        "foreign key", f"{schema}.{table}", columns, True,
        f"references {schema}.{ref_table}({', '.join(ref_columns)})",
    )


def primary_key_name(schema: str, table: str) -> str:
    """The name Alembic gave this table's primary key.

    Kept because 06_generate_models.py and the tests assert the models use the
    names the database actually gave them, and because it documents the grammar
    the migration follows. Nothing CREATES a constraint with it any more.
    """
    return f"pk_{schema}_{table}"


def unique_name(schema: str, table: str, index: int) -> str:
    """The name of the `index`-th unique constraint Alembic created here."""
    return f"uq_{schema}_{table}_{index}"


def foreign_key_name(schema: str, table: str, columns: list[str]) -> str:
    """The name Alembic gave the foreign key on `columns`."""
    return f"fk_{schema}_{table}_{'_'.join(columns)}"
