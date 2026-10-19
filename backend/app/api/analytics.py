"""Endpoints for the 41 verified analytics queries.

WHAT THIS ROUTER DELIBERATELY DOES NOT HAVE
-------------------------------------------
No endpoint that accepts SQL. A `/query?sql=...` route is the single most
dangerous thing this API could expose: it converts a read-only role into a
general-purpose execution surface for anyone who can reach the port, and prompt
injection in the AI phase would turn it into remote code execution against the
data.

Generating SQL from a natural-language question is a real requirement, but it
needs a validator, an allowlist and a refusal path that explains itself. Those
belong to the NL-to-SQL phase, and shipping only the dangerous half now would
mean the safety controls get written later, under time pressure, against an
interface nobody has thought about. So the catalogue is fixed and named here.

Every response is real query output. Nothing is cached, mocked or invented.

WHAT A CALLER SHOULD KNOW ABOUT COST
------------------------------------
Two of the 41 queries (`m5.demand_scale_by_hierarchy`, `m5.price_and_demand`)
aggregate tens of millions of rows and take minutes, measured rather than
guessed. They are not hidden, because hiding a verified query would be a lie
about what this API can do. They are bounded instead:

* every read-only connection carries a `statement_timeout`, so a query that
  outruns it returns 504 rather than holding a pooled connection open
* `include_total=false` skips the unpaginated `count(*)`, which on those
  aggregates costs as much as the page itself
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import require_analytics
from app.core.config import get_settings
from app.database.readonly import get_readonly_session
from app.schemas.responses import QueryListResponse, QueryResultResponse, QuerySummary
from app.services.analytics import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    execute_query,
    timed_out,
)
from app.services.query_catalogue import SOURCES, load_catalogue, resolve

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/queries", response_model=QueryListResponse)
def list_queries(
    source: str | None = Query(
        None, description="Filter to one source: olist, dataco, dunnhumby, m5"
    ),
    _principal=Depends(require_analytics),
) -> QueryListResponse:
    """List the catalogued queries, without running any of them.

    Cheap by construction: it reads the .sql files and returns the metadata. A
    dashboard can build its whole navigation from this one call.
    """
    if source is not None and source not in SOURCES:
        raise HTTPException(
            status_code=404,
            detail=f"unknown source {source!r}; expected one of {', '.join(SOURCES)}",
        )

    catalogue = load_catalogue()
    selected = [q for q in catalogue.values() if source is None or q.source == source]
    selected.sort(key=lambda q: (q.source, q.name))

    return QueryListResponse(
        total=len(selected),
        sources=[s for s in SOURCES if any(q.source == s for q in selected)],
        queries=[
            QuerySummary(
                name=q.qualified_name,
                source=q.source,
                question=q.question,
                file=q.file,
            )
            for q in selected
        ],
    )


@router.get("/queries/{name}", response_model=QueryResultResponse)
def run_query(  # noqa: PLR0913 - each parameter is a documented API surface
    name: str,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    sort_by: str | None = Query(
        None,
        description="Column to sort on. Ignored unless it is a column this "
        "query actually returns - a sort key that reached the statement "
        "unfiltered would be SQL injection.",
    ),
    descending: bool = Query(False),
    include_total: bool = Query(
        True,
        description="Compute the unpaginated row count. Set false to skip it - "
        "on the m5 aggregates the count costs as much as the page itself, and "
        "the response then reports total_rows: null rather than a guess.",
    ),
    _principal=Depends(require_analytics),
    session: Session = Depends(get_readonly_session),
) -> QueryResultResponse:
    """Run one catalogued query and return a page of its rows.

    Accepts either the qualified name (`olist.review_coverage_and_score`) or the
    bare one when it is unambiguous.
    """
    query = resolve(name)
    if query is None:
        catalogue = load_catalogue()
        # Distinguish "no such query" from "ambiguous name" - a 404 for an
        # ambiguous name sends the caller looking for a typo that isn't there.
        ambiguous = [q.qualified_name for q in catalogue.values() if q.name == name]
        if ambiguous:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{name!r} exists in more than one source; use the qualified "
                    f"name: {', '.join(sorted(ambiguous))}"
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=(
                f"no query named {name!r}. GET /api/analytics/queries lists all "
                f"{len(catalogue)}."
            ),
        )

    try:
        result = execute_query(
            session,
            query.sql,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            descending=descending,
            skip_total=not include_total,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a 502 or 504, not a 500
        # A timeout is not a broken query, and reporting it as 502 would tell
        # the caller the database rejected the SQL - which is false, and sends
        # them looking for a syntax error in a query that is fine. 504 says the
        # opposite: the statement was valid and it ran out of time.
        if timed_out(exc):
            raise HTTPException(
                status_code=504,
                detail=(
                    f"{query.qualified_name} exceeded the "
                    f"{get_settings().statement_timeout_ms} ms statement "
                    f"timeout and PostgreSQL cancelled it. This query is one of "
                    f"the slow catalogued queries; retry with a smaller limit, "
                    f"or run it through "
                    f"backend/scripts/sql/run_analytics.py, which has no "
                    f"request timeout."
                ),
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=(
                f"the database rejected {query.qualified_name}: "
                f"{type(exc).__name__}"
            ),
        ) from exc

    body = QueryResultResponse(
        query=query.qualified_name,
        source=query.source,
        question=query.question,
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        total_rows=result.total_rows,
        total_excluded=result.total_excluded,
        limit=limit,
        offset=offset,
        elapsed_ms=result.elapsed_ms,
        truncated=len(result.rows) == limit,
        # Said in the body rather than only in a header. A response header is
        # invisible to anyone reading the JSON, and "my sort did nothing" is
        # exactly the sort of thing a caller needs to be told about explicitly.
        sort_ignored=sort_by if result.sort_ignored else None,
    )

    if result.sort_ignored:
        return JSONResponse(
            status_code=200,
            content=body.model_dump(mode="json"),
            headers={"X-Sort-Ignored": f"{sort_by!r} is not a column this query returns"},
        )
    return body