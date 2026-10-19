"""The API key expiry check, including the timezone bug it used to have.

THE BUG
-------
`expires_at` is a `timestamp without time zone` column. This cluster's session
timezone is Asia/Calcutta (UTC+05:30), so the value written into it comes from
the server's LOCAL clock.

The check compared that value in Python against `datetime.now(timezone.utc)`.
Since psycopg hands back a naive datetime, the code stamped it with UTC and
compared - which reads the local instant as if it were UTC and therefore sees it
as **(server_local + 5:30) hours in the future**.

Measured consequence on this machine: a key whose `expires_at` was one minute in
the past, in server local time, authenticated successfully. The window in which
an expired key keeps working is exactly the UTC offset, and it is silent.

THE FIX
-------
The comparison moved into the SELECT. `expires_at < now()` puts both sides in
the same session timezone by construction, so there is no assumption left to get
wrong, and it follows the server if the timezone changes.

These tests are written against the database rather than against a mock,
because the whole defect was a disagreement between two clocks and a fake would
not have disagreed.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.deps import authenticate


@pytest.fixture()
def writer():
    from sqlalchemy import create_engine

    from app.core.config import get_settings

    return create_engine(get_settings().database_url, future=True)


@pytest.fixture()
def session():
    """A real READ-ONLY session, the same one `authenticate` receives in production.

    `authenticate` declares `session: Session = Depends(get_readonly_session)`,
    so calling it directly passes the `Depends` marker rather than a Session.
    Building the session here is also what makes this a test of the real thing:
    the read-only session and the writer session are different connections with
    different roles, and the expiry decision is made by the reader.
    """
    from app.database.readonly import get_readonly_session_factory

    factory = get_readonly_session_factory()
    with factory() as real:
        yield real


def auth(session, key: str):
    """Call `authenticate` with a real session, unwrapping the HTTPException."""
    from fastapi import HTTPException

    try:
        return authenticate(session=session, x_api_key=key), None
    except HTTPException as exc:
        return None, exc


def _mint(writer, label: str, scopes: str = "admin:read,ai:read,analytics:read") -> str:
    import re
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [str(root / ".venv/Scripts/python.exe"),
         str(root / "backend/scripts/db/08_issue_api_key.py"),
         "issue", "--label", label, "--scopes", scopes],
        capture_output=True, text=True, cwd=root, check=True,
    ).stdout
    match = re.search(r"\bomr_[A-Za-z0-9_-]{40,}\b", out)
    assert match, f"could not read the issued key:\n{out}"
    return match.group(0)


def _backdate(writer, label: str) -> None:
    """Set the expiry one minute into the past, using the SERVER's clock.

    `now()` rather than a Python datetime on purpose: the bug was precisely that
    the two clocks disagree, so a test that supplies a Python-side timestamp
    would not reproduce it.
    """
    with writer.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.api_keys SET expires_at = now() - interval '1 minute' "
                "WHERE label = :l"
            ),
            {"l": label},
        )


def _clean(writer, label: str) -> None:
    with writer.begin() as conn:
        conn.execute(text("DELETE FROM app.api_keys WHERE label = :l"), {"l": label})


def test_an_expired_key_is_refused(writer, session):
    label = "pytest-tz-expired"
    key = _mint(writer, label)
    try:
        _backdate(writer, label)
        principal, exc = auth(session, key)
        assert principal is None, "an expired key was accepted"
        assert exc.status_code == 401
        assert "expired" in exc.detail.lower()
    finally:
        _clean(writer, label)


def test_the_refusal_does_not_depend_on_the_process_timezone(writer, session):
    """The regression that matters: the server clock is local, the check is not.

    Sets `expires_at` to one minute ago by the SERVER's clock. Before the fix
    this authenticated, because the naive local timestamp was read as UTC.
    """
    label = "pytest-tz-offset"
    key = _mint(writer, label)
    try:
        with writer.connect() as conn:
            offset_hours = conn.execute(
                text(
                    "SELECT extract(epoch FROM (now() - now() AT TIME ZONE 'UTC')) / 3600"
                )
            ).scalar()

        _backdate(writer, label)
        principal, _ = auth(session, key)

        assert principal is None, (
            f"an expired key authenticated; the server is {float(offset_hours):.2f} "
            f"hours from UTC and the check is not accounting for it"
        )
    finally:
        _clean(writer, label)


def test_an_unexpired_key_is_accepted(writer, session):
    label = "pytest-tz-valid"
    key = _mint(writer, label)
    try:
        principal, exc = auth(session, key)
        assert exc is None, f"a valid key was refused: {exc and exc.detail}"
        assert principal is not None and principal.key_id > 0
    finally:
        _clean(writer, label)


def test_a_future_expiry_is_accepted(writer, session):
    label = "pytest-tz-future"
    key = _mint(writer, label)
    try:
        with writer.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.api_keys SET expires_at = now() + interval '1 hour' "
                    "WHERE label = :l"
                ),
                {"l": label},
            )
        principal, exc = auth(session, key)
        assert exc is None, f"a key with a future expiry was refused: {exc and exc.detail}"
        assert principal is not None
    finally:
        _clean(writer, label)


def test_a_null_expiry_never_expires(writer, session):
    label = "pytest-tz-null"
    key = _mint(writer, label)
    try:
        with writer.connect() as conn:
            value = conn.execute(
                text("SELECT expires_at FROM app.api_keys WHERE label = :l"),
                {"l": label},
            ).scalar()
        assert value is None, "the issuer should leave this key non-expiring"
        principal, exc = auth(session, key)
        assert exc is None
        assert principal is not None
    finally:
        _clean(writer, label)


def test_the_expiry_decision_is_made_in_sql(writer):
    """Guards against someone 'fixing' it by moving the comparison back to Python.

    The bug and the naive fix look identical in a diff, and only one of them is
    correct. Asserting that the column is selected as a boolean computed by the
    server means a future refactor that reintroduces `datetime.now(timezone.utc)`
    here fails loudly.
    """
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "app" / "api" / "deps.py"
    ).read_text(encoding="utf-8")

    assert "expires_at < now()" in source, (
        "the expiry comparison is no longer done in SQL; the server-local "
        "timestamp will be read as UTC again"
    )
    assert "datetime.now(_dt.timezone.utc)" not in source, (
        "the expiry check is comparing a naive server-local timestamp against "
        "UTC in Python again"
    )
