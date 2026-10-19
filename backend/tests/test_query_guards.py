"""Cost protection on the query path.

TWO CONTROLS, TWO THINGS BOUNDED
--------------------------------
* `statement_timeout` bounds how LONG one statement may run. Without it,
  PostgreSQL's default is 0 - wait forever - and on a database with a
  59-million-row fact table a single unlucky plan holds a pooled connection
  until the pool is exhausted and the API stops answering `/health` too.
* `include_total=false` bounds how MANY executions one request makes. On the
  `m5` aggregates the unpaginated `count(*)` costs as much as the page itself,
  because `LIMIT` is applied to a subquery and cannot be pushed into a
  `GROUP BY`.

Both are asserted by MEASUREMENT, not by reading the configuration back. A test
that asserts `settings.statement_timeout_ms == 300_000` proves only that a
constant is a constant; these ask a live connection what it actually got, and
make a real statement actually overrun.
"""

from __future__ import annotations

import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import event, text  # noqa: E402

from conftest import load_script  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.readonly import (  # noqa: E402
    _parse_postgres_interval_ms,
    get_readonly_session_factory,
    is_query_timeout,
    statement_timeout_ms_in_force,
)
from app.main import app  # noqa: E402
from app.services.analytics import execute_query  # noqa: E402


@pytest.fixture(scope="module")
def client():
    """A client holding a real, admin-scoped key, revoked in teardown.

    Issued through the shipping script rather than inserted by hand, for the
    reason `test_api.py` gives: a test-only credential path would pass here
    while the auth the API actually ships stayed broken.
    """
    from fastapi.testclient import TestClient

    issuer = load_script("backend/scripts/db/08_issue_api_key.py")
    with issuer.connect() as conn:
        assert issuer.table_exists(conn), "run `alembic upgrade head` first"
        key_id, key = issuer.create_key(
            conn,
            label="pytest-query-guards",
            scopes="analytics:read,ai:read,admin:read",
            created_by="pytest",
        )
    try:
        with TestClient(app) as test_client:
            test_client.headers.update({"X-API-Key": key})
            yield test_client
    finally:
        _revoke(key_id)


def _revoke(key_id: int) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(get_settings().database_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM app.api_keys WHERE id = :id"), {"id": key_id}
            )
    finally:
        engine.dispose()


@pytest.fixture
def session():
    """A read-only session on the real database, opened through the engine."""
    sess = get_readonly_session_factory()()
    try:
        sess.execute(text("SET TRANSACTION READ ONLY"))
        yield sess
    finally:
        sess.rollback()
        sess.close()


# ==============================================================================
# THE TIMEOUT IS ARMED
# ==============================================================================


def test_every_readonly_connection_carries_a_statement_timeout(session):
    """Asked, not configured.

    `SHOW statement_timeout` returns the value this connection actually got. If
    the engine stopped passing the libpq option - a refactor, a `create_engine`
    argument dropped by accident - this fails, and nothing else would.
    """
    raw = session.execute(text("SHOW statement_timeout")).scalar()
    measured = _parse_postgres_interval_ms(str(raw))

    assert measured > 0, (
        f"statement_timeout is {raw!r}, which in PostgreSQL means no limit at "
        f"all; one bad plan can hold a pooled connection indefinitely"
    )
    assert measured == get_settings().statement_timeout_ms


def test_the_reported_timeout_matches_a_second_independent_connection():
    """The guard is per-connection, so every connection must get it.

    A guard applied to one connection in a pool of ten protects one request in
    ten. This opens its own connection rather than reusing the fixture's, so a
    `connect` event listener that fired once could not pass it.
    """
    from sqlalchemy import create_engine

    from app.database.readonly import _connect_options

    engine = create_engine(
        get_settings().readonly_database_url,
        future=True,
        connect_args=_connect_options(),
    )
    try:
        with engine.connect() as conn:
            raw = conn.execute(text("SHOW statement_timeout")).scalar()
            assert _parse_postgres_interval_ms(str(raw)) == (
                get_settings().statement_timeout_ms
            )
            # And the connection is identifiable while it is doing the work.
            name = conn.execute(text("SHOW application_name")).scalar()
            assert name == "omniretail-api", name
    finally:
        engine.dispose()


