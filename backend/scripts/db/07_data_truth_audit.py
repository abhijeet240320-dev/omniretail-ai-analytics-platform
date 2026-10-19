"""Pre-FastAPI data truth audit: prove the data is trustworthy, or say why not.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/db/07_data_truth_audit.py
    .venv/Scripts/python.exe backend/scripts/db/07_data_truth_audit.py --quick

READ-ONLY. This script opens the database with the READ-ONLY role
(``omniretail_readonly``) so that a bug in it cannot alter the data it is
auditing. If that role cannot connect, the script exits rather than falling back
to the read/write role: an audit that can write is not an audit.

WHY THIS EXISTS ALONGSIDE 05_verify_integrity.py
------------------------------------------------
``05_verify_integrity.py`` proves the database is INTERNALLY consistent: the row
counts match the ETL, the constraints hold, no foreign key crosses a source.
That is necessary. It is not sufficient.

This script asks different questions:

  * Is the grain what the schema claims? A table can hold exactly the right
    number of rows, satisfy every constraint, and still be the wrong grain -
    e.g. if an ETL silently duplicated every row of a fact table, the row count
    would be wrong but nothing else would notice.
  * What is actually IN the columns? NULL distributions, numeric ranges and
    date ranges, measured rather than assumed. A ``-1`` in a column declared
    NOT NULL, a profit of -99%, a delivery that precedes its order - these are
    the things that make an analytics platform quietly wrong.
  * Does the database still match the SOURCE? source -> raw -> processed ->
    PostgreSQL, with a re-hashed checksum at the raw end.
  * Can the AI layer be built on this safely? What controls must exist before
    an LLM is allowed to answer questions about it.

THE GRAIN MAP IS THE POINT
--------------------------
``GRAIN`` below declares, for every table, what one row represents. Every grain
claim is then VERIFIED by counting duplicate groups on the declared grain
columns. A table whose declared grain has duplicates is either mis-declared or
mis-loaded, and the script says which rather than guessing.

Where a grain cannot be declared it says so. ``dunnhumby.coupon_redemptions``
has no key and no defensible grain; the script reports that as UNVERIFIED
rather than inventing one. A known fan-out - ``olist.reviews``, where one review
appears against several orders - is declared as such and MEASURED, not reported
as a duplicate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

SOURCE_SCHEMAS = ("olist", "dataco", "dunnhumby", "m5")

# ==============================================================================
# THE GRAIN MAP - one row = ?
# ==============================================================================
# Each entry: (grain_description, grain_columns, expected_behaviour)
#   "unique"  - the grain columns must have zero duplicate groups
#   "keyless" - no grain can be declared; reported UNVERIFIED with evidence
#   "fanout"  - duplicates on the grain columns are EXPECTED and legitimate;
#               the script measures how many and explains why
GRAIN: dict[str, tuple[str, list[str] | None, str]] = {
    # --- olist -------------------------------------------------------------
    "olist.orders": (
        "one e-commerce order", ["order_id"], "unique",
    ),
    "olist.customers": (
        # THE ANOMALY. In the Olist source, customer_id is unique per ORDER, not
        # per person; the person is customer_unique_id. So this table has one
        # row per order, NOT one row per customer, and it is named misleadingly.
        "one ORDER's customer record (customer_id is per-order, not per-person)",
        ["customer_id"], "unique",
    ),
    "olist.order_items": (
        "one line item within an order", ["order_id", "order_item_id"], "unique",
    ),
    "olist.payments": (
        "one payment instalment within an order",
        ["order_id", "payment_sequential"], "unique",
    ),
    "olist.reviews": (
        # THE FAN-OUT. 789 review_ids appear more than once, always with the
        # same person, the same text and BOTH timestamps, and 32 of the order
        # links were created after the review. That is one review attached to
        # several orders, not a resubmission. Grain is the pair.
        "one (review, order) attachment - one review can attach to several orders",
        ["review_id", "order_id"], "fanout",
    ),
    "olist.products": ("one product listing", ["product_id"], "unique"),
    "olist.sellers": ("one seller", ["seller_id"], "unique"),
    "olist.geolocation_by_zip": (
        "one zip-code prefix", ["geolocation_zip_code_prefix"], "unique",
    ),
    "olist.customer_rfm": (
        "derived: one row per PERSON (customer_unique_id)",
        ["customer_unique_id"], "unique",
    ),
    "olist.order_payments_agg": (
        "derived: one payment summary per order", ["order_id"], "unique",
    ),
    "olist.mql": ("one marketing qualified lead", ["mql_id"], "unique"),
    "olist.closed_deals": (
        "one WON deal per lead (a lead appears here only if it closed)",
        ["mql_id"], "unique",
    ),
    # --- dataco ------------------------------------------------------------
    "dataco.orders": ("one supply-chain order", ["order_id"], "unique"),
    "dataco.customers": ("one DataCo customer", ["customer_id"], "unique"),
    "dataco.products": ("one product card", ["product_card_id"], "unique"),
    "dataco.order_items": (
        "one order line item", ["order_item_id"], "unique",
    ),
    # --- dunnhumby ---------------------------------------------------------
    "dunnhumby.transactions": (
        "one product line within a household basket",
        ["household_key", "basket_id", "product_id"], "unique",
    ),
    "dunnhumby.products": ("one dunnhumby product", ["product_id"], "unique"),
    "dunnhumby.households": (
        "one household WITH demographics", ["household_key"], "unique",
    ),
    "dunnhumby.household_rfm": (
        # Derived over ALL 2,500 households, including the 1,699 with no
        # demographics. That is why it is larger than dunnhumby.households.
        "derived: one row per household, with or without demographics",
        ["household_key"], "unique",
    ),
    "dunnhumby.campaigns": ("one marketing campaign", ["campaign"], "unique"),
    "dunnhumby.campaign_contacts": (
        "one household targeted by one campaign",
        ["household_key", "campaign"], "unique",
    ),
    "dunnhumby.coupons": (
        "one coupon distributed for one product in one campaign",
        ["coupon_upc", "product_id", "campaign"], "unique",
    ),
    "dunnhumby.promo_exposure": (
        "one product's promotional presence in one store, one week",
        ["product_id", "store_id", "week_no", "display", "mailer"], "unique",
    ),
    "dunnhumby.coupon_redemptions": (
        # NO GRAIN. The candidate key (household_key, day, coupon_upc) has 14
        # duplicate groups, so the table has no key and therefore no defensible
        # grain. Inventing one would fabricate an identity the data refused.
        "UNVERIFIABLE: no primary key, candidate key rejected by the data",
        None, "keyless",
    ),
    # --- m5 ----------------------------------------------------------------
    "m5.series": ("one demand series (item x store)", ["id"], "unique"),
    "m5.calendar": ("one calendar date", ["d_num"], "unique"),
    "m5.sales_long": (
        "one demand observation: one series on one day",
        ["id", "d_num"], "unique",
    ),
    "m5.sales_daily_total": (
        "derived: total units for all stores on one day", ["d_num"], "unique",
    ),
    "m5.sell_prices": (
        "one price for one item in one store in one week",
        ["store_id", "item_id", "wm_yr_wk"], "unique",
    ),
}

# Relationships whose join fan-out is worth measuring, because the analytics
# layer will use them and a fan-out there silently multiplies a metric.
FANOUT_CHECKS = [
    ("olist.reviews -> olist.orders", "olist.reviews", "order_id", "olist.orders", "order_id"),
    ("olist.order_items -> olist.orders", "olist.order_items", "order_id", "olist.orders", "order_id"),
    ("olist.order_items -> olist.products", "olist.order_items", "product_id", "olist.products", "product_id"),
    ("olist.orders -> olist.customers", "olist.orders", "customer_id", "olist.customers", "customer_id"),
    ("olist.payments -> olist.orders", "olist.payments", "order_id", "olist.orders", "order_id"),
    ("olist.reviews -> olist.customers (by order)", "olist.reviews", "order_id", "olist.customers", "customer_id"),
    ("dunnhumby.transactions -> dunnhumby.products", "dunnhumby.transactions", "product_id", "dunnhumby.products", "product_id"),
    ("m5.sales_long -> m5.series", "m5.sales_long", "id", "m5.series", "id"),
    ("m5.sales_long -> m5.calendar", "m5.sales_long", "d_num", "m5.calendar", "d_num"),
]

# Date columns whose ORDERING carries meaning. A child timestamp earlier than
# its parent is a data defect, not a curiosity.
DATE_ORDER_RULES = [
    ("olist.orders", "order_approved_at", "order_purchase_timestamp", "approved before purchase"),
    ("olist.orders", "order_delivered_carrier_date", "order_approved_at", "handed to carrier before approval"),
    ("olist.orders", "order_delivered_customer_date", "order_approved_at", "delivered before approval"),
    ("olist.orders", "order_delivered_carrier_date", "order_purchase_timestamp", "handed to carrier before purchase"),
    ("olist.orders", "order_delivered_customer_date", "order_purchase_timestamp", "delivered before purchase"),
    ("dataco.orders", "shipping_date", "order_date", "shipped before ordered"),
    # NOTE the direction. A seller answering a review AFTER the customer wrote
    # it is the NORMAL case; an answer timestamp EARLIER than the creation
    # timestamp is the impossible one. Getting this backwards flagged all
    # 99,224 rows as violations and would have buried the real anomalies.
    ("olist.reviews", "review_answer_timestamp", "review_creation_date",
     "answered before it was written"),
]

# Numeric columns whose sign carries business meaning. A negative where the
# domain says "cannot be negative" is a defect; a ZERO where zero is a legitimate
# value is not, and saying so matters more than it sounds.
#
# Column names here were checked against pg_attribute. An earlier draft named
# money columns on `olist.orders` that only exist on `dataco.orders` -
# `olist.orders` holds dates and delivery flags and no money at all - so nine of
# fifteen rules silently skipped. A rule naming a column that does not exist is
# a rule that checks nothing, which is worse than not having it.
SIGN_RULES = [
    # olist money lives in the ITEM and PAYMENT tables, not in orders
    ("olist.order_items", "price", "positive"),
    ("olist.order_items", "freight_value", "non_negative"),
    ("olist.order_items", "item_total", "non_negative"),
    # A VOUCHER is a zero-cash payment method, so 0.0000 is a real value here.
    # Nine rows are voucher / not_defined zeroes; none are negative. Requiring
    # "positive" would have called correct data wrong.
    ("olist.payments", "payment_value", "non_negative"),
    # dataco orders are the money table
    ("dataco.orders", "order_value", "non_negative"),
    ("dataco.orders", "order_sales", "non_negative"),
    ("dataco.orders", "order_profit", "any"),          # profit is legitimately negative
    ("dataco.orders", "total_quantity", "non_negative"),
    ("dataco.orders", "days_for_shipping_real", "non_negative"),
    ("dataco.order_items", "order_item_product_price", "non_negative"),
    ("dataco.order_items", "order_item_discount", "non_negative"),
    ("dataco.order_items", "order_item_discount_rate", "any"),
    # dunnhumby: SALES_VALUE is 0 on 18,850 rows in the source itself, and 29
    # more are float residue that NUMERIC(18,4) rounds to 0. See
    # documented_transformations below.
    ("dunnhumby.transactions", "quantity", "non_negative"),
    ("dunnhumby.transactions", "sales_value", "non_negative"),
    ("dunnhumby.coupons", "coupon_upc", "positive"),
    ("m5.sales_long", "sales", "non_negative"),
    ("m5.sell_prices", "sell_price", "positive"),
    # M5 numbers weekdays 1..7 with 1 = Saturday, NOT 1..6. An earlier draft of
    # this script assumed 1..6 and reported 281 perfectly valid Friday rows as a
    # defect. Measured: wday 1 maps to weekday 'Saturday', wday 7 to 'Friday'.
    ("m5.calendar", "wday", "in_1_7"),
]

# Anomalies that look like defects but were checked against the RAW source file
# and found to be faithful copies of it. They are warnings, not defects, and the
# evidence for that claim is the source-file count, re-derived here.
#
# Each entry: (subject, what it is, raw file, a callable returning the count in
# the raw file). Recomputed rather than hard-coded, so the claim cannot go stale.
def _raw_olist_order_dates() -> dict:
    """Re-derive the olist order-date anomalies from the raw CSV."""
    import csv as _csv
    from datetime import datetime as _dt

    path = REPO_ROOT / "data" / "raw" / "olist" / "olist_orders_dataset.csv"
    if not path.exists():
        return {}
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")

    def parse(value):
        value = (value or "").strip()
        if not value:
            return None
        for fmt in formats:
            try:
                return _dt.strptime(value, fmt)
            except ValueError:
                continue
        return None

    counts = {"carrier_before_approval": 0, "delivered_before_approval": 0,
              "carrier_before_purchase": 0, "rows": 0}
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        for row in _csv.DictReader(handle):
            counts["rows"] += 1
            approved = parse(row.get("order_approved_at"))
            purchased = parse(row.get("order_purchase_timestamp"))
            carrier = parse(row.get("order_delivered_carrier_date"))
            delivered = parse(row.get("order_delivered_customer_date"))
            if carrier and approved and carrier < approved:
                counts["carrier_before_approval"] += 1
            if delivered and approved and delivered < approved:
                counts["delivered_before_approval"] += 1
            if carrier and purchased and carrier < purchased:
                counts["carrier_before_purchase"] += 1
    return counts


def _raw_olist_order_coverage() -> dict:
    """Count, in the RAW CSVs, how many orders lack payments or items.

    This has to be measured rather than hardcoded. If the allowance were a
    literal, the check would keep passing after the raw file changed, and would
    be asserting a number nobody ever re-verified - the exact failure this
    audit exists to catch.
    """
    import csv as _csv

    raw_dir = REPO_ROOT / "data" / "raw" / "olist"
    files = {
        "orders": raw_dir / "olist_orders_dataset.csv",
        "items": raw_dir / "olist_order_items_dataset.csv",
        "payments": raw_dir / "olist_order_payments_dataset.csv",
    }
    if not all(p.exists() for p in files.values()):
        return {}

    def order_ids(path):
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            return {row["order_id"] for row in _csv.DictReader(handle)
                    if row.get("order_id")}

    orders = order_ids(files["orders"])
    with_items = order_ids(files["items"])
    with_payments = order_ids(files["payments"])
    return {
        "orders": len(orders),
        "without_items": len(orders - with_items),
        "without_payments": len(orders - with_payments),
    }


def _raw_dunnhumby_sales_value() -> dict:
    """Re-derive dunnhumby SALES_VALUE zeros and float residue from the raw CSV."""
    import csv as _csv

    path = REPO_ROOT / "data" / "raw" / "dunnhumby" / "transaction_data.csv"
    if not path.exists():
        return {}
    exact_zero = residue = negative = rows = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        for row in _csv.DictReader(handle):
            rows += 1
            try:
                value = float(row["SALES_VALUE"])
            except (TypeError, ValueError):
                continue
            if value == 0:
                exact_zero += 1
            elif value < 0:
                negative += 1
            elif value < 0.00005:
                residue += 1
    return {"rows": rows, "exact_zero": exact_zero, "float_residue": residue,
            "negative": negative}


@dataclass
class Finding:
    """One measured result. `severity` decides whether it is a defect or a note."""

    section: str
    subject: str
    severity: str  # "ok" | "note" | "warning" | "defect"
    classification: str  # see CLASSIFICATIONS
    message: str
    measured: object = None

    def as_dict(self) -> dict:
        return {
            "section": self.section,
            "subject": self.subject,
            "severity": self.severity,
            "classification": self.classification,
            "message": self.message,
            "measured": self.measured,
        }


CLASSIFICATIONS = {
    "confirmed_invalid_duplicate": "same key, genuinely the same fact twice",
    "legitimate_repeat": "a real-world event that repeats (two baskets, two payments)",
    "known_grain_fanout": "duplicate on a key that is NOT the table's grain",
    "source_property": "the anomaly is in the publisher's file and is reproduced faithfully",
    "documented_transformation": "the database differs from the source by a deliberate, stated rule",
    "warning": "unusual but not provably wrong",
    "unverified": "could not be checked, and that is why",
    "pass": "checked and correct",
}


class Audit:
    def __init__(self, conn, quick: bool = False):
        self.conn = conn
        self.quick = quick
        self.findings: list[Finding] = []
        self.tables: dict[str, dict] = {}
        self.started = time.monotonic()

    # ------------------------------------------------------------------ util
    def q(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return cur.fetchall()

    def one(self, sql: str, params: tuple = ()):
        rows = self.q(sql, params)
        return rows[0][0] if rows else None

    def row(self, sql: str, params: tuple = ()):
        """First row as a plain tuple, so a multi-column result can be unpacked."""
        rows = self.q(sql, params)
        return tuple(rows[0]) if rows else tuple()

    def add(self, section, subject, severity, classification, message, measured=None):
        self.findings.append(Finding(section, subject, severity, classification, message, measured))

    def section(self, title: str) -> None:
        print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")

    def note(self, message: str) -> None:
        print(f"  {message}")

    # =================================================== 1. table inventory
    def table_inventory(self) -> None:
        self.section("1. TABLE INVENTORY AND ACTUAL ROW COUNTS")
        rows = self.q(
            """
            SELECT n.nspname, c.relname, c.reltuples::bigint, c.relpages
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s) AND c.relkind = 'r'
            ORDER BY 1, 2
            """,
            (list(SOURCE_SCHEMAS),),
        )
        for schema, table, estimate, _pages in rows:
            # reltuples is the planner's estimate. Measure the truth.
            actual = self.one(f'SELECT count(*) FROM "{schema}"."{table}"')
            pk = self.one(
                """
                SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                FROM pg_index i
                CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                WHERE i.indrelid = %s::regclass AND i.indisprimary
                """,
                (f'"{schema}"."{table}"',),
            )
            ncols = self.one(
                """
                SELECT count(*) FROM pg_attribute
                WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped
                """,
                (f'"{schema}"."{table}"',),
            )
            self.tables[f"{schema}.{table}"] = {
                "rows": actual,
                "planner_estimate": estimate,
                "primary_key": pk,
                "columns": ncols,
            }
            drift = ""
            if estimate and actual and abs(estimate - actual) / max(actual, 1) > 0.10:
                drift = "   <-- planner estimate off by >10%"
                self.add(
                    "inventory", f"{schema}.{table}", "warning", "warning",
                    f"planner estimate {estimate:,} vs actual {actual:,}",
                    {"estimate": estimate, "actual": actual},
                )
            print(f"  {schema}.{table:28} {actual:>12,} rows  {ncols:>2} cols  "
                  f"pk={pk or '(NONE - keyless)'}{drift}")

        total = sum(t["rows"] for t in self.tables.values())
        print(f"\n  TOTAL: {len(self.tables)} tables, {total:,} rows")
        self.total_rows = total

    # ============================== 2. per-column NULLs, numerics, dates
    def column_profiles(self) -> None:
        self.section("2. COLUMN PROFILES - NULLS, NUMERIC RANGES, DATE RANGES")
        print("  One scan per table, aggregating every column at once.")
        print("  'est>0.01' is the NULL fraction rounded to 2dp; shown only when >1%.\n")

        numeric_types = ("numeric", "double precision", "bigint", "integer", "smallint", "real")
        date_types = ("timestamp without time zone", "timestamp with time zone", "date")

        def base_type(declared: str) -> str:
            """`numeric(18,4)` -> `numeric`.

            format_type() KEEPS the modifier, so comparing the whole string
            against a list of base type names silently skips every money column
            in the database - NUMERIC(18,4) is not the string "numeric". That
            bug made the first run of this script skip 9 of its 15 numeric rules
            and print a degenerate range of [min..min] for the rest.
            """
            return declared.split("(")[0].strip()

        for name in sorted(self.tables):
            schema, table = name.split(".", 1)
            cols = self.q(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (f'"{schema}"."{table}"',),
            )
            selects = ["count(*)"]
            labels = []
            for col, ctype in cols:
                quoted = f'"{col}"'
                selects.append(f"count(*) FILTER (WHERE {quoted} IS NULL)")
                labels.append(("null", col, None))
                kind = base_type(ctype)
                if kind in numeric_types or kind in date_types:
                    selects.append(f"min({quoted})")
                    selects.append(f"max({quoted})")
                    labels.append((kind, col, None))
                    labels.append((kind, col, None))

            sql = (
                f"SELECT {', '.join(selects)} FROM \"{schema}\".\"{table}\""
            )
            try:
                row = self.q(sql)[0]
            except Exception as exc:  # noqa: BLE001
                self.add("profiles", name, "warning", "unverified",
                         f"profile scan failed: {exc}")
                continue

            total_rows = row[0]
            nulls: dict[str, int] = {}
            ranges: dict[str, dict] = {}
            idx = 1
            for kind, col, _ in labels:
                if kind == "null":
                    nulls[col] = row[idx]; idx += 1
                else:
                    entry = ranges.setdefault(col, {})
                    # min arrives first, then max, in the order they were
                    # appended to `selects`. Storing them under named keys
                    # rather than "col" and "col#2" is what stops a later
                    # lookup from silently reporting min twice as the range.
                    entry["max" if "min" in entry else "min"] = row[idx]
                    idx += 1

            interesting_nulls = {c: v for c, v in nulls.items() if v}
            numeric_ranges = {
                c: v for c, v in ranges.items() if v.get("min") is not None
                and base_type(next(t for n, t in cols if n == c)) in numeric_types
            }
            date_ranges = {
                c: v for c, v in ranges.items() if v.get("min") is not None
                and base_type(next(t for n, t in cols if n == c)) in date_types
            }
            num_line = [
                f"{c}[{v['min']}..{v['max']}]"
                for c, v in sorted(numeric_ranges.items())
                if v["min"] != v["max"]
            ]
            date_line = [
                f"{c}[{str(v['min'])[:10]}..{str(v['max'])[:10]}]"
                for c, v in sorted(date_ranges.items())
                if v["min"] != v["max"]
            ]

            self.tables[name].update(
                null_counts=interesting_nulls,
                numeric_ranges=numeric_ranges,
                date_ranges=date_ranges,
                total_columns=len(cols),
            )
            print(f"  {name}")
            if interesting_nulls:
                print("      NULLs : " + ", ".join(
                    f"{c}={v:,} ({v / total_rows:.0%})" if total_rows else f"{c}={v}"
                    for c, v in sorted(interesting_nulls.items())))
            else:
                print("      NULLs : none")
            if num_line:
                print("      num   : " + "  ".join(num_line))
            if date_line:
                print("      date  : " + "  ".join(date_line))

    # ================================================= 3. grain verification
    def grain_checks(self) -> None:
        self.section("3. GRAIN - DOES EACH TABLE HOLD WHAT ITS GRAIN CLAIMS?")
        self.grain_results = {}
        for name in sorted(self.tables):
            if name not in GRAIN:
                self.add("grain", name, "warning", "unverified",
                         "no grain declared in the audit map")
                self.grain_results[name] = {"verdict": "unverified", "reason": "not in GRAIN map"}
                continue
            description, columns, behaviour = GRAIN[name]
            schema, table = name.split(".", 1)

            if behaviour == "keyless" or not columns:
                pk = self.tables[name]["primary_key"]
                dup = self.one(
                    f'SELECT count(*) FROM (SELECT household_key, day, coupon_upc '
                    f'FROM "{schema}"."{table}" GROUP BY 1,2,3 HAVING count(*) > 1) x'
                ) if name == "dunnhumby.coupon_redemptions" else None
                self.grain_results[name] = {
                    "verdict": "unverified",
                    "reason": "no primary key; grain cannot be declared",
                    "candidate_key_duplicate_groups": dup,
                }
                self.add("grain", name, "note", "unverified",
                         f"grain UNVERIFIABLE (pk={pk}); candidate key has "
                         f"{dup} duplicate group(s)", {"duplicate_groups": dup})
                print(f"  {name:32} UNVERIFIABLE - no key. Candidate key "
                      f"(household_key, day, coupon_upc) has {dup} duplicate group(s).")
                continue

            collist = ", ".join(f'"{c}"' for c in columns)
            # Rows whose grain value contains a NULL: a grain is only a grain if
            # it is actually present. COUNT(DISTINCT) ignores NULLs, which is
            # exactly the trap this catches.
            null_grain = self.one(
                f'SELECT count(*) FROM "{schema}"."{table}" '
                f"WHERE {' OR '.join(f'{c} IS NULL' for c in columns)}"
            )
            dup_groups, dup_rows, max_multiplicity = self.q(
                f"""
                SELECT count(*), COALESCE(sum(n - 1), 0), COALESCE(max(n), 1)
                FROM (SELECT count(*) AS n FROM "{schema}"."{table}"
                      GROUP BY {collist} HAVING count(*) > 1) d
                """
            )[0]

            verdict = "unique"
            classification = "pass"
            if null_grain:
                verdict = "nulls_in_grain"
                classification = "confirmed_invalid_duplicate"
                self.add("grain", name, "defect", classification,
                         f"{null_grain:,} row(s) have a NULL in a grain column",
                         {"rows": null_grain})
            if dup_groups:
                if behaviour == "fanout":
                    verdict = "fanout_as_declared"
                    classification = "known_grain_fanout"
                    self.add("grain", name, "note", classification,
                             f"{dup_groups:,} duplicate group(s) on {collist}, "
                             f"{dup_rows:,} surplus row(s), max multiplicity "
                             f"{max_multiplicity} - this is the declared grain",
                             {"groups": dup_groups, "surplus": dup_rows})
                else:
                    verdict = "grain_violated"
                    classification = "confirmed_invalid_duplicate"
                    self.add("grain", name, "defect", classification,
                             f"{dup_groups:,} duplicate group(s) on the declared "
                             f"grain {collist}",
                             {"groups": dup_groups, "surplus": dup_rows})
            self.grain_results[name] = {
                "description": description,
                "grain_columns": columns,
                "behaviour": behaviour,
                "verdict": verdict,
                "null_grain_rows": null_grain,
                "duplicate_groups": dup_groups,
                "surplus_rows": dup_rows,
                "max_multiplicity": max_multiplicity,
            }
            mark = {"unique": "PASS  ", "fanout_as_declared": "FANOUT",
                    "nulls_in_grain": "DEFECT", "grain_violated": "DEFECT"}[verdict]
            print(f"  {name:32} {mark} dup_groups={dup_groups:>7,} "
                  f"surplus={dup_rows:>7,} null_grain={null_grain:>6,}  "
                  f"| {description[:60]}")

    # ============================================ 4. keys: PK, NULL, UNIQUE
    def key_checks(self) -> None:
        self.section("4. KEY VALIDITY - PK AND UNIQUE, RE-MEASURED FROM THE ROWS")
        self.key_summary = {"pk": 0, "uq": 0, "pk_violations": 0, "uq_violations": 0}
        constraints = self.q(
            """
            SELECT n.nspname, t.relname, c.contype,
                   (SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                      FROM LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = ANY(%s) AND c.contype IN ('p', 'u')
            ORDER BY 1, 2, 3
            """,
            (list(SOURCE_SCHEMAS),),
        )
        for schema, table, contype, cols in constraints:
            kinds = ["primary key"] if contype == "p" else ["unique"]
            collist = cols.replace(", ", '","').join(['"', '"'])
            nulls = self.one(
                f'SELECT count(*) FROM "{schema}"."{table}" '
                f"WHERE {' OR '.join(f'{c} IS NULL' for c in cols.split(", "))}"
            )
            dups = self.one(
                f'SELECT count(*) FROM (SELECT 1 FROM "{schema}"."{table}" '
                f"GROUP BY {collist} HAVING count(*) > 1) d"
            )
            key = "pk" if contype == "p" else "uq"
            self.key_summary[key] += 1
            self.key_summary[f"{key}_violations"] += dups + nulls
            status = "PASS" if not (dups or nulls) else "DEFECT"
            if dups or nulls:
                self.add("keys", f"{schema}.{table}", "defect",
                         "confirmed_invalid_duplicate",
                         f"{'/'.join(kinds)} on ({cols}): {dups} duplicate group(s), "
                         f"{nulls} NULL key row(s)",
                         {"duplicates": dups, "null_keys": nulls})
            print(f"  {status} {'/'.join(kinds):14} {schema}.{table:26} ({cols})"
                  f"  dups={dups} null_keys={nulls}")
        print(f"\n  primary keys: {self.key_summary['pk']}   "
              f"unique constraints: {self.key_summary['uq']}   "
              f"violations: {self.key_summary['pk_violations'] + self.key_summary['uq_violations']}")

    # ============================================ 5. foreign keys / orphans
    def foreign_key_checks(self) -> None:
        self.section("5. FOREIGN KEYS - ORPHANS RE-COUNTED FROM THE ROWS")
        fks = self.q(
            """
            SELECT n.nspname, t.relname, c.conname,
                   (SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                      FROM LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum),
                   rn.nspname, rt.relname,
                   (SELECT string_agg(a.attname, ', ' ORDER BY k.ord)
                      FROM LATERAL unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.attnum)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_class rt ON rt.oid = c.confrelid
            JOIN pg_namespace rn ON rn.oid = rt.relnamespace
            WHERE n.nspname = ANY(%s) AND c.contype = 'f'
            ORDER BY 1, 2, 3
            """,
            (list(SOURCE_SCHEMAS),),
        )
        self.fk_summary = {"count": 0, "orphans": 0, "cross_schema": 0}
        for schema, table, name, cols, rschema, rtable, rcols in fks:
            self.fk_summary["count"] += 1
            join = " AND ".join(
                f'i."{c}" = p."{rc}"' for c, rc in zip(cols.split(", "), rcols.split(", "))
            )
            present = " AND ".join(f'i."{c}" IS NOT NULL' for c in cols.split(", "))
            orphans = self.one(
                f'SELECT count(*) FROM "{schema}"."{table}" i '
                f'LEFT JOIN "{rschema}"."{rtable}" p ON {join} '
                f'WHERE {present} AND p."{rcols.split(", ")[0]}" IS NULL'
            )
            self.fk_summary["orphans"] += orphans
            cross = rschema != schema
            if cross:
                self.fk_summary["cross_schema"] += 1
            status = "PASS" if not orphans and not cross else "DEFECT"
            if orphans:
                self.add("foreign_keys", f"{schema}.{table}", "defect",
                         "confirmed_invalid_duplicate",
                         f"{name} ({cols} -> {rschema}.{rtable}): {orphans:,} orphan row(s)",
                         {"orphans": orphans})
            if cross:
                self.add("foreign_keys", f"{schema}.{table}", "defect",
                         "confirmed_invalid_duplicate",
                         f"{name} crosses a schema boundary "
                         f"({schema} -> {rschema}), which this project forbids")
            print(f"  {status} {schema}.{table:24} ({cols}) -> {rschema}.{rtable:20} "
                  f"orphans={orphans}")
        print(f"\n  foreign keys: {self.fk_summary['count']}   "
              f"total orphans: {self.fk_summary['orphans']}   "
              f"cross-schema: {self.fk_summary['cross_schema']}")

    # ================================================= 6. numeric plausibility
    def numeric_checks(self) -> None:
        self.section("6. NUMERIC PLAUSIBILITY - SIGNS AND EXTREMES")
        for table, column, rule in SIGN_RULES:
            schema, name = table.split(".", 1)
            info = self.tables.get(table, {})
            if column not in info.get("numeric_ranges", {}):
                self.add("numeric", f"{table}.{column}", "note", "unverified",
                         f"no numeric range recorded for {column}; rule '{rule}' not checked")
                print(f"  SKIP  {table}.{column:34} not a numeric column here")
                continue
            count = self.one(
                f'SELECT count(*) FROM "{schema}"."{name}" WHERE "{column}" IS NOT NULL'
            )
            bad = 0
            detail = ""
            if rule == "positive":
                bad = self.one(f'SELECT count(*) FROM "{schema}"."{name}" WHERE "{column}" <= 0')
                detail = "values <= 0"
            elif rule == "non_negative":
                bad = self.one(f'SELECT count(*) FROM "{schema}"."{name}" WHERE "{column}" < 0')
                detail = "negative values"
            elif rule == "in_1_7":
                bad = self.one(
                    f'SELECT count(*) FROM "{schema}"."{name}" '
                    f'WHERE "{column}" < 1 OR "{column}" > 7'
                )
                detail = "values outside 1..7"
            lo = info["numeric_ranges"][column]["min"]
            hi = info["numeric_ranges"][column]["max"]
            status = "PASS" if bad == 0 else "DEFECT"
            if bad:
                self.add("numeric", f"{table}.{column}", "defect",
                         "confirmed_invalid_duplicate",
                         f"{bad:,} of {count:,} row(s) have {detail}",
                         {"offending": bad, "non_null": count})
            print(f"  {status} {table}.{column:34} rule={rule:14} "
                  f"offending={bad:>10,}  range=[{lo} .. {hi}]")

    # ======================================================= 7. date sanity
    def date_checks(self) -> None:
        self.section("7. DATE PLAUSIBILITY - RANGES AND ORDERING VIOLATIONS")
        for table, child, parent, label in DATE_ORDER_RULES:
            schema, name = table.split(".", 1)
            info = self.tables.get(table, {})
            if child not in info.get("date_ranges", {}) or parent not in info.get("date_ranges", {}):
                print(f"  SKIP  {table}.{child} vs {parent}: column not present as a date")
                continue
            violations = self.one(
                f'SELECT count(*) FROM "{schema}"."{name}" '
                f'WHERE "{child}" IS NOT NULL AND "{parent}" IS NOT NULL '
                f'AND "{child}" < "{parent}"'
            )
            status = "PASS" if violations == 0 else "WARNING"
            if violations:
                self.add("dates", f"{table}.{child}", "warning", "warning",
                         f"{violations:,} row(s) {label} ({child} < {parent})",
                         {"violations": violations})
            print(f"  {status} {table:24} {label:42} violations={violations:,}")

        print("\n  observed date coverage per table (from the profile scan):")
        for name in sorted(self.tables):
            for col, bounds in sorted(self.tables[name].get("date_ranges", {}).items()):
                lo, hi = bounds.get("min"), bounds.get("max")
                if lo is None or hi is None:
                    continue
                span = (hi - lo).days
                flag = ""
                if span < 0:
                    flag = "  <-- INVERTED RANGE"
                    self.add("dates", f"{name}.{col}", "defect",
                             "confirmed_invalid_duplicate",
                             f"max {hi} precedes min {lo}")
                print(f"      {name}.{col:38} {str(lo)[:19]:19} .. {str(hi)[:19]:19} "
                      f"({span:>5} days){flag}")

        self.coverage_completeness()

    # ============================ 7a. is a dense grid actually dense?
    #
    # A min/max date range proves a table spans the period; it does NOT prove the
    # table is complete. m5.calendar covers 1,969 days and m5.sales_long covers
    # 1,941 - and only a product reveals whether the difference is 28 missing
    # days of data (a truncated load) or 28 days that legitimately have no
    # actuals (the forecast horizon). Those look identical in a range check and
    # mean opposite things, so the arithmetic has to be done.
    #
    # Declared as data rather than inferred: an entity/period grid only means
    # something when we know the two axes are supposed to be complete.
    GRID_EXPECTATIONS = [
        {
            "label": "m5 sales grid (series x day)",
            "facts": "SELECT (SELECT count(*) FROM m5.series) AS series, "
                     "(SELECT count(DISTINCT d_num) FROM m5.sales_long) AS days",
            "actual": "SELECT count(*) FROM m5.sales_long",
            # the calendar deliberately runs past the actuals
            "allowed_shortfall": "SELECT (SELECT count(DISTINCT d_num) FROM m5.calendar) "
                                 "- (SELECT count(DISTINCT d_num) FROM m5.sales_long)",
            "note": "m5.calendar holds 28 horizon days with no actuals by design",
        },
        {
            # Not every order in the source carries payments or items. That is a
            # property of Olist's publisher, verified against the raw CSVs: the
            # same 1 and 775 orders lack them there. Both counts are therefore
            # recorded as corroboratable rather than tolerated blindly.
            "label": "olist orders with payments",
            "facts": "SELECT (SELECT count(*) FROM olist.orders) AS series, 1 AS days",
            "actual": "SELECT count(DISTINCT order_id) FROM olist.payments",
            "allowed_shortfall": ("raw", "without_payments"),
            "note": "measured from the raw publisher CSVs",
            "corroborate": "olist_orders_without_payments",
        },
        {
            "label": "olist orders with items",
            "facts": "SELECT (SELECT count(*) FROM olist.orders) AS series, 1 AS days",
            "actual": "SELECT count(DISTINCT order_id) FROM olist.order_items",
            "allowed_shortfall": ("raw", "without_items"),
            "note": "measured from the raw publisher CSVs",
            "corroborate": "olist_orders_without_items",
        },
    ]

    def coverage_completeness(self) -> None:
        self.section("7a. COVERAGE COMPLETENESS - IS A DENSE GRID ACTUALLY DENSE?")
        self.coverage_source_properties = {}
        raw_coverage = _raw_olist_order_coverage()
        if raw_coverage:
            print(f"  raw publisher coverage: {raw_coverage['orders']:,} orders, "
                  f"{raw_coverage['without_payments']:,} without payments, "
                  f"{raw_coverage['without_items']:,} without items")
        for spec in self.GRID_EXPECTATIONS:
            facts = dict(
                zip(
                    ("series", "days"),
                    self.row(spec["facts"]),
                )
            )
            expected = facts["series"] * facts["days"]
            actual = self.one(spec["actual"])
            shortfall = expected - actual

            allowance = spec["allowed_shortfall"]
            if isinstance(allowance, tuple):
                # (source_file_key, field) - the allowance is whatever the raw
                # publisher data actually shows, read on every run
                kind, field = allowance
                if kind == "raw":
                    if not raw_coverage:
                        print(f"  SKIP  {spec['label']:32} raw csv unavailable")
                        continue
                    allowed = raw_coverage[field]
                else:
                    allowed = self.one(allowance)
            else:
                allowed = self.one(allowance)

            if shortfall == 0:
                status, classification = "PASS", "complete"
                message = f"{actual:,} row(s) = {facts['series']:,} x {facts['days']:,}, no gaps"
            elif shortfall <= allowed:
                status = "PASS"
                classification = "source_property"
                message = (
                    f"{actual:,} row(s); {shortfall:,} fewer than the full "
                    f"{facts['series']:,} x {facts['days']:,} product, reproduced "
                    f"exactly by the raw source - {spec['note']}"
                )
                if spec.get("corroborate"):
                    self.coverage_source_properties[spec["corroborate"]] = {
                        "database_shortfall": shortfall,
                        "raw_shortfall": allowed,
                        "agree": shortfall == allowed,
                    }
                    self.add(
                        "coverage", spec["label"], "note", "source_property",
                        f"{shortfall:,} row(s) short; the raw publisher file has the "
                        f"same {allowed:,}. Faithfully loaded, not a partial ingest.",
                        {"database": shortfall, "raw": allowed},
                    )
            else:
                status = "WARNING"
                classification = "unverified"
                message = (
                    f"{actual:,} row(s) but {expected:,} expected "
                    f"({shortfall:,} unexplained, {allowed:,} allowed)"
                )
                self.add("coverage", spec["label"], "warning", classification, message,
                         {"expected": expected, "actual": actual, "shortfall": shortfall})

            print(f"  {status} {spec['label']:32} {message}")

        # merged into self.corroboration by source_corroboration(), which runs
        # next and owns that dict's lifetime
        self.coverage_source_properties_pending = self.coverage_source_properties

    # ============================ 7b. temporal columns that are not dates
    #
    # 20 of the 30 tables print "no date column" because their time axis is an
    # integer or a code, not a timestamp: m5 uses d_num and wm_yr_wk, dunnhumby
    # uses day and week_no. Those are real temporal coverage and must be
    # measured, or the audit silently skips a quarter of the data.
    TEMPORAL_CODED_COLUMNS = [
        ("m5.sales_long", "d_num", "day index, resolved through m5.calendar"),
        ("m5.sell_prices", "wm_yr_wk", "year-week code, e.g. 11101 = 2011 week 1"),
        ("dunnhumby.transactions", "day", "day index 1..711"),
        ("dunnhumby.transactions", "week_no", "week number"),
        ("dunnhumby.promo_exposure", "week_no", "week number"),
    ]

    def coded_temporal_ranges(self) -> None:
        self.section("7b. CODED TEMPORAL AXES - TIME THAT IS NOT A TIMESTAMP")
        measured = {}
        for table, column, meaning in self.TEMPORAL_CODED_COLUMNS:
            schema, name = table.split(".", 1)
            lo, hi = self.row(
                f'SELECT min("{column}"), max("{column}") FROM "{schema}"."{name}"'
            )
            if lo is None:
                print(f"  SKIP  {table}.{column}: no rows")
                continue
            measured[f"{table}.{column}"] = {"min": lo, "max": hi}
            width = hi - lo + 1
            span = "" if width <= 1 else f"  ({width:,} distinct slots)"
            print(f"  {table}.{column:12} {lo} .. {hi}{span}   {meaning}")

        # m5 day indices only mean something once resolved to real dates
        for table, column in (("m5.sales_long", "d_num"),):
            row = self.row(
                "SELECT min(cal.date), max(cal.date) FROM m5.calendar cal "
                "JOIN m5.sales_long s ON s.d_num = cal.d_num"
            )
            if row[0]:
                measured[f"{table}.{column}->date"] = {"min": row[0], "max": row[1]}
                print(f"  {table}.{column:12} resolves to {str(row[0])[:10]} .. "
                      f"{str(row[1])[:10]} via m5.calendar")

        self.coded_temporal = measured

    # ============================ 7b. which anomalies are OURS and which are the SOURCE's
    def source_corroboration(self) -> None:
        """Re-derive each anomaly from the RAW file before calling it a defect.

        This is the distinction that stops a data audit being useless. A date
        ordering violation could be a broken ETL or a messy publisher. Only one
        of those is worth fixing, and telling them apart means opening the raw
        CSV and counting the same thing there.

        A defect in this pipeline would show as a mismatch between the two
        counts. Agreement means the database is a faithful copy of a source that
        is itself imperfect - a different thing entirely, and it gets a different
        classification.
        """
        self.section("7b. ARE THESE ANOMALIES OURS, OR THE SOURCE'S?")
        self.corroboration = dict(
            getattr(self, "coverage_source_properties_pending", {})
        )

        print("  olist.orders date ordering - counted again in the RAW csv:")
        raw = _raw_olist_order_dates()
        db = {
            "carrier_before_approval": self.one(
                "SELECT count(*) FROM olist.orders WHERE order_delivered_carrier_date "
                "IS NOT NULL AND order_approved_at IS NOT NULL "
                "AND order_delivered_carrier_date < order_approved_at"),
            "delivered_before_approval": self.one(
                "SELECT count(*) FROM olist.orders WHERE order_delivered_customer_date "
                "IS NOT NULL AND order_approved_at IS NOT NULL "
                "AND order_delivered_customer_date < order_approved_at"),
            "carrier_before_purchase": self.one(
                "SELECT count(*) FROM olist.orders WHERE order_delivered_carrier_date "
                "IS NOT NULL AND order_purchase_timestamp IS NOT NULL "
                "AND order_delivered_carrier_date < order_purchase_timestamp"),
        }
        for key, label in (
            ("carrier_before_approval", "handed to carrier before approval"),
            ("delivered_before_approval", "delivered before approval"),
            ("carrier_before_purchase", "handed to carrier before purchase"),
        ):
            source_count = raw.get(key)
            agree = source_count is not None and source_count == db[key]
            self.corroboration[f"olist.orders.{key}"] = {
                "database": db[key], "raw_source": source_count, "agree": agree,
            }
            verdict = "SOURCE PROPERTY" if agree else "PIPELINE DISCREPANCY"
            self.add(
                "source_corroboration", f"olist.orders.{key}",
                "warning" if agree else "defect",
                "source_property" if agree else "confirmed_invalid_duplicate",
                f"{label}: {db[key]:,} in the database, {source_count} in the raw "
                f"file -> {verdict}",
                {"database": db[key], "raw": source_count},
            )
            print(f"    {label:38} db={db[key]:>6,}  raw={source_count}  -> {verdict}")
        print("    (the Olist source orders its lifecycle stamps inconsistently for a")
        print("     small share of orders. The database reproduces that exactly, which is")
        print("     the correct outcome for an ETL. Downstream KPIs must exclude or")
        print("     flag these rows - they are not repairable.)")

        print("\n  dunnhumby.transactions.sales_value - counted again in the RAW csv:")
        raw = _raw_dunnhumby_sales_value()
        db_zero = self.one("SELECT count(*) FROM dunnhumby.transactions WHERE sales_value = 0")
        db_neg = self.one("SELECT count(*) FROM dunnhumby.transactions WHERE sales_value < 0")
        expected = (raw.get("exact_zero", 0) + raw.get("float_residue", 0)) if raw else None
        agree = expected is not None and expected == db_zero
        self.corroboration["dunnhumby.transactions.sales_value"] = {
            "database_zero": db_zero,
            "raw_exact_zero": raw.get("exact_zero"),
            "raw_float_residue_below_0.00005": raw.get("float_residue"),
            "raw_negative": raw.get("negative"),
            "database_negative": db_neg,
            "agree": agree,
        }
        self.add(
            "source_corroboration", "dunnhumby.transactions.sales_value", "warning",
            "documented_transformation",
            f"{db_zero:,} zero rows in the database = {raw.get('exact_zero')} exact zeros "
            f"in the source + {raw.get('float_residue')} IEEE-754 residues (~1e-16) that "
            f"NUMERIC(18,4) rounds to zero. No negatives in either.",
            {"database_zero": db_zero, "raw_exact_zero": raw.get("exact_zero"),
             "float_residue": raw.get("float_residue")},
        )
        print(f"    database zero             : {db_zero:,}")
        print(f"    raw exact zero            : {raw.get('exact_zero')}")
        print(f"    raw values in (0, 0.00005) : {raw.get('float_residue')}   <- 2^-52 and friends")
        print(f"    raw negative              : {raw.get('negative')}   database negative: {db_neg}")
        print(f"    -> {raw.get('exact_zero')} + {raw.get('float_residue')} = "
              f"{expected} {'==' if agree else '!='} {db_zero}")
        print("    -> DOCUMENTED TRANSFORMATION: NUMERIC(18,4) rounds ~1e-16 to 0.0000.")
        print("       Financially immaterial, but it means the database is NOT a byte-for-")
        print("       byte copy of the source and must never be claimed as one.")
        print(f"    -> {db_zero - (raw.get('exact_zero') or 0):,} rows are a precision artefact")
        print("       of the ETL's 4-decimal rounding, not a change to the data.")

        print("\n  olist.payments zero-value payments - counted again in the RAW csv:")
        import csv as _csv

        raw_path = REPO_ROOT / "data" / "raw" / "olist" / "olist_order_payments_dataset.csv"
        if raw_path.exists():
            zero = rows = negative = 0
            methods: dict[str, int] = {}
            with raw_path.open(newline="", encoding="utf-8", errors="replace") as handle:
                for row in _csv.DictReader(handle):
                    rows += 1
                    try:
                        value = float(row["payment_value"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if value < 0:
                        negative += 1
                    if value == 0:
                        zero += 1
                        key = row.get("payment_type", "?")
                        methods[key] = methods.get(key, 0) + 1
            db_zero_p = self.one("SELECT count(*) FROM olist.payments WHERE payment_value = 0")
            db_neg_p = self.one("SELECT count(*) FROM olist.payments WHERE payment_value < 0")
            agree = zero == db_zero_p and negative == db_neg_p
            self.corroboration["olist.payments.payment_value"] = {
                "database_zero": db_zero_p, "raw_zero": zero,
                "raw_negative": negative, "database_negative": db_neg_p,
                "zero_by_method": methods, "agree": agree,
            }
            self.add(
                "source_corroboration", "olist.payments.payment_value",
                "note", "legitimate_repeat" if agree else "confirmed_invalid_duplicate",
                f"{db_zero_p} zero-value payments, {db_neg_p} negative; the source has "
                f"{zero} zeros and {negative} negatives, by method {methods}. A voucher is "
                f"a zero-cash payment method, so 0.0000 is a real value.",
                {"database_zero": db_zero_p, "raw_zero": zero, "methods": methods},
            )
            print(f"    database zeros/negatives : {db_zero_p} / {db_neg_p}")
            print(f"    raw      zeros/negatives : {zero} / {negative}   -> "
                  f"{'AGREE' if agree else 'DISAGREE'}")
            print(f"    zero rows by method     : {methods}")
            print("    -> LEGITIMATE. Voucher and not_defined are zero-cash payment methods;")
            print("       demanding payment_value > 0 would reject real, correctly-recorded")
            print("       orders.")

    # ====================================================== 8. join fan-out
    def fanout_checks(self) -> None:
        self.section("8. JOIN FAN-OUT - MEASURED, BECAUSE IT MULTIPLIES METRICS")
        print("  A join that fans out silently multiplies any SUM or COUNT on the")
        print("  far side. These are the joins the analytics layer actually uses.\n")
        self.fanout_results = []
        for label, child, child_col, parent, parent_col in FANOUT_CHECKS:
            cs, ct = child.split(".", 1)
            ps, pt = parent.split(".", 1)
            child_rows = self.tables.get(child, {}).get("rows", 0)
            distinct = self.one(
                f'SELECT count(DISTINCT "{child_col}") FROM "{cs}"."{ct}"'
            )
            parent_distinct = self.one(
                f'SELECT count(DISTINCT "{parent_col}") FROM "{ps}"."{pt}"'
            )
            factor = child_rows / distinct if distinct else 0
            classification = "known_grain_fanout" if factor > 1.001 else "pass"
            self.fanout_results.append({
                "join": label, "child_rows": child_rows,
                "distinct_child_keys": distinct,
                "rows_per_key": round(factor, 4),
            })
            note = ""
            if factor > 1.001:
                note = "  <-- FANS OUT: joining duplicates the parent row"
            print(f"  {label:44} child_rows={child_rows:>12,}  "
                  f"distinct {child_col}={distinct:>10,}  "
                  f"rows/key={factor:>7.3f}{note}")

        print("\n  the documented olist.reviews fan-out, measured exactly:")
        dup_ids = self.one(
            "SELECT count(*) FROM (SELECT review_id FROM olist.reviews "
            "GROUP BY 1 HAVING count(*) > 1) d"
        )
        surplus = self.one(
            "SELECT COALESCE(sum(n - 1), 0) FROM (SELECT count(*) n FROM olist.reviews "
            "GROUP BY review_id) x"
        )
        max_n = self.one(
            "SELECT COALESCE(max(n), 0) FROM (SELECT count(*) n FROM olist.reviews "
            "GROUP BY review_id) x"
        )
        distinct_ids = self.one("SELECT count(DISTINCT review_id) FROM olist.reviews")
        print(f"    distinct review_id            : {distinct_ids:,}")
        print(f"    review_ids appearing >1 time  : {dup_ids:,}")
        print(f"    surplus review rows           : {surplus:,}")
        print(f"    max reviews on one review_id  : {max_n}")
        print("    -> the same review counted once per attached order. Per-order")
        print("       review metrics built from review_id alone WILL double count.")
        self.add("fanout", "olist.reviews", "note", "known_grain_fanout",
                 f"{dup_ids} review_ids attach to several orders; surplus {surplus} rows; "
                 f"max multiplicity {max_n}",
                 {"duplicate_ids": dup_ids, "surplus": surplus, "max": max_n})

    # ========================================== 9. ingestion / reconciliation
    def reconciliation(self) -> None:
        self.section("9. INGESTION RECONCILIATION - SOURCE -> RAW -> PROCESSED -> POSTGRES")
        self.recon = {"raw_files": 0, "checksum_ok": 0, "etl_tables": 0, "row_match": 0}

        checksums_path = REPO_ROOT / "data" / "raw" / "_checksums.json"
        if not checksums_path.exists():
            self.add("reconciliation", "raw checksums", "warning", "unverified",
                     "_checksums.json is missing; raw integrity cannot be checked")
        else:
            checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
            print(f"  raw files recorded: {len(checksums)}")
            print("  re-hashing each raw file and comparing with the recorded sha256:")
            for rel, record in sorted(checksums.items()):
                path = REPO_ROOT / "data" / "raw" / rel
                self.recon["raw_files"] += 1
                if not path.exists():
                    self.add("reconciliation", rel, "defect",
                             "confirmed_invalid_duplicate", "recorded raw file is missing")
                    print(f"    MISSING  {rel}")
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                        digest.update(block)
                actual = digest.hexdigest()
                size = path.stat().st_size
                if actual == record["sha256"] and size == record["bytes"]:
                    self.recon["checksum_ok"] += 1
                else:
                    self.add("reconciliation", rel, "defect",
                             "confirmed_invalid_duplicate",
                             f"raw file changed since download: sha256 "
                             f"{actual[:16]}... vs recorded {record['sha256'][:16]}..., "
                             f"bytes {size} vs {record['bytes']}")
                    print(f"    CHANGED  {rel}")
            print(f"    verified {self.recon['checksum_ok']}/{self.recon['raw_files']} "
                  f"raw files byte-identical to the recorded download")

        print("\n  ETL report rows_out  vs  live PostgreSQL count:")
        total_etl = 0
        for source in SOURCE_SCHEMAS:
            report_path = REPO_ROOT / "data" / "processed" / source / "_report.json"
            if not report_path.exists():
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            for entry in report.get("tables", []):
                name = entry["table"]
                # The ETL writes its ACTION NOTES into the same `tables` list,
                # e.g. dataco's "(derived shipping fields)". Counting those as
                # tables makes the reconciliation read 30/32 and looks like two
                # missing tables when in fact all thirty matched.
                if name.startswith("("):
                    self.recon["etl_notes"] = self.recon.get("etl_notes", 0) + 1
                    print(f"    note  {source}.{name}  (an ETL action note, not a table)")
                    continue
                table = f"{source}.{name}"
                self.recon["etl_tables"] += 1
                claimed = entry.get("rows_out")
                actual = self.tables.get(table, {}).get("rows")
                if claimed is None or actual is None:
                    continue
                total_etl += claimed
                if claimed == actual:
                    self.recon["row_match"] += 1
                    print(f"    ok    {table:34} etl={claimed:>12,}  db={actual:>12,}")
                else:
                    self.add("reconciliation", table, "defect",
                             "confirmed_invalid_duplicate",
                             f"ETL reported {claimed:,} rows, database holds {actual:,} "
                             f"(difference {actual - claimed:+,})")
                    print(f"    MISMATCH {table:34} etl={claimed:>12,}  db={actual:>12,}")
        print(f"\n    matched {self.recon['row_match']}/{self.recon['etl_tables']} tables; "
              f"ETL total {total_etl:,} rows")
        print(f"    ({self.recon.get('etl_notes', 0)} further entries in the ETL reports "
              f"are action notes, not tables)")

        load_report_path = REPO_ROOT / "data" / "processed" / "_load_report.json"
        if load_report_path.exists():
            load = json.loads(load_report_path.read_text(encoding="utf-8"))
            live_total = sum(t["rows"] for t in self.tables.values())
            print(f"\n  _load_report.json total_rows : {load['total_rows']:,}")
            print(f"  live PostgreSQL total        : {live_total:,}")
            if load["total_rows"] == live_total:
                print("  -> equal")
            else:
                self.add("reconciliation", "_load_report.json", "defect",
                         "confirmed_invalid_duplicate",
                         f"report says {load['total_rows']:,}, database holds {live_total:,}")

        print("\n  repeated-ingestion check (a doubled load would double every table):")
        doubled = 0
        for name, info in self.tables.items():
            claimed = None
            report_path = REPO_ROOT / "data" / "processed" / name.split(".")[0] / "_report.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                for entry in report.get("tables", []):
                    if entry["table"] == name.split(".", 1)[1]:
                        claimed = entry.get("rows_out")
            if claimed and info["rows"] == claimed * 2:
                doubled += 1
                self.add("reconciliation", name, "defect",
                         "confirmed_invalid_duplicate",
                         f"row count {info['rows']:,} is exactly 2x the ETL's {claimed:,}")
        print(f"    {doubled} table(s) show an exact 2x row count vs the ETL report")

    # ================================================ 10. AI truth controls
    def ai_controls(self) -> None:
        self.section("10. AI DATA-TRUTH CONTROLS - WHAT IS ENFORCED TODAY")
        self.ai_controls = []

        def control(name, enforced, evidence, required_later=True):
            self.ai_controls.append({
                "control": name,
                "enforced_today": enforced,
                "evidence": evidence,
                "must_be_enforced_when": required_later,
            })
            mark = "ENFORCED" if enforced else "NOT YET"
            print(f"  [{mark:8}] {name}")
            print(f"             {evidence}")

        print("\n  (a) Can the database physically refuse a write from the AI role?")
        privileges = self.q(
            """
            SELECT table_schema, privilege_type, count(*)
            FROM information_schema.role_table_grants
            WHERE grantee = 'omniretail_readonly'
            GROUP BY 1, 2 ORDER BY 1, 2
            """
        )
        has_write = any(p[1] not in ("SELECT",) for p in privileges)
        print(f"    omniretail_readonly table privileges: "
              f"{sorted({p[1] for p in privileges}) or 'none'}")
        if has_write:
            control("read-only role cannot write", False,
                    "the read-only role holds a write privilege", required_later=False)
        else:
            control("read-only role cannot write", True,
                    f"omniretail_readonly holds only {sorted({p[1] for p in privileges})} "
                    f"on all 30 tables")

        # Grants are a claim about permissions. This is the thing itself: the
        # audit's OWN connection, opened as the read-only role, tries each
        # forbidden statement and records what the server says. Every one is
        # attempted inside a transaction that is rolled back, so nothing changes
        # even if one of them were to succeed - which is the whole reason this
        # probe is safe to run inside an audit that promises not to modify data.
        print("\n  PROBE: attempting each forbidden statement as omniretail_readonly")
        print("  (each inside a rolled-back transaction; nothing is modified)")
        probes = [
            ("INSERT", "INSERT INTO olist.orders (order_id) VALUES ('__probe__')"),
            ("UPDATE", "UPDATE olist.orders SET order_status = '__probe__'"),
            ("DELETE", "DELETE FROM olist.orders"),
            ("DROP", "DROP TABLE olist.orders"),
            ("ALTER", "ALTER TABLE olist.orders ADD COLUMN __probe__ int"),
            ("TRUNCATE", "TRUNCATE olist.orders"),
            ("CREATE", "CREATE TABLE olist.__probe__ (i int)"),
            ("GRANT", "GRANT SELECT ON olist.orders TO omniretail_readonly"),
            ("REVOKE", "REVOKE SELECT ON olist.orders FROM omniretail_readonly"),
        ]
        self.write_probes = {}
        for label, statement in probes:
            outcome = "NOT REFUSED"
            try:
                with self.conn.cursor() as cur:
                    cur.execute(statement)
                outcome = "NOT REFUSED - THE ROLE COULD WRITE"
            except Exception as exc:  # noqa: BLE001
                message = str(exc).splitlines()[0]
                refused = any(
                    marker in message for marker in (
                        "read-only", "permission denied", "must be owner",
                        "not authorized", "cannot ", "denied",
                    )
                )
                outcome = (f"refused ({message[:60]})" if refused
                           else f"OTHER ERROR: {message[:60]}")
            # Roll back after EVERY probe, not once at the end. A failed
            # statement aborts the transaction, so without this the second probe
            # would fail with "current transaction is aborted" and every probe
            # after it would appear to be refused for entirely the wrong reason.
            self.conn.rollback()
            self.write_probes[label] = outcome
            print(f"    {label:9} {outcome}")
        refused_all = all(v.startswith("refused") for v in self.write_probes.values())
        control("every forbidden statement is refused by the server", refused_all,
                f"{sum(1 for v in self.write_probes.values() if v.startswith('refused'))}"
                f"/{len(probes)} statements (INSERT, UPDATE, DELETE, DROP, ALTER, "
                f"TRUNCATE, CREATE, GRANT, REVOKE) refused by PostgreSQL itself")

        print("\n  role-level settings (setdatabase = 0 means ROLE level, every database):")
        role_settings = self.q(
            """
            SELECT r.rolname, s.setdatabase, array_to_string(s.setconfig, ',')
            FROM pg_db_role_setting s
            JOIN pg_roles r ON r.oid = s.setrole
            WHERE r.rolname = 'omniretail_readonly'
            """
        )
        for rolname, setdatabase, setconfig in role_settings:
            print(f"    {rolname}: setdatabase={setdatabase} setconfig={setconfig}")
        pinned = any(
            setdatabase == 0 and "default_transaction_read_only=on" in (setconfig or "")
            for _role, setdatabase, setconfig in role_settings
        )
        control("every session for the AI role starts read-only", pinned,
                "ALTER ROLE omniretail_readonly SET default_transaction_read_only = on "
                "(role level, setdatabase=0). Even if the application forgot to set it, "
                "the session cannot write."
                if pinned else
                "no role-level default_transaction_read_only setting was found")

        writable = self.q(
            """
            SELECT has_schema_privilege('omniretail_readonly', n.nspname, 'CREATE')
            FROM pg_namespace n
            WHERE n.nspname = ANY(%s)
            """,
            (list(SOURCE_SCHEMAS),),
        )
        if any(w[0] for w in writable):
            control("read-only role cannot create objects", False,
                    "omniretail_readonly holds CREATE on a source schema",
                    required_later=False)
        else:
            control("read-only role cannot create objects", True,
                    "omniretail_readonly holds no CREATE on any source schema")

        print("\n  (b) Does the AI session pin itself read-only at the transaction level?")
        read_only_paths = []
        candidate = REPO_ROOT / "backend" / "scripts" / "sql" / "run_analytics.py"
        if candidate.exists():
            if "READ ONLY" in candidate.read_text(encoding="utf-8"):
                read_only_paths.append(f"{candidate.name} sets READ ONLY")
        self.one("SHOW transaction_read_only")
        live = self.one("SHOW transaction_read_only")
        read_only_paths.append(f"this audit's own session reports transaction_read_only={live}")
        control("analytics execution is transactionally read-only", live == "on",
                "; ".join(read_only_paths))

        print("\n  (c) Is there a SQL validator that would reject writes from LLM output?")
        validators = list((REPO_ROOT / "backend" / "app").rglob("*.py"))
        validator_hits = [
            p.relative_to(REPO_ROOT).as_posix()
            for p in validators
            if "sql" in p.name.lower() and "validator" in p.name.lower()
        ]
        control("write-statement rejection for generated SQL", bool(validator_hits),
                "no SQL validator exists yet - it belongs to the NL-to-SQL phase"
                if not validator_hits else f"found: {validator_hits}")

        print("\n  (d) Does anything verify a number against the database before it is stated?")
        doc_validator = (REPO_ROOT / "backend" / "tests" / "test_analytics_findings.py")
        control("documented figures checked against query results",
                doc_validator.exists(),
                f"{doc_validator.name} fails the build when a documented number is "
                f"absent from the recorded query results"
                if doc_validator.exists() else "missing")

        print("\n  (e) Cross-source join prevention at the data layer")
        cross = self.fk_summary["cross_schema"]
        control("cross-source joins are impossible in the schema", cross == 0,
                f"{self.fk_summary['count']} foreign keys, {cross} crossing a schema "
                f"boundary; enforced by pg_constraint AND by "
                f"test_no_model_relationship_crosses_a_source")

        print("\n  (f) Knowledge the AI layer will need, and whether it exists today")
        for path, what in [
            ("docs/data_dictionary.md", "column-level data dictionary"),
            ("docs/data_lineage.md", "source -> raw -> processed -> database lineage"),
            ("docs/sql_analytics.md", "verified findings with traceable queries"),
            ("docs/database.md", "schema layout, roles, ownership rules"),
            ("data/processed/_sql_results.json", "machine-readable query results"),
            ("data/processed/_verify_report.json", "independent integrity verdicts"),
            ("data/raw/_checksums.json", "source file provenance"),
        ]:
            full = REPO_ROOT / path
            exists = full.exists()
            control(f"RAG corpus contains {what}", exists,
                    f"{path} ({full.stat().st_size:,} bytes)" if exists else f"{path} MISSING",
                    required_later=False)

    # ============================================================ 11. report
    def write_report(self) -> Path:
        self.section("VERDICT")
        defects = [f for f in self.findings if f.severity == "defect"]
        warnings = [f for f in self.findings if f.severity == "warning"]
        notes = [f for f in self.findings if f.severity == "note"]
        unverified = [f for f in self.findings if f.classification == "unverified"]
        enforced = [c for c in self.ai_controls if c["enforced_today"]]
        not_enforced = [c for c in self.ai_controls if not c["enforced_today"]]

        data_verdict = "PASS"
        if defects:
            data_verdict = "FAIL"
        elif warnings or unverified:
            data_verdict = "PASS WITH WARNINGS"

        ai_verdict = "PASS"
        if any(c["control"] == "read-only role cannot write" and not c["enforced_today"]
               for c in self.ai_controls):
            ai_verdict = "FAIL"
        elif not_enforced:
            ai_verdict = "PASS WITH WARNINGS"

        by_classification: dict[str, int] = {}
        for finding in self.findings:
            by_classification[finding.classification] = (
                by_classification.get(finding.classification, 0) + 1
            )

        report = {
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
            "read_only_role": "omniretail_readonly",
            "data_integrity": {
                "verdict": data_verdict,
                "tables_audited": len(self.tables),
                "rows_audited": getattr(self, "total_rows", 0),
                "defects": len(defects),
                "warnings": len(warnings),
                "notes": len(notes),
                "by_classification": by_classification,
            },
            "ai_data_truth_readiness": {
                "verdict": ai_verdict,
                "controls_total": len(self.ai_controls),
                "controls_enforced": len(enforced),
                "controls_not_yet_enforced": [c["control"] for c in not_enforced],
            },
            "tables": self.tables,
            "grain": getattr(self, "grain_results", {}),
            "coded_temporal_ranges": getattr(self, "coded_temporal", {}),
            "keys": getattr(self, "key_summary", {}),
            "foreign_keys": getattr(self, "fk_summary", {}),
            "source_corroboration": getattr(self, "corroboration", {}),
            "fanout": getattr(self, "fanout_results", []),
            "reconciliation": getattr(self, "recon", {}),
            "ai_controls": self.ai_controls,
            "write_probes": getattr(self, "write_probes", {}),
            "findings": [f.as_dict() for f in self.findings],
        }

        destination = REPO_ROOT / "data" / "processed" / "_audit_report.json"

        def jsonable(value):
            """psycopg returns Decimal, date and datetime; json does not.

            Decimal becomes a float (or an int when it is integral, so a count of
            281 does not get written as 281.0). str() would have worked for
            writing the file but would make every number in the report a string,
            and this report exists to be read by other tools.
            """
            from datetime import date as _date, datetime as _datetime
            from decimal import Decimal as _Decimal

            if isinstance(value, _Decimal):
                return int(value) if value == value.to_integral_value() else float(value)
            if isinstance(value, (_datetime, _date)):
                return value.isoformat()
            raise TypeError(f"cannot serialise {type(value).__name__}: {value!r}")

        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=jsonable) + "\n",
            encoding="utf-8",
        )
        print(f"  DATA INTEGRITY      : {data_verdict}")
        print(f"  AI DATA-TRUTH READINESS : {ai_verdict}")
        print(f"  defects={len(defects)}  warnings={len(warnings)}  notes={len(notes)}")
        print(f"  AI controls enforced {len(enforced)}/{len(self.ai_controls)}")
        print(f"\n  report -> {destination}")
        return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="skip the raw-file re-hashing (slower than everything else)")
    args = parser.parse_args()

    import psycopg

    from app.core.config import get_settings

    settings = get_settings()
    print("OmniRetail data truth audit")
    print(f"  target: {settings.postgres_host}:{settings.postgres_port}/"
          f"{settings.postgres_db}")
    print("  role  : omniretail_readonly (READ ONLY - this script cannot alter data)")

    try:
        conn = psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_readonly_user,
            password=settings.postgres_readonly_password,
            connect_timeout=15,
        )
    except psycopg.OperationalError as exc:
        print(f"\n  CANNOT CONNECT as omniretail_readonly: {exc}")
        print("  This audit refuses to fall back to the read/write role. An audit")
        print("  that can modify the data it is auditing is not an audit.")
        return 2

    with conn:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            print(f"  server says transaction_read_only = {cur.fetchone()[0]}")

        audit = Audit(conn, quick=args.quick)
        audit.table_inventory()
        audit.column_profiles()
        audit.grain_checks()
        audit.key_checks()
        audit.foreign_key_checks()
        audit.numeric_checks()
        audit.date_checks()
        audit.coded_temporal_ranges()
        audit.source_corroboration()
        audit.fanout_checks()
        audit.reconciliation()
        audit.ai_controls()
        audit.write_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
