"""Clean and transform the DataCo SMART Supply Chain dataset.

Source: Mendeley Data, Constante/Silva/Pereira (2019), V5.
doi:10.17632/8x2fvg2k6.5   (see backend/config/datasets.yaml)

This file arrives **fully denormalised**: order, customer, product and logistics
attributes are all stacked in 53 columns, one row per ORDER LINE. The cleaning
job is therefore mostly *normalisation* - splitting one wide table into the four
entities it actually contains, so that SQL can join them properly and so that
customer and product attributes are not repeated 180,519 times.

MEASURED GRAIN (from the real file, not from documentation):
    180,519 rows  ==  180,519 unique Order Item Id   -> grain is the order line
     65,752 unique Order Id                          -> 2.75 lines per order
     20,652 unique Customer Id
        118 unique Product Card Id
"""

from __future__ import annotations

import pandas as pd

from etl.common import (
    EtlReport,
    drop_all_null_columns,
    drop_constant_columns,
    parse_datetimes,
    read_raw,
    to_snake_case,
    write_processed,
)
from etl.registry import Manifest, Source

ORDER_DATE = "order date (DateOrders)"
SHIP_DATE = "shipping date (DateOrders)"

# Columns that are constant or empty in the real file, confirmed by profiling.
# `Customer Email` and `Customer Password` are both the literal masked
# placeholder "XXXXXXXXX" (one distinct value each). They are dropped both
# because they carry no information AND because loading a column called
# "password" - even a masked one - into an analytics database is a needless
# liability.
ALWAYS_DROP = ["Customer Email", "Customer Password", "Product Status"]