def test_the_timeout_is_reported_by_the_health_endpoint(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok", body
    assert body["statement_timeout_ms"] == get_settings().statement_timeout_ms, (
        f"/api/health reports {body.get('statement_timeout_ms')!r}; a health "
        f"check that cannot see its own guard is not reporting anything"
    )


def test_a_timed_out_statement_is_actually_cancelled(session):
    """The guard does not merely exist - it stops work.

    The timeout is lowered for this one session only, so the test costs
    milliseconds instead of the 300 s the configuration asks for. What is being
    proved is that PostgreSQL raises rather than returning a partial result,
    because a timeout that returned whatever was ready would be a correctness
    bug wearing a performance costume.
    """
    session.execute(text("SET LOCAL statement_timeout = 250"))
    started = __import__("time").perf_counter()
    with pytest.raises(Exception) as caught:
        session.execute(text("SELECT pg_sleep(30)")).scalar()
    elapsed = __import__("time").perf_counter() - started

    assert is_query_timeout(caught.value), (
        f"expected a query_canceled error, got {type(caught.value).__name__}: "
        f"{caught.value}"
    )
    assert elapsed < 10, (
        f"the statement took {elapsed:.1f}s to be cancelled; a 250 ms timeout "
        f"that takes 30 s to enforce is not a timeout"
    )
    session.rollback()


def test_the_api_reports_a_timeout_as_504_not_502(client, monkeypatch):
    """A timeout is not a broken query.

    502 says "the database rejected this SQL", which sends the caller hunting a
    syntax error in a statement that is perfectly valid. 504 says what happened:
    valid SQL, out of time. The distinction is the difference between a useful
    error message and a wasted afternoon.
    """
    import psycopg

    from app.services.analytics import ExecutionResult

    def cancelled(*args, **kwargs):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    monkeypatch.setattr("app.api.analytics.execute_query", cancelled)
    response = client.get("/api/analytics/queries/olist.review_coverage_and_score")

    assert response.status_code == 504, response.status_code
    detail = response.json()["detail"]
    assert "timeout" in detail, detail
    assert "review_coverage_and_score" in detail, detail

    # And the pre-existing behaviour for a genuinely rejected query is intact:
    # still 502, and still without the exception's own text.
    def broken(*args, **kwargs):
        raise psycopg.errors.SyntaxError('syntax error at or near "SELCT"')

    monkeypatch.setattr("app.api.analytics.execute_query", broken)
    response = client.get("/api/analytics/queries/olist.review_coverage_and_score")
    assert response.status_code == 502, response.status_code
    assert "SELCT" not in response.json()["detail"], response.json()

    # `ExecutionResult` is imported above to prove the symbol the router builds
    # still exists with the fields the route reads; a typo in the 504 path must
    # not have quietly changed the success path's shape.
    assert ExecutionResult(
        columns=["a"], rows=[{"a": 1}], total_rows=1, elapsed_ms=1.0
    ).total_excluded is False


# ==============================================================================
# SKIPPING THE TOTAL
# ==============================================================================


def test_skipping_the_total_does_not_execute_the_count():
    """`include_total=false` must skip the statement, not just hide the number.

    Recording every statement the session actually sends is the only way to tell
    the difference between "the count was skipped" and "the count was computed
    and then thrown away" - and the second is the expensive one, which is the
    whole reason the parameter exists.
    """
    from app.database.readonly import get_readonly_engine

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_readonly_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            execute_query(
                _as_session(conn),
                "SELECT 1 AS n",
                limit=5,
                skip_total=True,
            )
        assert statements, "no statements were recorded; the test proved nothing"
        assert not any("count(*)" in s for s in statements), statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_the_default_path_still_computes_the_total():
    """The optimisation must not have become the default.

    A pager that silently stops working is worse than a slow page: the number
    simply disappears and nothing looks broken.
    """
    from app.database.readonly import get_readonly_engine

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_readonly_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            execute_query(_as_session(conn), "SELECT 1 AS n", limit=5)
        assert any("count(*)" in s for s in statements), statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _as_session(conn):
    """Wrap a raw Connection in the Session interface `execute_query` expects.

    `execute_query` only ever calls `session.execute(...)`, so this avoids
    building a second engine for the tests that need to watch the statements.
    """
    class _Shim:
        def execute(self, *args, **kwargs):
            return conn.execute(*args, **kwargs)

    return _Shim()


def test_the_endpoint_reports_an_excluded_total_honestly(client):
    """`total_rows` is null, not a number - and the reason is in the payload.

    Reporting `row_count` as the total would be the tempting shortcut and it
    would be a lie the moment `row_count == limit`, which is exactly the case a
    reader cares about.
    """
    body = client.get(
        "/api/analytics/queries/olist.review_coverage_and_score?include_total=false"
    ).json()

    assert body["total_excluded"] is True
    assert body["total_rows"] is None, body["total_rows"]
    assert body["row_count"] > 0, "the page itself must still be returned"

    counted = client.get(
        "/api/analytics/queries/olist.review_coverage_and_score"
    ).json()
    assert counted["total_excluded"] is False
    assert isinstance(counted["total_rows"], int)
    assert counted["total_rows"] >= counted["row_count"], counted


# ==============================================================================
# THE TIMEOUT PARSER
# ==============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("300000", 300_000),        # milliseconds, PostgreSQL's bare form
        ("300000ms", 300_000),
        ("5min", 300_000),
        ("300s", 300_000),
        ("0", 0),                    # 0 means UNLIMITED, and must parse as 0
        ("1h", 3_600_000),
        ("10min 30s", 630_000),
        ("0s", 0),
    ],
)
def test_the_timeout_parser_reads_every_form_postgres_accepts(raw, expected):
    """`SHOW` returns whatever the server thinks of the setting.

    A parser that only handled the form this project happened to write would
    report `None` - indistinguishable from "could not be measured" - the moment
    somebody changed `STATEMENT_TIMEOUT_MS` to `5min`.
    """
    assert _parse_postgres_interval_ms(raw) == expected


