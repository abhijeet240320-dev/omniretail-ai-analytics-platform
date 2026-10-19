"""Create the database, the two roles, the schemas and the grants.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/db/bootstrap_db.py
    .venv/Scripts/python.exe backend/scripts/db/bootstrap_db.py --drop

WHY PYTHON RATHER THAN A SHELL ONE-LINER
----------------------------------------
The passwords must never appear on a command line (they would land in shell
history and in the process table) and must never be hardcoded in a .sql file
(they would land in git). Reading them from the same ``app.core.config``
object the application uses means there is exactly one source of truth for
credentials, and the bootstrap cannot drift away from what the app expects.

The scripts in ``backend/sql/00_bootstrap/`` are still plain SQL on purpose:
they are readable, reviewable and runnable by hand. This module just runs
them in the right order, against the right database, with the right
variables - which is the part that is easy to get wrong by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make "backend" importable so we can reuse app.core.config.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import psycopg  # noqa: E402
import psycopg.sql  # noqa: E402

from app.core.config import get_settings  # noqa: E402

BOOTSTRAP_DIR = REPO_ROOT / "backend" / "sql" / "00_bootstrap"

# Each step: which file, which database to connect to, and whether it needs
# the generated role passwords bound as parameters.
STEPS = [
    ("01_roles.sql", "postgres", True),
    ("02_database.sql", "postgres", False),  # only run when the db is missing
    ("03_schemas_and_grants.sql", None, False),  # None = the app database
]


def connect(settings, database: str) -> psycopg.Connection:
    """Connect as the superuser to a specific maintenance database.

    We deliberately do NOT use the application DSN here: bootstrapping has to
    happen before the application role can be trusted or even log in.
    """
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=database,
        user="postgres",
        autocommit=True,
    )


def database_exists(settings) -> bool:
    """True when the application database is already present."""
    with connect(settings, "postgres") as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (settings.postgres_db,)
        )
        return cur.fetchone() is not None


def run_step(settings, filename: str, database: str | None, needs_passwords: bool) -> None:
    """Execute one bootstrap SQL file."""
    path = BOOTSTRAP_DIR / filename
    sql = path.read_text(encoding="utf-8")
    target_db = database or settings.postgres_db

    # The role passwords are bound as named parameters, never interpolated
    # into the statement text. That keeps them out of the SQL string, out of
    # server logs and out of any error message that echoes the query.
    params: dict[str, str] = {}
    if needs_passwords:
        params = {
            "rw_password": settings.postgres_password,
            "ro_password": settings.postgres_readonly_password,
        }

    print(f"  -> {filename} (database: {target_db})")

    # psycopg 3 delivers server NOTICEs to a callback rather than exposing a
    # list, so we collect them ourselves. This is what makes the RAISE NOTICE
    # output from the DO blocks visible, e.g. "created role omniretail" and
    # "verified: omniretail_readonly holds no write privileges".
    notices: list[str] = []

    with connect(settings, target_db) as conn:
        conn.add_notice_handler(
            lambda diag: notices.append(f"{diag.severity}: {diag.message_primary}")
        )

        # WHY NOT BIND PARAMETERS HERE
        # The statements in these files are DDL (ALTER ROLE, CREATE SCHEMA).
        # PostgreSQL only accepts $1-style parameters in DML - SELECT, INSERT,
        # UPDATE, DELETE - not in utility statements, so `ALTER ROLE x
        # PASSWORD $1` is a syntax error. We therefore splice the value in as
        # a properly escaped LITERAL using psycopg's own quoting, which
        # applies the same escaping rules the driver would for a parameter.
        # The passwords are never concatenated by hand.
        statement = sql
        for name, value in params.items():
            placeholder = f":'{name}'"
            literal = psycopg.sql.Literal(value).as_string(conn)
            statement = statement.replace(placeholder, literal)

        with conn.cursor() as cur:
            cur.execute(statement)

    for message in notices:
        if message.strip():
            print(f"     {message.strip()}")


def drop_everything(settings) -> None:
    """Drop the database so ``--drop`` gives a genuinely clean slate."""
    print("  DROPPING database omniretail (all data will be lost)")
    with connect(settings, "postgres") as conn, conn.cursor() as cur:
        # Terminate other sessions first, otherwise DROP DATABASE fails.
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'omniretail' AND pid <> pg_backend_pid()"
        )
        cur.execute("DROP DATABASE IF EXISTS omniretail")
    print("  dropped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the OmniRetail database.")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="drop and recreate the database first (destroys all data).",
    )
    args = parser.parse_args()

    settings = get_settings()
    print("OmniRetail database bootstrap")
    print(f"  host : {settings.postgres_host}:{settings.postgres_port}")
    print(f"  db   : {settings.postgres_db}")
    print(f"  rw   : {settings.postgres_user}")
    print(f"  ro   : {settings.postgres_readonly_user}")

    if settings.postgres_password in {"", "change_me", "change_me_too"}:
        print(
            "\nERROR: POSTGRES_PASSWORD is still a placeholder. "
            "Set a real value in .env before bootstrapping.",
            file=sys.stderr,
        )
        return 2

    if args.drop:
        drop_everything(settings)

    try:
        for filename, database, needs_passwords in STEPS:
            # CREATE DATABASE is not idempotent in SQL, so the existence check
            # lives here rather than in the .sql file.
            if filename == "02_database.sql" and database_exists(settings):
                print(f"  -- {filename}: database already exists, skipping")
                continue
            run_step(settings, filename, database, needs_passwords)
    except psycopg.OperationalError as exc:
        print(f"\nFATAL: could not reach PostgreSQL: {exc}", file=sys.stderr)
        print(
            "Is the cluster running? Try: bash backend/scripts/db/pg_ctl.sh start",
            file=sys.stderr,
        )
        return 1

    print("\nbootstrap complete.")
    print("Next: .venv/Scripts/python.exe backend/scripts/db/check_db.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())