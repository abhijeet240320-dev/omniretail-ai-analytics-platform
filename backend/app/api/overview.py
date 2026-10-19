"""Overview KPIs, one group per source.

THE MOST IMPORTANT THING THIS FILE DOES NOT DO
-----------------------------------------------
It does not produce a combined number. Four unrelated companies - a Brazilian
marketplace, a supply-chain operator, a US grocery panel, Walmart - have no
shared key space and no common definition of a sale. Summing their revenue
produces a number with no referent: not "total revenue", but a sum of four
incompatible quantities that happens to share a currency.

That is why `OverviewResponse` has `sources: list[SourceKpi]` and no `total`.
The shape makes the mistake unrepresentable rather than merely discouraged.

EVERY NUMBER IS QUERIED FROM THE DATABASE
-----------------------------------------
There is no hardcoded constant and no seed data: every metric is a `SELECT`
whose SQL is written out in `SOURCE_QUERIES` below, where it can be read and
traced to a column. If the database is empty the response says so rather than
showing a number that came from somewhere else.

THE CACHE, AND WHY IT IS DISCLOSED RATHER THAN HIDDEN
------------------------------------------------------
There are 26 metric queries here, several of which are full scans of
`m5.sales_long` (59.2M rows) or `dunnhumby.transactions` (2.6M). Measured, a
cold `/api/overview` takes about 80 seconds. That is not a bug that can be
fixed - the numbers have to come from the rows - but it does not have to be paid
twice.

So the computed response is held for `OVERVIEW_CACHE_TTL_SECONDS` and reused.
Three things keep that honest:

  * the payload carries `generated_at` (when the numbers were actually
    computed) and `cached` (whether THIS response came out of the cache), so a
    reader can never mistake a cached response for a fresh one;
  * a response containing any source-level `note` is never cached, so a
    transient failure is not pinned for the TTL;
  * the cache is process-local, so it dies with the API rather than outliving
    the data it describes.

The alternative - no cache, and an 80-second dashboard - was rejected because it
pushes the cost onto every visitor to buy freshness that changes only when the
data is reloaded, which happens through a separate batch job.

PER-SOURCE, NOT PER-DATASET-SPECIFIC
------------------------------------
Each source exposes the handful of figures that are meaningful for it. A grocery
panel has no revenue in the same sense as a marketplace; showing Olist revenue
next to it under one "revenue" label would be the same error one level down.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import require_analytics
from app.core.config import get_settings
from app.database.readonly import get_readonly_session
from app.schemas.responses import OverviewResponse, SourceKpi

router = APIRouter(prefix="/api/overview", tags=["overview"])

# How long a computed overview is reused. Two minutes is short enough that a
# dashboard left open over lunch still refreshes on a timer rather than showing
# yesterday, and long enough that the two pages which need these numbers
# (Overview and Data Sources) do not each pay the 80-second scan.
DEFAULT_CACHE_TTL_SECONDS = 120.0

_cache_lock = threading.Lock()
_cache: dict[str, Any] | None = None

DISCLAIMER = (
    "No combined total is provided, by design. The four datasets are unrelated "
    "companies with no shared key space and different definitions of a sale, so "
    "a sum across them would have no referent. Every figure below belongs to "
    "exactly one source."
)

# Each entry: label for the UI, and the SQL that produces ONE row.
# Kept as a declaration rather than assembled dynamically so that every figure
# on the dashboard can be read, reviewed and traced to a source.
SOURCE_QUERIES: dict[str, list[tuple[str, str]]] = {
    "olist": [
        (
            "orders",
            "SELECT count(*) AS orders FROM olist.orders",
        ),
        (
            "revenue",
            "SELECT round(sum(p.payment_value), 2) AS revenue "
            "FROM olist.payments p",
        ),
        (
            "customers",
            "SELECT count(DISTINCT customer_unique_id) AS customers "
            "FROM olist.customers",
        ),
        (
            "review_coverage_pct",
            "SELECT round(100.0 * count(DISTINCT o.order_id) "
            "  FILTER (WHERE r.order_id IS NOT NULL) "
            "  / count(DISTINCT o.order_id), 2) AS review_coverage_pct "
            "FROM olist.orders o LEFT JOIN olist.reviews r USING (order_id)",
        ),
        (
            "delivered_pct",
            "SELECT round(100.0 * count(*) FILTER (WHERE order_status = 'delivered') "
            "  / count(*), 2) AS delivered_pct FROM olist.orders",
        ),
        (
            # Date coverage, measured not documented. The docs claim Olist spans
            # 2016-2018; this is the actual min/max in the loaded rows, so the
            # claim and the data cannot drift apart silently.
            "coverage_start",
            "SELECT min(order_purchase_timestamp)::date AS coverage_start "
            "FROM olist.orders",
        ),
        (
            "coverage_end",
            "SELECT max(order_purchase_timestamp)::date AS coverage_end "
            "FROM olist.orders",
        ),
    ],
    "dataco": [
        (
            "orders",
            "SELECT count(*) AS orders FROM dataco.orders",
        ),
        (
            "revenue",
            "SELECT round(sum(sales), 2) AS revenue FROM dataco.order_items",
        ),
        (
            "profit",
            "SELECT round(sum(order_profit_per_order), 2) AS profit "
            "FROM dataco.order_items",
        ),
        (
            "avg_margin_pct",
            "SELECT round(avg(profit_margin * 100)::numeric, 2) AS avg_margin_pct "
            "FROM dataco.order_items",
        ),
        (
            "coverage_start",
            "SELECT min(order_date)::date AS coverage_start FROM dataco.orders",
        ),
        (
            "coverage_end",
            "SELECT max(order_date)::date AS coverage_end FROM dataco.orders",
        ),
    ],
    "dunnhumby": [
        (
            "basket_lines",
            "SELECT count(*) AS basket_lines FROM dunnhumby.transactions",
        ),
        (
            "gross_sales",
            "SELECT round(sum(gross_sales_value), 2) AS gross_sales "
            "FROM dunnhumby.transactions",
        ),
        (
            "discounts_given",
            "SELECT round(sum(total_discount), 2) AS discounts_given "
            "FROM dunnhumby.transactions",
        ),
        (
            # 801, not 2500: hh_demographic.csv covers 801 households. Reporting
            # 2500 here would be the single most misleading number in the API.
            "households_with_demographics",
            "SELECT count(*) AS households_with_demographics "
            "FROM dunnhumby.household_rfm WHERE classification_3 IS NOT NULL",
        ),
        (
            "households_transacting",
            "SELECT count(DISTINCT household_key) AS households_transacting "
            "FROM dunnhumby.transactions",
        ),
        (
            # NOT DATES. Dunnhumby's time axis is an integer `day` index and the
            # dataset ships no calendar to convert it with, so the only honest
            # coverage figure is the index range. Reporting these as dates would
            # be inventing a calendar the data does not contain - which is why
            # the key is named `day_index_*` and not `coverage_*`.
            "day_index_min",
            "SELECT min(day) AS day_index_min FROM dunnhumby.transactions",
        ),
        (
            "day_index_max",
            "SELECT max(day) AS day_index_max FROM dunnhumby.transactions",
        ),
    ],
    "m5": [
        (
            "series_days",
            "SELECT count(*) AS series_days FROM m5.sales_long",
        ),
        (
            "units",
            "SELECT sum(sales) AS units FROM m5.sales_long",
        ),
        (
            "stores",
            "SELECT count(DISTINCT store_id) AS stores FROM m5.series",
        ),
        (
            "items",
            "SELECT count(DISTINCT item_id) AS items FROM m5.series",
        ),
        (
            # 68% of series-days have zero sales. Intermittent demand, and the
            # reason a naive forecast on this data looks fine and is not.
            "zero_sales_day_pct",
            "SELECT round(100.0 * count(*) FILTER (WHERE sales = 0) / count(*), 2) "
            "AS zero_sales_day_pct FROM m5.sales_long",
        ),
        (
            "coverage_start",
            "SELECT min(date)::date AS coverage_start FROM m5.sales_daily_total",
        ),
        (
            "coverage_end",
            "SELECT max(date)::date AS coverage_end FROM m5.sales_daily_total",
        ),
    ],
}

SOURCE_LABELS = {
    "olist": "Olist - Brazilian marketplace",
    "dataco": "DataCo - supply chain",
    "dunnhumby": "Dunnhumby - US grocery panel",
    "m5": "M5 - Walmart daily sales",
}


@router.get("", response_model=OverviewResponse)
def get_overview(
    _principal=Depends(require_analytics),
    session: Session = Depends(get_readonly_session),
) -> OverviewResponse:
    """Per-source KPIs, computed from the database.

    Each source's queries are independent; one failing does not blank the whole
    response. A dashboard showing three sources and one error is more useful than
    one showing a 500, and the error is reported in that source's `note` rather
    than being swallowed.
    """
    global _cache

    ttl = _cache_ttl_seconds()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache
        fresh = hit is not None and ttl > 0 and (now - hit["stored_at"]) < ttl

    if fresh:
        return OverviewResponse(
            generated_at=hit["generated_at"],
            sources=hit["sources"],
            disclaimer=DISCLAIMER,
            cached=True,
            age_seconds=round(now - hit["stored_at"], 3),
        )

    sources, complete = _compute_overview(session)
    computed_at = datetime.now(timezone.utc)

    if complete and ttl > 0:
        # Only a clean result is cached. Pinning a partial one for two minutes
        # would turn a transient database hiccup into a visible wrong number.
        with _cache_lock:
            _cache = {
                "stored_at": time.monotonic(),
                "generated_at": computed_at,
                "sources": [s.model_dump(mode="json") for s in sources],
            }
    else:
        with _cache_lock:
            _cache = None

    return OverviewResponse(
        generated_at=computed_at,
        sources=sources,
        disclaimer=DISCLAIMER,
        cached=False,
        age_seconds=0.0,
    )


def _cache_ttl_seconds() -> float:
    """The TTL, overridable for a test that must see a cold response."""
    return getattr(
        get_settings(), "overview_cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS
    )


def cache_clear() -> None:
    """Drop the cached overview. Used by tests, and by nothing else."""
    global _cache
    with _cache_lock:
        _cache = None


def _compute_overview(session: Session) -> tuple[list[SourceKpi], bool]:
    """Run every metric query. Returns the KPIs and whether all of them worked.

    The 80 seconds is spent OUTSIDE the cache lock on purpose: holding a lock
    across a full scan of a 59-million-row table would serialise every other
    overview request behind it, including the one that could have served a
    perfectly good cached answer.
    """
    sources: list[SourceKpi] = []
    complete = True

    for source, queries in SOURCE_QUERIES.items():
        metrics: dict[str, object] = {}
        notes: list[str] = []
        for name, sql in queries:
            try:
                row = session.execute(text(sql)).mappings().one()
                for key, value in row.items():
                    metrics[key] = _jsonable(value)
            except Exception as exc:  # noqa: BLE001 - one metric, not the API
                notes.append(f"{name}: {type(exc).__name__}")
                complete = False

        sources.append(
            SourceKpi(
                source=source,
                label=SOURCE_LABELS.get(source, source),
                metrics=metrics,
                note="; ".join(notes) if notes else None,
            )
        )

    return sources, complete


def _jsonable(value):
    from app.services.analytics import _jsonable as convert

    return convert(value)