def test_the_timeout_classifier_does_not_depend_on_message_text():
    """SQLSTATE, not the words.

    The English message is a courtesy; the code is the contract. A test that
    only matched the message would break under a localised server and would
    miss a cancellation raised by something other than the timeout.
    """
    import psycopg

    cancelled = psycopg.errors.QueryCanceled("whatever the server says")
    assert is_query_timeout(cancelled)

    # Unrelated errors are not timeouts.
    assert not is_query_timeout(psycopg.errors.SyntaxError("syntax error"))
    assert not is_query_timeout(psycopg.errors.UndefinedTable("no such table"))

    # And the classifier survives being handed a bare string, which is what a
    # caller would hand it if they caught the wrong exception type.
    assert not is_query_timeout(RuntimeError("connection reset"))


def test_the_measured_timeout_is_none_when_the_database_is_absent(monkeypatch):
    """A health check must degrade, not raise, when it cannot measure."""
    from app.core import config as config_module

    monkeypatch.setattr(
        config_module, "get_settings", lambda: _unreachable_settings()
    )
    monkeypatch.setattr(
        "app.database.readonly.get_readonly_engine",
        lambda: _broken_engine(),
    )
    assert statement_timeout_ms_in_force() is None


def _unreachable_settings():
    settings = get_settings().model_copy(update={"postgres_port": 1})
    return settings


def _broken_engine():
    from sqlalchemy import create_engine

    return create_engine(
        _unreachable_settings().readonly_database_url,
        future=True,
        connect_args={"connect_timeout": 1},
    )