def run(manifest: Manifest, source: Source) -> EtlReport:
    report = EtlReport(source.id)
    print(f"\n=== dataco: {source.name}")

    frame = read_raw(manifest, source, "DataCoSupplyChainDataset.csv")
    print(f"    read {len(frame):,} rows x {len(frame.columns)} columns")

    # ---------------------------------------------------------------
    # 1. Normalise column names
    # ---------------------------------------------------------------
    frame = frame.rename(columns={c: to_snake_case(c) for c in frame.columns})

    # ---------------------------------------------------------------
    # 2. Parse the two date columns
    # ---------------------------------------------------------------
    drop_table = report.new_table(
        "(column hygiene)",
        rows_in=len(frame),
        raw_inputs=["DataCoSupplyChainDataset.csv"],
    )
    for original, snake in ((ORDER_DATE, "order_date"), (SHIP_DATE, "shipping_date")):
        source_column = to_snake_case(original)
        before = frame[source_column].isna().sum()
        frame[snake] = parse_datetimes(frame[source_column], fmt="%m/%d/%Y %H:%M")
        failed = frame[snake].isna().sum() - before
        drop_table.note(
            f"{snake}: parsed from '{original}' (US MM/DD/YYYY HH:MM); "
            f"{failed:,} value(s) unparseable"
        )
        frame = frame.drop(columns=[source_column])

    # ---------------------------------------------------------------
    # 3. Column hygiene: remove what cannot carry information
    # ---------------------------------------------------------------
    frame = frame.drop(columns=[c for c in ALWAYS_DROP if c in frame.columns])
    drop_table.note(
        "dropped always-useless columns: Customer Email and Customer Password "
        "(both are the single masked placeholder 'XXXXXXXXX'), and Product Status "
        "(one single value in every row)"
    )
    frame = drop_all_null_columns(frame, drop_table)
    frame = drop_constant_columns(frame, drop_table)
    drop_table.rows_out = len(frame)

    # ---------------------------------------------------------------
    # 4. DERIVED: shipping performance
    # ---------------------------------------------------------------
    # shipping_delay_days = actual shipping days - scheduled shipping days.
    # POSITIVE means the shipment took longer than promised, i.e. it was late.
    # NEGATIVE means it arrived early, which is valid business behaviour and is
    # deliberately NOT removed - treating early delivery as bad data would
    # discard a genuine segment of good performance.
    frame["shipping_delay_days"] = (
        frame["days_for_shipping_real"] - frame["days_for_shipment_scheduled"]
    )
    frame["is_late"] = frame["late_delivery_risk"].astype("boolean")
    frame["lead_time_days"] = (
        frame["shipping_date"] - frame["order_date"]
    ).dt.total_seconds() / 86400

    # profit margin per line. Guarded against a zero denominator rather than
    # producing inf, which would silently poison any average.
    frame["profit_margin"] = (
        frame["order_profit_per_order"] / frame["sales"].where(frame["sales"] != 0)
    )

    delay_table = report.new_table(
        "(derived shipping fields)",
        rows_in=len(frame),
        raw_inputs=["DataCoSupplyChainDataset.csv"],
    )
    delay_table.rows_out = len(frame)
    early = int((frame["shipping_delay_days"] < 0).sum())
    on_time = int((frame["shipping_delay_days"] == 0).sum())
    late = int((frame["shipping_delay_days"] > 0).sum())
    delay_table.note(
        f"shipping_delay_days = days_for_shipping_real - days_for_shipment_scheduled: "
        f"{early:,} early, {on_time:,} exactly on time, {late:,} late"
    )
    delay_table.note(
        "is_late mirrors the publisher's own late_delivery_risk flag (kept as the "
        "source of truth rather than recomputed, so we can cross-check it)"
    )
    delay_table.note(
        "lead_time_days = shipping_date - order_date; profit_margin = "
        "order_profit_per_order / sales (NULL where sales = 0, never infinite)"
    )

    # ---------------------------------------------------------------
    # 5. Split into entities
    # ---------------------------------------------------------------
    # These four are entity extractions from one wide table: the row counts are
    # intentionally smaller than the line count, so "dropped" is not data loss.
    orders_table = report.new_table(
        "orders", derived=True, raw_inputs=["DataCoSupplyChainDataset.csv"]
    )
    items_table = report.new_table(
        "order_items", raw_inputs=["DataCoSupplyChainDataset.csv"]
    )
    customers_table = report.new_table(
        "customers", derived=True, raw_inputs=["DataCoSupplyChainDataset.csv"]
    )
    products_table = report.new_table(
        "products", derived=True, raw_inputs=["DataCoSupplyChainDataset.csv"]
    )

    # --- products (one row per Product Card Id) ---
    product_columns = [
        "product_card_id",
        "product_name",
        "product_price",
        "product_category_id",
        "category_name",
        "category_id",
        "department_id",
        "department_name",
    ]
    product_columns = [c for c in product_columns if c in frame.columns]
    products = frame[product_columns].drop_duplicates(subset=["product_card_id"])
    items_table.rows_in = len(frame)
    products_table.rows_in = len(frame)
    products_table.note(
        f"collapsed {len(frame):,} lines into {len(products):,} distinct products "
        "(one per product_card_id)"
    )
    write_processed(manifest, source.id, "products", products, products_table)

    # --- customers (one row per Customer Id) ---
    customer_columns = [
        "customer_id",
        "customer_fname",
        "customer_lname",
        "customer_segment",
        "customer_city",
        "customer_state",
        "customer_country",
        "customer_zipcode",
        "latitude",
        "longitude",
    ]
    customer_columns = [c for c in customer_columns if c in frame.columns]
    customers = frame[customer_columns].drop_duplicates(subset=["customer_id"])
    customers_table.rows_in = len(frame)
    customers_table.note(
        f"collapsed {len(frame):,} lines into {len(customers):,} distinct customers. "
        "Latitude/Longitude are REAL in this release (11,250 / 4,487 distinct "
        "values) - the widely repeated claim that they are blank is wrong."
    )
    write_processed(manifest, source.id, "customers", customers, customers_table)

    # --- order_items (line grain) ---
    item_columns = [
        "order_item_id",
        "order_id",
        "product_card_id",
        "order_item_quantity",
        "order_item_product_price",
        "order_item_discount",
        "order_item_discount_rate",
        "order_item_profit_ratio",
        "order_item_total",
        "sales",
        "order_profit_per_order",
        "profit_margin",
        "benefit_per_order",
        "sales_per_customer",
    ]
    items = frame[[c for c in item_columns if c in frame.columns]].copy()
    items_table.note(
        "grain is the ORDER LINE: order_item_id is unique across all "
        f"{len(items):,} rows, while order_id repeats (65,752 distinct orders)"
    )
    items_table.note(
        "these numeric columns were verified to VARY within an order, so summing "
        "them per order is correct - they are line-level, not order-level repeats"
    )
    write_processed(manifest, source.id, "order_items", items, items_table)

    # --- orders (one row per Order Id) ---
    order_level_columns = [
        "order_id",
        "customer_id",
        "type",
        "order_date",
        "shipping_date",
        "days_for_shipping_real",
        "days_for_shipment_scheduled",
        "shipping_delay_days",
        "lead_time_days",
        "delivery_status",
        "is_late",
        "order_status",
        "shipping_mode",
        "market",
        "order_region",
        "order_country",
        "order_state",
        "order_city",
    ]
    order_columns_present = [c for c in order_level_columns if c in frame.columns]

    # Line-level values must be SUMMED, not taken from an arbitrary row, or an
    # order's value would silently become one of its lines.
    line_sums = (
        frame.groupby("order_id", as_index=False)
        .agg(
            order_items_count=("order_item_id", "nunique"),
            total_quantity=("order_item_quantity", "sum"),
            order_value=("order_item_total", "sum"),
            order_sales=("sales", "sum"),
            order_profit=("order_profit_per_order", "sum"),
        )
    )

    orders = (
        frame[order_columns_present]
        .drop_duplicates(subset=["order_id"])
        .merge(line_sums, on="order_id", how="left")
    )
    orders_table.rows_in = len(frame)
    orders_table.note(
        f"collapsed {len(frame):,} lines into {len(orders):,} orders; line-level "
        "numerics were SUMMED per order (verified to vary per line, so a "
        "first-row pick would have been wrong)"
    )
    orders_table.note(
        "derived: order_items_count, total_quantity, order_value, order_sales, "
        "order_profit"
    )
    write_processed(manifest, source.id, "orders", orders, orders_table)

    return report