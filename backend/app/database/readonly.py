"""The API's database access, which is read-only by construction.

WHY A SEPARATE ENGINE RATHER THAN THE EXISTING ONE
--------------------------------------------------
`app/database/session.py` builds an engine from `settings.database_url` - the
read/write `omniretail` role, because the loader and the migrations need it.

An API that queries analytics has no reason to hold credentials that can change
106 million rows. Using the read-only role here means that even a completely
wrong API - a SQL injection, a bug in a generated query, a `DELETE` that slipped
through a review - is refused by PostgreSQL rather than by our good intentions.

Three layers, because each can fail independently:

  1. the ROLE      - `omniretail_readonly` holds SELECT and nothing else
  2. the SESSION   - `default_transaction_read_only = on`, set at role level, so
                     a forgotten `SET TRANSACTION` still cannot open a writable
                     session
  3. this module   - an explicit `SET TRANSACTION READ ONLY` per request, which
                     is the layer that produces a clear error instead of a
                     confusing one

The engine is separate so the read/write credentials are not merely unused -
they are not present in the API process at all.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

# libpq passes these straight to the server as `-c name=value` at connect time,
# so they apply to EVERY statement on that connection - including statements
# issued by SQLAlchemy itself, by Alembic's inspection queries, and by any code
# path added later. That is the property that matters: a guard implemented as a
# `SET LOCAL` inside one dependency is a guard the next endpoint can forget.
#
# `application_name` is not a guard, it is attribution: a runaway query is then
# identifiable in `pg_stat_activity` instead of anonymous.
_STATEMENT_TIMEOUT_SQLSTATE = "57014"  # query_canceled
_LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"  # lock_not_available


def _connect_options() -> dict[str, str]:
    """libpq connection options for the read-only engine."""
    settings = get_settings()
    options = ["-c application_name=omniretail-api"]
    # `statement_timeout = 0` means "no limit" in PostgreSQL. Emitting
    # `-c statement_timeout=0` when it is disabled keeps the behaviour
    # explicit rather than relying on the server default, so a change to the
    # server config cannot silently re-arm or disarm the guard.
    options.append(f"-c statement_timeout={int(settings.statement_timeout_ms)}")
    return {"options": " ".join(options)}


def is_query_timeout(exc: BaseException) -> bool:
    """True when PostgreSQL refused a statement on a time limit.

    Detected from the SQLSTATE rather than the message text, because the message
    is localised and the code is not. Both `57014` (query_canceled - what
    `statement_timeout` raises) and `55P03` (lock_not_available - what
    `lock_timeout` raises) count: both mean "you waited too long", and both are
    a 504 rather than a 502.

    The exception chain is walked because SQLAlchemy wraps the driver error, so
    the useful `sqlstate` is usually two levels down on `.orig`.
    """
    seen: set[int] = set()
    candidate: BaseException | None = exc
    while candidate is not None and id(candidate) not in seen:
        seen.add(id(candidate))
        code = (
            getattr(candidate, "sqlstate", None)
            or getattr(candidate, "pgcode", None)
        )
        if code in (_STATEMENT_TIMEOUT_SQLSTATE, _LOCK_NOT_AVAILABLE_SQLSTATE):
            return True
        candidate = getattr(candidate, "orig", None) or candidate.__cause__
    return False


def statement_timeout_ms_in_force() -> int | None:
    """What the SERVER says the timeout is, in milliseconds.

    Not `settings.statement_timeout_ms`. The configured value is an intention;
    this asks a live connection what it actually got, which is the only version
    of the claim that `/api/health` can report honestly. Returns `None` if the
    database is unreachable, so a health check degrades rather than raises.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = get_readonly_engine()
        with engine.connect() as conn:
            raw = conn.execute(text("SHOW statement_timeout")).scalar()
    except SQLAlchemyError:
        return None
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    # PostgreSQL accepts `5min`, `300s`, `1h`, `300` - all of which are legal
    # here, so parsing has to go through its own unit syntax rather than
    # assuming milliseconds.
    try:
        return _parse_postgres_interval_ms(raw)
    except ValueError:
        return None


def _parse_postgres_interval_ms(raw: str) -> int:
    """Convert a `SHOW statement_timeout` value to milliseconds.

    Accepts the three shapes PostgreSQL accepts: a bare count of milliseconds,
    a `s` suffix, and a compound `min s` / `h min s` string.
    """
    text_ = raw.lower().strip()
    if text_.endswith("ms"):
        return int(float(text_[:-2]))
    parts = text_.split()
    total_ms = 0.0
    multipliers = {"h": 3_600_000.0, "min": 60_000.0, "s": 1_000.0, "ms": 1.0}
    matched = False
    for part in parts:
        matched = False
        for unit, factor in multipliers.items():
            if part.endswith(unit):
                total_ms += float(part[: -len(unit)]) * factor
                matched = True
                break
        if not matched:
            # No unit suffix: PostgreSQL's bare number means milliseconds.
            total_ms += float(part)
    if not parts:
        raise ValueError(f"unparseable timeout {raw!r}")
    return int(total_ms)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_readonly_engine() -> Engine:
    """Process-wide read-only engine.

    Lazy, like the read/write one, so importing the app does not require a
    reachable database - which matters because `alembic` and the tests both
    import it.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.readonly_database_url,
            pool_pre_ping=True,
            future=True,
            connect_args=_connect_options(),
        )
    return _engine


def get_readonly_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_readonly_engine(), expire_on_commit=False
        )
    return _session_factory


def get_readonly_session() -> Iterator[Session]:
    """FastAPI dependency: one read-only session per request.

    The session is closed by FastAPI when the response is sent, including on an
    exception path, so a failing endpoint cannot leak a pooled connection - which
    on a 30-table, 106-million-row database shows up as the whole API hanging.
    """
    session = get_readonly_session_factory()()
    try:
        # Belt-and-braces. Layer 1 (the role) and layer 2 (the role setting)
        # already refuse writes; this makes the refusal a clear, immediate error
        # rather than a deferred one.
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        session.close()


def verify_readonly() -> dict:
    """Prove at runtime that this session cannot write.

    Called by the health endpoint. A claim that the API is read-only is worth
    exactly as much as the measurement behind it, and this is the measurement:
    it asks PostgreSQL what the role actually holds rather than trusting the
    bootstrap script that granted it.
    """
    session = get_readonly_session_factory()()
    try:
        privileges = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT DISTINCT privilege_type FROM "
                    "information_schema.role_table_grants "
                    "WHERE grantee = 'omniretail_readonly'"
                )
            )
        }
        settings_says = None
        for setdatabase, config in session.execute(
            text(
                "SELECT setdatabase, array_to_string(setconfig, ',') "
                "FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                "WHERE r.rolname = 'omniretail_readonly'"
            )
        ):
            if setdatabase == 0 and "default_transaction_read_only=on" in (config or ""):
                settings_says = True
        return {
            "role": get_settings().postgres_readonly_user,
            "privileges": sorted(privileges),
            "is_select_only": privileges <= {"SELECT"},
            "role_default_read_only": bool(settings_says),
        }
    finally:
        session.close()