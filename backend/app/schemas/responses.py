"""Response shapes for the API.

THE SERIALISATION RULE THAT MATTERS MOST HERE
--------------------------------------------
psycopg returns money as `Decimal` and dates as `date`/`datetime`. Neither is
JSON-serialisable, and the usual workaround - `json.dumps(..., default=str)` -
turns `Decimal("16008872.12")` into the string `"16008872.12"`.

That is how a revenue figure quietly becomes a *string* in a JSON response. The
frontend then does arithmetic on it, or renders it with string rules, and the
number is subtly wrong - or silently absent. `docs/sql_analytics.md` has the same
convention, so at least the two agree; but agreeing on a wart is not the same as
not having one.

So money and counts are declared `float`, which Pydantic coerces from Decimal
without the string round-trip, and the API contract says plainly that these are
JSON numbers. Dates stay `date`/`datetime`, which Pydantic formats as ISO 8601 -
a standard a frontend can parse without guessing.

Where precision genuinely matters, a string is the correct answer and is used
explicitly (see `MoneyLiteral` in the comments of the KPI router). Deciding that
per-field is the honest approach; a blanket `default=str` is neither.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class QuerySummary(BaseModel):
    """One analytics query, without its SQL body."""

    name: str = Field(description="Qualified name, e.g. olist.review_coverage_and_score")
    source: str = Field(description="Which dataset it belongs to: olist, dataco, dunnhumby, m5")
    question: str = Field(description="The business question this query answers")
    file: str = Field(description="Path to the .sql file it is defined in")


class QueryListResponse(BaseModel):
    total: int
    sources: list[str]
    queries: list[QuerySummary]


class QueryResultResponse(BaseModel):
    """The result of running one catalogued query.

    `rows` are dicts, not lists, because the column names are the only thing
    that makes a result interpretable. A list of lists is smaller and useless.
    """

    query: str
    source: str
    question: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(description="Rows in this page")
    total_rows: int | None = Field(
        description="Rows the query returns without LIMIT/OFFSET, or null when "
        "the caller passed include_total=false and the count was not computed. "
        "Null means 'not measured' - it is never a substitute for row_count."
    )
    total_excluded: bool = Field(
        default=False,
        description="True when the unpaginated count was deliberately skipped "
        "for cost reasons, which on the m5 aggregates saves as much time as "
        "the page itself.",
    )
    limit: int
    offset: int
    elapsed_ms: float
    truncated: bool = Field(
        description="True when row_count == limit, so more rows may exist"
    )
    sort_ignored: str | None = Field(
        default=None,
        description="Set to the requested sort key when it is not a column this "
        "query returns. The sort was dropped, not applied.",
    )


class SourceKpi(BaseModel):
    """KPI values for exactly one source.

    Deliberately per-source and never summed. Four unrelated companies have no
    shared key space, so a combined "total revenue" has no referent. The shape
    makes that structural: there is nowhere in this response to put a total
    across sources without constructing one deliberately and incorrectly.
    """

    source: str
    label: str
    metrics: dict[str, Any] = Field(
        description="Metric name -> value. Values are JSON numbers or strings, "
        "never Decimal or datetime."
    )
    note: str | None = None


class OverviewResponse(BaseModel):
    """Per-source KPIs. There is deliberately no cross-source total."""

    generated_at: datetime
    sources: list[SourceKpi]
    disclaimer: str = Field(
        description="Why there is no combined figure across sources"
    )
    cached: bool = Field(
        default=False,
        description="True when this response was reused from the in-process "
        "cache rather than recomputed. `generated_at` is when the numbers were "
        "actually computed, so a cached response is never mistaken for a fresh "
        "one.",
    )
    age_seconds: float = Field(
        default=0.0,
        description="How long ago the numbers were computed. 0 on a fresh "
        "response.",
    )


class TableInfo(BaseModel):
    schema_name: str
    table_name: str
    rows: int
    columns: int


class DatabaseStatus(BaseModel):
    database: str
    reachable: bool
    role: str
    is_select_only: bool
    role_default_read_only: bool
    alembic_revision: str | None = None
    table_count: int = 0
    total_rows: int = 0
    tables: list[TableInfo] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = Field(description="'ok' or 'degraded'")
    database_reachable: bool
    read_only_enforced: bool
    detail: str | None = None
    statement_timeout_ms: int | None = Field(
        default=None,
        description="The statement timeout PostgreSQL reports on a live read-only "
        "connection, in milliseconds. Measured, not copied from configuration, "
        "so it is evidence the guard is actually armed. 0 means unlimited.",
    )


class ErrorResponse(BaseModel):
    """A consistent error body.

    Internal detail deliberately does NOT include the SQL or a stack trace.
    A 500 that echoes the failing statement teaches an attacker the schema, and
    the same message in production and in development is how that leak ships.
    """

    error: str
    detail: str | None = None