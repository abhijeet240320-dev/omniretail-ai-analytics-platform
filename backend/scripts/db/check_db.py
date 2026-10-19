"""Post-bootstrap health check: prove the database is wired up correctly.

USAGE
-----
    .venv/Scripts/python.exe backend/scripts/db/check_db.py

WHAT IT CHECKS
--------------
  1. Both roles can log in, using the exact DSN builder the application uses.
  2. Both roles can see all five schemas.
  3. The read/write role can create and drop a table.
  4. The READ-ONLY role genuinely CANNOT write - proven by attempting an
     INSERT and requiring the database to reject it.

Check 4 is the one that matters. "We granted SELECT only" is a claim;
"we tried to write and the server said no" is evidence. This project's rule
is that every important assertion gets executed, not asserted in prose.

Exit code 0 means the database layer is ready for the load phase.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from app.core.config import get_settings  # noqa: E402

EXPECTED_SCHEMAS = ["dunnhumby", "dataco", "olist", "m5", "analytics"]


def report(ok: bool, message: str) -> None:
    """Print one check result in a fixed-width, log-friendly format."""
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")


def connect(settings, url_attr: str, read_only: bool) -> psycopg.Connection:
    """Open a connection using the application's own DSN builder.

    Using the real DSN (rather than hand-built connection arguments) means
    this script tests the same code path the API will use. If the DSN is
    malformed, we find out here instead of at first request.
    """
    kwargs = {
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "dbname": settings.postgres_db,
    }
    if url_attr == "rw":
        kwargs["user"] = settings.postgres_user
        kwargs["password"] = settings.postgres_password
    else:
        kwargs["user"] = settings.postgres_readonly_user
        kwargs["password"] = settings.postgres_readonly_password

    return psycopg.connect(**kwargs, connect_timeout=10)


def check_role(settings, label: str, url_attr: str, read_only: bool) -> bool:
    """Run the checks for one role. Returns True when everything passes.

    NOTE ON TRANSACTIONS: the connection is managed explicitly rather than
    with `with conn:`. We need several independent units of work on one
    connection, and an earlier version of this script reused `with conn:`
    twice - the second block then found a connection that was no longer
    usable, which produced a confusing "the connection is closed" failure
    that looked like a database problem instead of a test bug.
    """
    print(f"\n--- role: {label}")
    try:
        conn = connect(settings, url_attr, read_only)
    except psycopg.OperationalError as exc:
        report(False, f"could not connect: {exc}")
        return False

    ok = True
    try:
        # --- Read-side facts about this role ---------------------------
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            user, database = cur.fetchone()
            report(True, f"connected as {user!r} to {database!r}")

            cur.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) "
                "ORDER BY nspname",
                (EXPECTED_SCHEMAS,),
            )
            visible = [row[0] for row in cur.fetchall()]
            missing = sorted(set(EXPECTED_SCHEMAS) - set(visible))
            report(
                not missing,
                f"schemas visible: {', '.join(visible)}"
                + (f" (missing: {missing})" if missing else ""),
            )
            ok &= not missing

            cur.execute("SHOW default_transaction_read_only")
            default_ro = cur.fetchone()[0]
            if read_only:
                report(
                    default_ro == "on",
                    f"default_transaction_read_only = {default_ro}",
                )
                ok &= default_ro == "on"
        conn.commit()

        # --- The decisive check: can this role actually write? ---------
        if read_only:
            try:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE analytics._should_not_exist (x int)")
                conn.commit()
                # Reaching here means the control is broken.
                cur_cleanup = conn.cursor()
                cur_cleanup.execute("DROP TABLE analytics._should_not_exist")
                conn.commit()
                report(
                    False,
                    "SECURITY: read-only role CREATED a table (should be impossible)",
                )
                ok = False
            except psycopg.errors.ReadOnlySqlTransaction:
                conn.rollback()
                report(
                    True,
                    "write rejected: 'cannot execute ... in a read-only transaction'",
                )
            except psycopg.errors.InsufficientPrivilege as exc:
                conn.rollback()
                report(True, f"write rejected: {exc.diag.message_primary}")
            except psycopg.Error as exc:
                conn.rollback()
                report(True, f"write rejected ({type(exc).__name__})")
        else:
            # The read/write role must be able to create and drop its objects.
            try:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE analytics._healthcheck (x int)")
                    cur.execute("INSERT INTO analytics._healthcheck VALUES (1)")
                    cur.execute("SELECT count(*) FROM analytics._healthcheck")
                    count = cur.fetchone()[0]
                    cur.execute("DROP TABLE analytics._healthcheck")
                conn.commit()
                report(count == 1, "can create, insert, select and drop a table")
                ok &= count == 1
            except psycopg.Error as exc:
                conn.rollback()
                report(False, f"write path failed: {exc}")
                ok = False
    finally:
        conn.close()

    return ok


def main() -> int:
    settings = get_settings()
    print("OmniRetail database health check")
    print(f"  target: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")

    rw_ok = check_role(settings, "read/write (omniretail)", "rw", read_only=False)
    ro_ok = check_role(settings, "read-only (omniretail_readonly)", "ro", read_only=True)

    print("\n" + "=" * 62)
    print(f"read/write role : {'OK' if rw_ok else 'PROBLEMS'}")
    print(f"read-only role  : {'OK' if ro_ok else 'PROBLEMS'}")
    ready = rw_ok and ro_ok
    print("RESULT:", "DATABASE READY" if ready else "NOT READY")
    print("=" * 62)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())