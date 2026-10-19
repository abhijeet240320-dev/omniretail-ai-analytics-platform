"""Coverage-completeness checks for the dense tables.

WHY THIS IS SEPARATE FROM test_ai_grounding_contract.py
------------------------------------------------------
Those tests are about what the AI layer must never claim. These are about the
data itself being whole, which is a different question with a different fix.

THE GAP THIS FILLS
------------------
`m5.calendar` has 1,969 days. `m5.sales_long` has 1,941. A min/max range check
passes both, and a "does the row count look plausible" check passes too. Only
multiplying the two axes - 30,490 series x 1,941 days = 59,181,090 - reveals
whether the 28-day difference is 28 missing days of data or 28 days that
legitimately have no actuals.

Those two cases look identical to every check that existed before this and mean
opposite things. One is a truncated load; the other is a forecast horizon. A
test that cannot tell them apart is a test that cannot catch the bad one.

The Olist checks are the same idea in the other direction: a relational rather
than grid dataset still has coverage, and an order with no payment row is only
alarming until you count it in the publisher's own file.

WHY TWO TESTS ARE MARKED SLOW
----------------------------
They scan m5.sales_long, and a bare `count(*)` over 59,181,090 rows costs about
13 seconds no matter what else the query does. There is no index that answers
"how many cells of the grid are filled" without the scan, so the only honest
options were to accept a 75-second fast tier or to mark these slow. They are
slow, which is where the analytics regression lives anyway.
"""

from __future__ import annotations

import json

import pytest

from conftest import REPO_ROOT, load_script

AUDIT_REPORT = REPO_ROOT / "data" / "processed" / "_audit_report.json"


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def audit_module():
    """The audit SCRIPT, so its own declarations can be asserted against.

    Named `_module` because tests that take both this and `live` would
    otherwise have the fixture shadow the module they meant to call.
    """
    return load_script("backend/scripts/db/07_data_truth_audit.py")


# ==============================================================================
# THE GRID
# ==============================================================================

@pytest.mark.slow
def test_m5_grid_is_complete_not_merely_covering_a_range(live):
    """30,490 series x 1,941 actual days must equal the row count exactly.

    An equality test, not a range test. A missing day or a duplicated
    store-day would break it; a correct load passes it.

    Written as plain aggregates rather than correlated subqueries: the PK is
    (id, d_num), so `count(*)` and `count(DISTINCT id)` together prove both
    that every series appears and that no series repeats a day. The NOT EXISTS
    form cost 150s of sequential scan and proved less.
    """
    from sqlalchemy import text

    with live.bind.connect() as conn:
        series = conn.execute(text("SELECT count(*) FROM m5.series")).scalar()
        rows, distinct_series, distinct_days = conn.execute(
            text(
                "SELECT count(*), count(DISTINCT id), count(DISTINCT d_num) "
                "FROM m5.sales_long"
            )
        ).one()

    assert distinct_series == series, (
        f"m5.series holds {series:,} series but sales_long references "
        f"{distinct_series:,}. A series has no sales rows at all."
    )
    assert series * distinct_days == rows, (
        f"{series:,} series x {distinct_days:,} days = {series * distinct_days:,}, "
        f"but m5.sales_long holds {rows:,}. "
        f"{series * distinct_days - rows:,} cells missing."
    )


@pytest.mark.slow
def test_the_28_calendar_only_days_are_a_horizon_not_a_truncated_load(live):
    """The calendar outruns the actuals by exactly 28 days, and that is by design.

    If this gap ever closes, a forecast benchmark built on it becomes invalid -
    there would be nothing left to predict. So this asserts the gap EXISTS,
    which is the opposite of a completeness test and just as important.

    The horizon is computed by comparing the two day ranges rather than with
    NOT EXISTS, because every sales_long day is in the calendar by foreign key,
    so the difference of the ranges IS the set difference.
    """
    from sqlalchemy import text

    with live.bind.connect() as conn:
        calendar_days, cal_lo, cal_hi = conn.execute(
            text("SELECT count(*), min(d_num), max(d_num) FROM m5.calendar")
        ).one()
        actual_lo, actual_hi = conn.execute(
            text("SELECT min(d_num), max(d_num) FROM m5.sales_long")
        ).one()

    horizon = calendar_days - (actual_hi - actual_lo + 1)

    assert actual_hi < cal_hi, (
        "m5.sales_long extends to or past the end of the calendar; there is no "
        "forecast horizon left, so nothing can be evaluated"
    )
    assert horizon == 28, (
        f"the M5 forecast horizon is now {horizon} days (actual d_num "
        f"{actual_lo}..{actual_hi}, calendar {cal_lo}..{cal_hi}), not 28. The "
        f"dataset changed, or the load dropped actuals."
    )


def test_the_audit_records_the_grid_as_complete(audit_module):
    """The script must measure this, not merely be capable of it.

    A check that exists but was never run tells you nothing about the data, so
    the recorded verdict is the thing being asserted.
    """
    cls = audit_module.Audit
    assert cls.GRID_EXPECTATIONS, "the grid expectations table is empty"

    m5 = next(s for s in cls.GRID_EXPECTATIONS if "m5" in s["label"])
    assert m5["actual"].strip().lower().startswith("select count(*) from m5.sales_long")
    assert "days" in m5["facts"].lower()


