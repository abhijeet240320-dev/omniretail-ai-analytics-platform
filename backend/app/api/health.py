"""Health and database status.

WHY THIS EXISTS RATHER THAN A STATIC "OK"
-----------------------------------------
An API that answers `/health` with a hardcoded 200 is not reporting anything. It
is telling you the process is alive and nothing about whether the 106 million rows
behind it are reachable, or whether the read-only role still holds only SELECT.

Both of those can break silently. The database can be stopped by hand during
development - which happens - and the role can be granted something extra by a
migration nobody reviewed. Both are exactly the kind of thing that should show up
in monitoring rather than in a support ticket.

Row counts are read live rather than served from a cached report, because a
health check that reports a stale number is worse than one that reports nothing:
it looks like evidence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import get_settings
from app.database.readonly import (
    get_readonly_session,
    statement_timeout_ms_in_force,
    verify_readonly,
)
from app.schemas.responses import DatabaseStatus, HealthResponse, TableInfo

logger = logging.getLogger("omniretail.api")

router = APIRouter(prefix="/api", tags=["health"])

SOURCE_SCHEMAS = ("olist", "dataco", "dunnhumby", "m5")


@router.get("/health", response_model=HealthResponse)
def health(session: Session = Depends(get_readonly_session)) -> HealthResponse:
    """Liveness plus the one property that must never fail: read-only access."""
    try:
        session.execute(text("SELECT 1")).scalar()
        reachable = True
    except Exception as exc:  # noqa: BLE001 - a health check reports, not raises
        return HealthResponse(
            status="degraded",
            database_reachable=False,
            read_only_enforced=False,
            detail=f"{type(exc).__name__}: the database is not reachable",
        )

    try:
        role = verify_readonly()
        enforced = bool(role["is_select_only"] and role["role_default_read_only"])
    except Exception as exc:  # noqa: BLE001
        return HealthResponse(
            status="degraded",
            database_reachable=True,
            read_only_enforced=False,
            statement_timeout_ms=statement_timeout_ms_in_force(),
            detail=f"{type(exc).__name__}: could not verify the read-only role",
        )

    if not enforced:
        return HealthResponse(
            status="degraded",
            database_reachable=True,
            read_only_enforced=False,
            statement_timeout_ms=statement_timeout_ms_in_force(),
            detail=(
                "the omniretail_readonly role no longer guarantees read-only "
                "access; see /api/status"
            ),
        )

    # The query cost guard is checked the same way the role is: by asking.
    # `STATEMENT_TIMEOUT_MS` in configuration is an intention; `SHOW
    # statement_timeout` on a live connection is the fact. On a database where
    # the default is "wait forever", the difference between the two is the whole
    # point - a misconfigured guard that a health endpoint reports as working is
    # worse than no guard, because it is believed.
    configured_timeout = get_settings().statement_timeout_ms
    measured_timeout = statement_timeout_ms_in_force()
    if configured_timeout and measured_timeout != configured_timeout:
        return HealthResponse(
            status="degraded",
            database_reachable=True,
            read_only_enforced=True,
            statement_timeout_ms=measured_timeout,
            detail=(
                f"STATEMENT_TIMEOUT_MS is {configured_timeout} but the read-only "
                f"connections report {measured_timeout!r} ms; the query cost "
                f"guard is not what the configuration says it is"
            ),
        )

    return HealthResponse(
        status="ok",
        database_reachable=True,
        read_only_enforced=True,
        statement_timeout_ms=measured_timeout,
    )


@router.get("/status", response_model=DatabaseStatus)
def status(
    _principal=Depends(require_admin),
    session: Session = Depends(get_readonly_session),
) -> DatabaseStatus:
    """Table inventory with live row counts, plus the Alembic revision.

    Row counts on 30 tables including a 59-million-row one are not free, so this
    is heavier than `/health`. It is the endpoint a dashboard loads once, not one
    to poll.

    Requires `admin:read`, unlike `/health`. This one enumerates every table,
    its column count and its approximate row count - which is reconnaissance
    worth having before an attack rather than a convenience. `/health` stays
    unauthenticated so a load balancer can reach it without a credential.
    """
    settings = get_settings()
    role = verify_readonly()

    tables: list[TableInfo] = []
    rows = session.execute(
        text(
            "SELECT c.relname AS table_name, "
            "       n.nspname AS schema_name, "
            "       c.reltuples::bigint AS approx_rows, "
            "       (SELECT count(*) FROM information_schema.columns col "
            "         WHERE col.table_schema = n.nspname "
            "           AND col.table_name = c.relname) AS columns "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = ANY(:schemas) AND c.relkind = 'r' "
            "ORDER BY n.nspname, c.relname"
        ),
        {"schemas": list(SOURCE_SCHEMAS)},
    ).mappings()

    # reltuples is the planner's estimate, which is good enough for a status
    # page and far cheaper than 30 COUNT(*) calls. Exact counts live in
    # /api/analytics and in data/processed/_load_report.json.
    for row in rows:
        tables.append(
            TableInfo(
                schema_name=row["schema_name"],
                table_name=row["table_name"],
                rows=max(int(row["approx_rows"]), 0),
                columns=int(row["columns"]),
            )
        )

    return DatabaseStatus(
        database=settings.postgres_db,
        reachable=True,
        role=str(role["role"]),
        is_select_only=bool(role["is_select_only"]),
        role_default_read_only=bool(role["role_default_read_only"]),
        alembic_revision=_alembic_revision(session),
        table_count=len(tables),
        total_rows=sum(t.rows for t in tables),
        tables=tables,
    )


@router.get("/rate-limit", summary="What the rate limiter is actually configured to do")
def rate_limit_status(
    _principal=Depends(require_admin),
) -> dict:
    """The limiter's own configuration and its stated limits.

    Reported rather than asserted in a README, for the same reason as
    `/api/integrations/powerbi/status`: a limit that silently multiplies across
    uvicorn workers is worse than no limit, and the only way a reader finds out
    is by being told.
    """
    from app.main import limiter_for_status

    return limiter_for_status().limitations()


def _alembic_revision(session: Session) -> str | None:
    """Current revision, or None.

    Returns None - rather than raising - in three separate cases, because all
    three are legitimate states that a status endpoint must report rather than
    crash on:

      1. the table does not exist (the database was never stamped)
      2. the read-only role cannot SELECT it (it holds no privilege on `public`)
      3. the table exists but is empty

    Case 2 is the one that bit: `omniretail_readonly` holds SELECT on the four
    source schemas and nothing in `public`, so the first version of this function
    made `/api/status` return a 500 on a perfectly healthy database. A status
    endpoint that fails when things are fine is worse than useless - it teaches
    people to ignore it.
    """
    try:
        exists = session.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ).scalar()
        if not exists:
            return None
        row = session.execute(
            text("SELECT version_num FROM public.alembic_version LIMIT 1")
        ).scalar()
        return str(row) if row else None
    except SQLAlchemyError:
        # no privilege on public.alembic_version, or the table is unreadable
        logger.info(
            "alembic revision unavailable to the read-only role; reporting None"
        )
        return None