"""Shared fixtures for the OmniRetail test suite.

Two things have to be true before any assertion here is meaningful:

* `backend/` has to be importable, because the models and settings live there.
  Tests run from the repository root, so the path is added explicitly rather
  than relying on the package being installed.
* The analytics runner and the documentation generator live under
  `backend/scripts/`, which is NOT a package. They are loaded by file path.

WHY SOME TESTS RE-RUN EVERY QUERY AND SOME DO NOT
------------------------------------------------
Executing all the analytics SQL takes several minutes, dominated by the M5
price-vs-demand join. Two tiers:

* fast tests validate the written findings against the committed
  `_sql_results.json`. They need no database and catch the common failure -
  somebody edits a sentence and the number in it stops being true.
* slow tests re-execute every query against PostgreSQL and re-validate. They
  catch the other failure: the data changed under the documentation.

Both matter. A doc that drifts from its results file is a lie; results that drift
from the database is a lie that regenerates cleanly. Run the slow tier with
``pytest -m slow``, or both with no marker filter.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

RESULTS_FILE = REPO_ROOT / "data" / "processed" / "_sql_results.json"
FINDINGS_FILE = REPO_ROOT / "backend" / "config" / "sql_findings.yaml"
SQL_DIR = REPO_ROOT / "backend" / "sql"


def load_script(relative_path: str):
    """Import a module by file path, for scripts outside any package."""
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ #
# TEST KEYS MUST NOT OUTLIVE THE TEST RUN                             #
# ------------------------------------------------------------------ #
# The API tests mint real, admin-scoped credentials into `app.api_keys`
# because the auth path cannot be exercised against a mock. Every one of them
# is revoked in a fixture finaliser - but a finaliser does not run when a
# process is killed, a laptop sleeps, or a test crashes the interpreter. Two
# live admin-scoped keys labelled `pytest` survived exactly that way in this
# project before the sweep below existed.
#
# So the invariant is enforced twice, from both directions:
#   * at session START, any active key labelled like a test key is revoked -
#     an interrupted run cannot poison the next one;
#   * at session END, any active key labelled like a test key FAILS the suite
#     - a leak cannot pass silently.
#
# The label prefix is the mechanism. It is deliberately narrow: `frontend-dev`,
# `frontend-admin` and `ai-smoke` are real working keys and must survive.
TEST_KEY_LABEL_PREFIX = "pytest"


def writer_connection():
    """A read/write connection to the real database, from the real settings.

    The writer role, not `omniretail_readonly`: revoking a key is an UPDATE and
    the read-only role exists to prove the API cannot write, not to prevent an
    operator script from maintaining its own credential store.
    """
    import psycopg

    from app.core.config import get_settings

    settings = get_settings()
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=10,
    )


def api_keys_table_exists(conn) -> bool:
    return conn.execute(
        "SELECT to_regclass('app.api_keys') IS NOT NULL"
    ).fetchone()[0]


def get_settings():
    """The app's settings, re-exported for fixtures in this module.

    A local import rather than a module-level one: `app.core.config` reads
    `.env` at construction, and importing it while conftest is being collected
    would freeze settings before a test has had a chance to change the
    environment.
    """
    from app.core.config import get_settings as _get

    return _get()


def revoke_leaked_test_keys() -> list[tuple[int, str]]:
    """Deactivate every ACTIVE key whose label marks it as a test key.

    Returns the `(id, label)` pairs it revoked, so a caller can report them
    rather than mutating the credential table silently.
    """
    conn = writer_connection()
    try:
        if not api_keys_table_exists(conn):
            return []
        revoked = conn.execute(
            """
            UPDATE app.api_keys SET is_active = FALSE
            WHERE label LIKE %s AND is_active
            RETURNING id, label
            """,
            (f"{TEST_KEY_LABEL_PREFIX}%",),
        ).fetchall()
        conn.commit()
        return revoked
    finally:
        conn.close()


def active_test_keys() -> list[tuple[int, str]]:
    """Every ACTIVE key that this suite would consider its own."""
    conn = writer_connection()
    try:
        if not api_keys_table_exists(conn):
            return []
        return conn.execute(
            "SELECT id, label FROM app.api_keys "
            "WHERE label LIKE %s AND is_active ORDER BY id",
            (f"{TEST_KEY_LABEL_PREFIX}%",),
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def no_test_key_outlives_the_suite():
    """Sweep leaked test keys on entry, and prove none survived on exit."""
    revoked = revoke_leaked_test_keys()
    if revoked:
        print(
            f"\n[conftest] revoked {len(revoked)} leaked test key(s) from an "
            f"earlier interrupted run: {revoked}"
        )
    yield
    leaked = active_test_keys()
    assert not leaked, (
        f"the suite finished with ACTIVE test keys still in app.api_keys: "
        f"{leaked}. These are real credentials - revoke them with "
        f"`08_issue_api_key.py revoke <prefix>` and find the fixture whose "
        f"teardown did not run."
    )


@pytest.fixture(scope="module")
def api_client():
    """A TestClient holding a real, admin-scoped key, revoked in teardown.

    Shared rather than copied per file because a third copy had appeared. The
    key is minted through the SHIPPING script (`08_issue_api_key.py`) rather
    than by an INSERT written here, for one reason: a test-only credential path
    would let these tests pass while the authentication the API actually ships
    stayed broken.

    Session-scoped rather than module-scoped so several files share one key and
    one handshake - Argon2 verification is deliberately slow, and paying it per
    file is real time for no extra coverage.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    issuer = load_script("backend/scripts/db/08_issue_api_key.py")
    with issuer.connect() as conn:
        if not issuer.table_exists(conn):
            raise AssertionError(
                "app.api_keys does not exist; run `alembic upgrade head`"
            )
        key_id, key = issuer.create_key(
            conn,
            label=f"{TEST_KEY_LABEL_PREFIX}api-client",
            scopes="analytics:read,ai:read,admin:read",
            created_by="pytest",
        )
    try:
        with TestClient(app) as test_client:
            test_client.headers.update({"X-API-Key": key})
            yield test_client
    finally:
        engine = create_engine(get_settings().database_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM app.api_keys WHERE id = :id"), {"id": key_id}
                )
        finally:
            engine.dispose()


