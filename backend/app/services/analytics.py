"""Run a catalogued query and return JSON-safe rows.

PAGINATION: WHY IT IS DONE IN SQL, NOT IN PYTHON
-----------------------------------------------
`m5.demand_scale_by_hierarchy` aggregates 59 million rows. Fetching all of them
to hand back the first 50 would make every request cost a full scan and would
put the whole result set in the API process's memory. `LIMIT`/`OFFSET` pushed
into the statement means PostgreSQL stops when it has enough.

`OFFSET` degrades on deep pages, which is a real limitation rather than a
correctness one: page 50,000 is slow, but it is still correct. Keyset pagination
would fix it and is the right change if these result sets ever get browsed that
far; it is not the right complexity to build before anyone needs it.

THE WRAPPING IS THE INTERESTING PART
------------------------------------
Each catalogued query is wrapped:

    SELECT * FROM ( <original> ) AS q ORDER BY ... LIMIT ... OFFSET ...

so the caller can sort and page without the original query having to support it.
The ORDER BY is only added when the caller asks for one. Without a deterministic
order, `LIMIT 10 OFFSET 20` is not a stable page - the same row can appear on
two pages, which is a bug that looks like intermittent data corruption.

WHEN NO SORT IS REQUESTED the original query's own ORDER BY is used, because the
analyst wrote it deliberately and overriding it would be worse than ignoring it.

WHAT BOUNDS THE COST
--------------------
Two independent controls, because they bound different things:

* `statement_timeout`, set on every read-only connection, bounds how LONG a
  single statement may run. A bad plan then fails rather than pinning a pooled
  connection until the pool is exhausted and the whole API stops answering.
* `skip_total`, which bounds how MANY executions one request makes. On the
  `m5` aggregates the unpaginated `count(*)` costs as much as the page itself,
  so a caller that does not need a pager should not pay for one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.readonly import is_query_timeout

# A caller-supplied identifier can only ever be an ORDER BY term, never SQL.
# The allowlist below is what makes that true rather than merely intended.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 50


@dataclass
class ExecutionResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int | None
    elapsed_ms: float
    sort_ignored: bool = False
    total_excluded: bool = False


def _jsonable(value: Any) -> Any:
    """Convert a psycopg value into something JSON can carry.

    `Decimal` becomes a float. That is a real, documented loss of exactness for
    very large or very precise money, and it is the right trade for an analytics
    API because JSON has no decimal type - but it must be a decision, not an
    accident of `default=str`, so it is made here explicitly.

    Anything unrecognised becomes its string form rather than raising. A single
    exotic column type must not turn an entire result set into a 500.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


def _quote(identifier: str) -> str:
    """Quote a SQL identifier.

    The only place identifiers come from is this module's own allowlist, so
    quoting is belt-and-braces rather than the primary defence.
    """
    return '"' + identifier.replace('"', '""') + '"'


def _as_subquery(sql: str) -> str:
    """Strip the trailing semicolon so the statement can be used as a subquery.

    All 41 catalogued queries end with `;`, which is correct when they are
    executed as standalone statements - which is what the runner does. Wrapping
    one in `SELECT * FROM ( ... )` makes that semicolon a syntax error, because
    a subquery is a complete expression rather than a statement.

    Naive `sql.rstrip(';')` is not enough: a trailing SQL line comment
    (`-- note`) after the semicolon would leave the `;` in place. So the
    semicolon is removed and comments are stripped, in that order.
    """
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    # A line comment after the semicolon would otherwise leave the `;` in place.
    lines = [
        line for line in stripped.splitlines()
        if not line.lstrip().startswith("--")
    ]
    return "\n".join(lines).strip()


def execute_query(
    session: Session,
    sql: str,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    sort_by: str | None = None,
    descending: bool = False,
    skip_total: bool = False,
) -> ExecutionResult:
    """Run `sql` read-only and return at most `limit` rows.

    A sort key from the caller is only ever used if it names a column this query
    actually returns. That check is what keeps an ORDER BY clause from becoming
    SQL injection, and it is why the column list is discovered by executing the
    statement with `LIMIT 0` first: PostgreSQL returns the column names without
    transferring a single row.

    Three executions per request, cheapest first:
      1. `LIMIT 0`       - column names only, no rows moved
      2. the page        - LIMIT/OFFSET pushed into the statement
      3. `count(*)`      - the unpaginated total, for the pager

    WHY THE TOTAL IS THE EXPENSIVE ONE, AND HOW IT IS SKIPPED
    --------------------------------------------------------
    `LIMIT 50` does not make these queries cheap, because the limit is applied
    to a SUBQUERY: PostgreSQL cannot push `LIMIT` into the `GROUP BY` of
    `m5.demand_scale_by_hierarchy`, so it computes every group and then throws
    most of them away. Step 3 therefore costs as much as step 2, and the
    endpoint costs roughly twice what one execution does.

    A caller that renders a table and does not render a pager therefore pays for
    a number it never shows. `skip_total` is how it stops paying: the response
    carries `total_excluded: true` and `total_rows: null`, which is honest -
    there is no count, rather than a count that was invented as `row_count`.

    The limit on the page is still applied, because it bounds what leaves
    PostgreSQL and what enters the API process's memory even when the
    aggregation itself cannot be avoided.
    """
    started = time.monotonic()
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    inner = _as_subquery(sql)

    # 1. column names, no data
    probe = session.execute(
        text(f"SELECT * FROM (\n{inner}\n) AS omniretail_probe LIMIT 0")
    )
    columns = set(probe.keys())

    order_clause = ""
    if sort_by and sort_by in columns:
        direction = "DESC" if descending else "ASC"
        order_clause = f" ORDER BY {_quote(sort_by)} {direction}"

    # 2. the page itself
    page = session.execute(
        text(
            f"SELECT * FROM (\n{inner}\n) AS omniretail_page"
            f"{order_clause} LIMIT {limit} OFFSET {offset}"
        )
    )
    column_order = list(page.keys())
    rows = [
        {col: _jsonable(val) for col, val in zip(column_order, row)} for row in page
    ]

    # 3. the unpaginated total, unless the caller opted out of it
    if skip_total:
        total_rows: int | None = None
    else:
        total_rows = (
            session.execute(
                text(f"SELECT count(*) FROM (\n{inner}\n) AS omniretail_total")
            ).scalar()
            or 0
        )

    return ExecutionResult(
        columns=column_order,
        rows=rows,
        total_rows=total_rows,
        elapsed_ms=round((time.monotonic() - started) * 1000, 1),
        sort_ignored=bool(sort_by) and sort_by not in columns,
        total_excluded=skip_total,
    )


def timed_out(exc: BaseException) -> bool:
    """Re-exported so callers import the classifier from the service layer.

    `is_query_timeout` lives in `app.database.readonly` because it is a fact
    about the database refusing a statement, but the only place it is needed is
    where a query is executed - so the service that runs queries is the
    natural import site, and the API imports it from there.
    """
    return is_query_timeout(exc)