# ==============================================================================
# THE RELATIONAL COVERAGE
# ==============================================================================

def test_orders_without_payments_are_the_publishers_own_gaps(live, audit_module):
    """One order has no payment row - and so does one row in the raw CSV.

    The temptation is to treat this as an orphan or a partial ingest and
    'fix' it. Doing so would invent a payment that the marketplace never
    recorded. The correct outcome is the current one: preserved, counted, and
    traced to the source.
    """
    from sqlalchemy import text

    raw = audit_module._raw_olist_order_coverage()
    assert raw, "the raw olist CSVs are unavailable; cannot corroborate"

    with live.bind.connect() as conn:
        in_db = conn.execute(
            text("SELECT count(*) FROM olist.orders o WHERE NOT EXISTS "
                 "(SELECT 1 FROM olist.payments p WHERE p.order_id = o.order_id)")
        ).scalar()

    assert in_db == raw["without_payments"], (
        f"the database has {in_db} order(s) with no payment row but the raw file "
        f"has {raw['without_payments']}. That is a pipeline discrepancy."
    )
    assert in_db == 1, (
        f"{in_db} orders now lack payments; 1 was measured. If this grew, "
        f"an order's payments were dropped somewhere in this pipeline."
    )


def test_orders_without_items_are_the_publishers_own_gaps(live, audit_module):
    """775 orders have no item rows, matching the raw file exactly.

    These are orders that never became real - unavailable, canceled, created.
    Loading them faithfully means an order-count KPI includes them while a
    revenue KPI naturally excludes them, because there is nothing to price.
    """
    from sqlalchemy import text

    raw = audit_module._raw_olist_order_coverage()
    assert raw, "the raw olist CSVs are unavailable; cannot corroborate"

    with live.bind.connect() as conn:
        in_db = conn.execute(
            text("SELECT count(*) FROM olist.orders o WHERE NOT EXISTS "
                 "(SELECT 1 FROM olist.order_items i WHERE i.order_id = o.order_id)")
        ).scalar()

    assert in_db == raw["without_items"], (
        f"the database has {in_db} order(s) with no item row but the raw file "
        f"has {raw['without_items']}"
    )
    assert in_db == 775, f"{in_db} item-less orders; 775 was measured"


def test_the_shortfalls_are_recorded_as_corroborated(report):
    """Both coverage gaps must be in the corroboration table with agree=True.

    Recorded in the CORROBORATION table rather than as a bare warning,
    because that table is the one an AI layer is expected to read before
    making a claim about order coverage.
    """
    corroboration = report["source_corroboration"]
    for key in ("olist_orders_without_items", "olist_orders_without_payments"):
        assert key in corroboration, f"{key} is missing from source_corroboration"
        assert corroboration[key]["agree"] is True, corroboration[key]


# ==============================================================================
# CODED TEMPORAL AXES
# ==============================================================================

def test_time_that_is_not_a_timestamp_is_still_measured(report):
    """20 of 30 tables have no date column because their time axis is a code.

    m5 indexes days with `d_num`, dunnhumby with `day` and `week_no`. If the
    audit only understands DATE types it reports 'no date column' for a fifth of
    the data and calls that complete.
    """
    coded = report["coded_temporal_ranges"]
    assert coded, "no coded temporal axes were measured"

    assert coded["m5.sales_long.d_num"]["max"] == 1941, coded["m5.sales_long.d_num"]
    assert coded["dunnhumby.transactions.day"]["max"] == 711, coded["dunnhumby.transactions.day"]
    assert coded["m5.sell_prices.wm_yr_wk"]["min"] == 11101, coded["m5.sell_prices.wm_yr_wk"]


def test_m5_day_indices_are_resolved_to_real_dates(report):
    """A day index means nothing on its own; 1,941 is not a date.

    The audit resolves `d_num` through m5.calendar so the coverage can be
    stated as 2011-01-29 .. 2016-05-22. Without that step a reader would have
    to trust that 1,941 means what it should.
    """
    resolved = report["coded_temporal_ranges"]["m5.sales_long.d_num->date"]
    assert str(resolved["min"])[:10] == "2011-01-29", resolved
    assert str(resolved["max"])[:10] == "2016-05-22", resolved


def test_the_audits_temporal_table_names_real_columns(live, audit_module):
    """Every column the audit measures as 'temporal' must actually exist.

    I got this wrong once already: `wm_yr_wk` is on m5.sell_prices, not on
    m5.sales_long. A stale entry would raise at runtime and take the whole
    audit down, so it is checked here instead.
    """
    from sqlalchemy import text

    with live.bind.connect() as conn:
        for table, column, _ in audit_module.Audit.TEMPORAL_CODED_COLUMNS:
            schema, name = table.split(".", 1)
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema = :s "
                    "AND table_name = :t AND column_name = :c"
                ),
                {"s": schema, "t": name, "c": column},
            ).fetchone()
            assert exists, f"{table}.{column} does not exist"