@pytest.fixture(scope="session")
def live():
    """A SQLAlchemy Inspector bound to the real database.

    Module-scoped in the test files that use it; the engine itself is created
    once here so several files can share one connection pool.
    """
    from sqlalchemy import create_engine, inspect

    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url, future=True)
    return inspect(engine)


@pytest.fixture(scope="session")
def runner():
    return load_script("backend/scripts/sql/run_analytics.py")


@pytest.fixture(scope="session")
def generator():
    return load_script("backend/scripts/docs/generate_sql_analytics.py")


@pytest.fixture(scope="session")
def findings() -> dict:
    import yaml

    return yaml.safe_load(FINDINGS_FILE.read_text(encoding="utf-8")) or {}


@pytest.fixture(scope="session")
def recorded_results() -> list[dict]:
    """The result rows from the last recorded run of the analytics SQL."""
    assert RESULTS_FILE.exists(), (
        f"{RESULTS_FILE} is missing. Run:\n"
        "  .venv/Scripts/python.exe backend/scripts/sql/run_analytics.py"
    )
    return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))["queries"]


@pytest.fixture(scope="session")
def live_results(runner) -> list[dict]:
    """Re-execute every analytics query against PostgreSQL, once per session.

    Session-scoped on purpose: the whole corpus takes minutes, and every test
    that needs it wants the same snapshot. Tests must not modify it.
    """
    import psycopg

    from app.core.config import get_settings

    settings = get_settings()
    records: list[dict] = []
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=10,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        for source in runner.SOURCES:
            folder = SQL_DIR / source
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.sql")):
                for block in runner.parse_file(path, source):
                    records.append(runner.run_block(conn, block))
    return records