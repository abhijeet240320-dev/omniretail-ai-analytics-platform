"""Clean and transform the Olist Brazilian E-Commerce dataset.

Source: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
Licence: CC BY-NC-SA 4.0 (non-commercial, attribution required).

This is the only genuinely RELATIONAL source in the portfolio, which is why it
backs the SQL and join work. Cleaning here is mostly about three things:

1. Parsing text timestamps into real timestamps, and recording how many failed.
2. Preserving the publisher's quirks in raw while normalising them in processed
   (the ``lenght`` typo, for example).
3. Building the aggregations that prevent the two classic Olist mistakes -
   counting a repeat buyer as a new one, and double counting revenue via the
   payments table.
"""

from __future__ import annotations

import pandas as pd

from etl.common import (
    EtlReport,
    TableReport,
    deduplicate,
    parse_datetimes,
    read_raw,
    write_processed,
)
from etl.registry import Manifest, Source

# Publisher typos, corrected only in the processed layer. Raw keeps them so the
# raw data stays a faithful copy of what Olist published.
PRODUCT_COLUMN_FIXES = {
    "product_name_lenght": "product_name_length",
    "product_description_lenght": "product_description_length",
}


def clean_orders(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    """Order lifecycle with delivery performance derived from real timestamps."""
    frame = read_raw(manifest, source, "olist_orders_dataset.csv")
    table = report.new_table(
        "orders", rows_in=len(frame), raw_inputs=["olist_orders_dataset.csv"]
    )

    timestamp_columns = [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ]
    for column in timestamp_columns:
        before = frame[column].isna().sum()
        frame[column] = parse_datetimes(frame[column])
        after = frame[column].isna().sum()
        if after > before:
            table.note(
                f"{column}: {after - before:,} value(s) could not be parsed as a "
                "timestamp and became NULL"
            )

    # --- DERIVED FIELDS (formulas stated, so they are auditable) ----------
    # delivery_days  = delivered to customer - purchased
    # estimated_days = estimated delivery  - purchased
    # delay_days     = delivered to customer - estimated delivery
    #                  POSITIVE means it arrived LATE.
    # is_late        = delay_days > 0  (NULL when the order never arrived)
    purchase = frame["order_purchase_timestamp"]
    delivered = frame["order_delivered_customer_date"]
    estimated = frame["order_estimated_delivery_date"]

    frame["purchase_date"] = purchase.dt.date
    frame["delivered_to_customer_date"] = delivered.dt.date
    frame["estimated_delivery_date"] = estimated.dt.date

    frame["delivery_days"] = (delivered - purchase).dt.total_seconds() / 86400
    frame["estimated_days"] = (estimated - purchase).dt.total_seconds() / 86400
    frame["delay_days"] = (delivered - estimated).dt.total_seconds() / 86400

    # A nullable boolean, so "not yet delivered" stays distinct from "delivered
    # on time". Naively writing `(delay_days > 0).where(...)` would turn every
    # on-time order (delay <= 0, i.e. False) into NULL as well as the missing
    # ones, which would erase the entire on-time population.
    frame["is_late"] = (frame["delay_days"] > 0).astype("boolean").mask(
        frame["delay_days"].isna()
    )

    frame["is_delivered"] = frame["order_status"].eq("delivered")

    # Delivered orders with no delivery date are a genuine inconsistency; count
    # them rather than quietly letting them skew the delivery KPIs.
    inconsistent = int((frame["is_delivered"] & delivered.isna()).sum())
    if inconsistent:
        table.note(
            f"{inconsistent:,} order(s) have status 'delivered' but no "
            "delivered_customer_date - they are excluded from delivery-day KPIs"
        )

    by_status = frame["order_status"].value_counts().to_dict()
    table.note("order_status distribution: " + ", ".join(
        f"{k}={v:,}" for k, v in sorted(by_status.items())
    ))
    table.note(
        "derived: delivery_days, estimated_days, delay_days (positive = late), "
        "is_late, is_delivered"
    )

    write_processed(manifest, source.id, "orders", frame, table)
    return frame


def clean_order_items(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    frame = read_raw(manifest, source, "olist_order_items_dataset.csv")
    table = report.new_table(
        "order_items", rows_in=len(frame), raw_inputs=["olist_order_items_dataset.csv"]
    )

    frame["shipping_limit_date"] = parse_datetimes(frame["shipping_limit_date"])

    # item_total = product price + freight actually charged for that item.
    # Kept as separate columns too, because "revenue" and "freight cost" are
    # different business questions and collapsing them loses information.
    frame["item_total"] = frame["price"] + frame["freight_value"]
    table.note(
        "derived: item_total = price + freight_value (product revenue and "
        "freight kept separately as well)"
    )

    key = ["order_id", "order_item_id"]
    duplicates = int(frame.duplicated(subset=key).sum())
    if duplicates:
        table.note(f"{duplicates:,} duplicate (order_id, order_item_id) rows found")
    write_processed(manifest, source.id, "order_items", frame, table)
    return frame


def clean_payments(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    frame = read_raw(manifest, source, "olist_order_payments_dataset.csv")
    table = report.new_table(
        "payments",
        rows_in=len(frame),
        raw_inputs=["olist_order_payments_dataset.csv"],
    )

    split_orders = int(
        (frame.groupby("order_id").size() > 1).sum()
    )
    table.note(
        f"{split_orders:,} order(s) are paid in more than one row - this is why "
        "payments must be aggregated before being joined to items"
    )
    write_processed(manifest, source.id, "payments", frame, table)
    return frame


def clean_order_payments_agg(
    manifest: Manifest, source: Source, payments: pd.DataFrame, report: EtlReport
) -> pd.DataFrame:
    """One row per order, so revenue is never double counted.

    WHY THIS TABLE EXISTS
    ---------------------
    ``olist_order_payments_dataset.csv`` has one row per payment METHOD. Joining
    it to ``order_items`` (which has several rows per order) multiplies both
    sides and inflates revenue. Any analysis touching payments must go through
    this per-order aggregate instead.
    """
    table = report.new_table(
        "order_payments_agg",
        rows_in=len(payments),
        derived=True,
        raw_inputs=["olist_order_payments_dataset.csv"],
    )

    grouped = payments.groupby("order_id", as_index=False).agg(
        payment_total=("payment_value", "sum"),
        payment_count=("payment_sequential", "count"),
        payment_installments_max=("payment_installments", "max"),
        payment_type_count=("payment_type", "nunique"),
    )
    # The dominant payment method is the one with the largest value, which is
    # more meaningful than "the first row" when a customer splits a payment.
    dominant = (
        payments.sort_values("payment_value", ascending=False)
        .drop_duplicates(subset="order_id")
        .set_index("order_id")["payment_type"]
    )
    grouped["primary_payment_type"] = grouped["order_id"].map(dominant)

    table.note(
        "derived: payment_total = SUM(payment_value), plus payment_count, "
        "payment_installments_max, primary_payment_type"
    )
    write_processed(manifest, source.id, "order_payments_agg", grouped, table)
    return grouped


def clean_reviews(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    frame = read_raw(manifest, source, "olist_order_reviews_dataset.csv")
    table = report.new_table(
        "reviews", rows_in=len(frame), raw_inputs=["olist_order_reviews_dataset.csv"]
    )

    for column in ("review_creation_date", "review_answer_timestamp"):
        frame[column] = parse_datetimes(frame[column])

    # Response time is a real, useful service metric derived from two real
    # columns. Kept only where both timestamps exist.
    frame["review_response_hours"] = (
        frame["review_answer_timestamp"] - frame["review_creation_date"]
    ).dt.total_seconds() / 3600

    multi = int((frame.groupby("review_id").size() > 1).sum())
    table.note(
        f"{multi:,} review_id(s) appear more than once; review rows are not "
        "strictly 1:1 with orders (99,224 reviews vs 99,441 orders)"
    )
    table.note(
        "derived: review_response_hours = answer_timestamp - creation_date; "
        "review_comment_title/message are deliberately kept despite high null "
        "rates (88%/59%) because the nulls are real 'no comment left' values"
    )
    write_processed(manifest, source.id, "reviews", frame, table)
    return frame


def clean_customers(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    frame = read_raw(manifest, source, "olist_customers_dataset.csv")
    table = report.new_table(
        "customers", rows_in=len(frame), raw_inputs=["olist_customers_dataset.csv"]
    )

    # THE most important distinction in this dataset.
    table.note(
        f"customer_id is per-order ({frame['customer_id'].nunique():,} unique) "
        f"while customer_unique_id identifies the person "
        f"({frame['customer_unique_id'].nunique():,} unique). Repeat-buyer, RFM "
        "and CLV analysis MUST use customer_unique_id."
    )
    repeat_ratio = 1 - frame["customer_unique_id"].nunique() / frame["customer_id"].nunique()
    table.note(
        f"{repeat_ratio * 100:.1f}% of customer_id values belong to a person with "
        "more than one order"
    )
    write_processed(manifest, source.id, "customers", frame, table)
    return frame


def clean_products(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    frame = read_raw(manifest, source, "olist_products_dataset.csv")
    table = report.new_table(
        "products",
        rows_in=len(frame),
        raw_inputs=[
            "olist_products_dataset.csv",
            "product_category_name_translation.csv",
        ],
    )

    renamed = {k: v for k, v in PRODUCT_COLUMN_FIXES.items() if k in frame.columns}
    if renamed:
        frame = frame.rename(columns=renamed)
        table.note(
            "corrected the publisher's misspelling of 'lenght' -> 'length' in the "
            "processed layer only (raw is preserved unchanged): "
            + ", ".join(f"{k}->{v}" for k, v in renamed.items())
        )

    nulls = int(frame["product_category_name"].isna().sum())
    if nulls:
        # We do NOT invent a category. We label the absence clearly so it can be
        # excluded or reported on deliberately.
        frame["product_category_name"] = frame["product_category_name"].fillna(
            "unknown"
        )
        table.note(
            f"{nulls:,} product(s) have no category; filled with the literal "
            "'unknown' rather than inventing one. This is a real gap in the source."
        )

    translation = read_raw(manifest, source, "product_category_name_translation.csv")
    frame = frame.merge(
        translation, on="product_category_name", how="left"
    )
    unmapped = int(frame["product_category_name_english"].isna().sum())
    table.note(
        f"joined the PT->EN category translation; {unmapped:,} row(s) have no "
        "English name (including 'unknown')"
    )
    write_processed(manifest, source.id, "products", frame, table)
    return frame


def clean_sellers(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    frame = read_raw(manifest, source, "olist_sellers_dataset.csv")
    table = report.new_table(
        "sellers", rows_in=len(frame), raw_inputs=["olist_sellers_dataset.csv"]
    )
    write_processed(manifest, source.id, "sellers", frame, table)
    return frame


def clean_geolocation(manifest: Manifest, source: Source, report: EtlReport) -> pd.DataFrame:
    """Aggregate the ~1M raw rows into one reliable point per zip prefix.

    The raw file has 1,000,163 rows with 261,831 exact duplicates, all keyed on
    a zip-code PREFIX rather than an address. Joining it row-for-row would
    fan out every order. Aggregating to one representative point per prefix is
    the only honest way to attach geography to an order.
    """
    frame = read_raw(manifest, source, "olist_geolocation_dataset.csv")
    table = report.new_table(
        "geolocation_by_zip",
        rows_in=len(frame),
        derived=True,
        raw_inputs=["olist_geolocation_dataset.csv"],
    )

    before = len(frame)
    frame = deduplicate(frame, table, subset=None)
    table.note(f"de-duplicated {before - len(frame):,} identical rows")

    grouped = frame.groupby("geolocation_zip_code_prefix", as_index=False).agg(
        latitude=("geolocation_lat", "mean"),
        longitude=("geolocation_lng", "mean"),
        points=("geolocation_lat", "size"),
        city=("geolocation_city", lambda s: s.mode().iloc[0] if len(s.mode()) else None),
        state=("geolocation_state", lambda s: s.mode().iloc[0] if len(s.mode()) else None),
    )
    table.note(
        "derived: one row per zip prefix with the MEAN lat/lng of all observations, "
        "the modal city/state, and the observation count"
    )
    write_processed(manifest, source.id, "geolocation_by_zip", grouped, table)
    return grouped


def clean_marketing_funnel(
    manifest: Manifest, source: Source, report: EtlReport
) -> None:
    leads = read_raw(manifest, source, "olist_marketing_qualified_leads_dataset.csv")
    leads_table = report.new_table(
        "mql",
        rows_in=len(leads),
        raw_inputs=["olist_marketing_qualified_leads_dataset.csv"],
    )
    leads["first_contact_date"] = parse_datetimes(leads["first_contact_date"])
    write_processed(manifest, source.id, "mql", leads, leads_table)

    deals = read_raw(manifest, source, "olist_closed_deals_dataset.csv")
    deals_table = report.new_table(
        "closed_deals",
        rows_in=len(deals),
        raw_inputs=["olist_closed_deals_dataset.csv"],
    )
    deals["won_date"] = parse_datetimes(deals["won_date"])

    # Several funnel columns are ~92% empty. They are real but nearly useless;
    # recording the rates keeps anyone from building a feature on them.
    high_null = {
        column: round(deals[column].isna().mean() * 100, 2)
        for column in deals.columns
        if deals[column].isna().mean() > 0.5
    }
    if high_null:
        deals_table.note(
            "columns that are majority-NULL (kept, but not useful as features): "
            + ", ".join(f"{k}={v}%" for k, v in sorted(high_null.items()))
        )
    write_processed(manifest, source.id, "closed_deals", deals, deals_table)


def build_customer_rfm(
    manifest: Manifest,
    source: Source,
    customers: pd.DataFrame,
    orders: pd.DataFrame,
    items: pd.DataFrame,
    report: EtlReport,
) -> pd.DataFrame:
    """RFM at the PERSON level, using customer_unique_id.

    WHY THIS IS BUILT THE WAY IT IS
    -------------------------------
    ``customer_id`` is per-order, so grouping by it would give every person a
    frequency of exactly 1 and make RFM meaningless. We join orders to
    ``customer_unique_id`` first and group on that.

    Monetary value uses ``price`` (product revenue), NOT ``payment_value``: the
    payments table can hold several rows per order, so using it would inflate
    spend for split payments. Freight is excluded from monetary because it is a
    logistics charge, not customer spend on goods.

    Recency is measured against the last observed purchase in the dataset. That
    is a deliberately stated reference point - it is not "today", because the
    data ends in 2018 and comparing against today would make every customer
    look identically dormant.
    """
    table = report.new_table(
        "customer_rfm",
        rows_in=len(customers),
        derived=True,
        raw_inputs=[
            "olist_orders_dataset.csv",
            "olist_customers_dataset.csv",
            "olist_order_items_dataset.csv",
        ],
    )

    person_map = customers[["customer_id", "customer_unique_id"]]
    order_person = orders[["order_id", "customer_id", "order_purchase_timestamp"]].merge(
        person_map, on="customer_id", how="left"
    )

    item_value = (
        items.groupby("order_id", as_index=False)["price"].sum().rename(
            columns={"price": "order_goods_value"}
        )
    )
    merged = order_person.merge(item_value, on="order_id", how="left")

    snapshot = merged["order_purchase_timestamp"].max()
    table.note(
        f"recency is measured against the last purchase in the dataset "
        f"({snapshot.date()}), not against today - the data ends in 2018"
    )

    rfm = merged.groupby("customer_unique_id", as_index=False).agg(
        last_purchase=("order_purchase_timestamp", "max"),
        first_purchase=("order_purchase_timestamp", "min"),
        frequency=("order_id", "nunique"),
        monetary=("order_goods_value", "sum"),
    )
    rfm["recency_days"] = (snapshot - rfm["last_purchase"]).dt.total_seconds() / 86400
    rfm["lifespan_days"] = (
        rfm["last_purchase"] - rfm["first_purchase"]
    ).dt.total_seconds() / 86400
    rfm["avg_order_value"] = rfm["monetary"] / rfm["frequency"]

    # Quintile scores are only meaningful where a distribution exists. We
    # compute them, but do NOT invent a score for customers that fall outside a
    # bin - the count of unresolved rows is reported.
    for metric, ascending in (("recency_days", False), ("frequency", True), ("monetary", True)):
        column = f"{metric.split('_')[0]}_score"
        try:
            rfm[column] = pd.qcut(
                rfm[metric].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]
            ).astype("Int64")
        except ValueError as exc:
            table.note(f"could not compute {column}: {exc}")
    unresolved = int(rfm["recency_score"].isna().sum()) if "recency_score" in rfm else 0
    if unresolved:
        table.note(f"{unresolved:,} customer(s) have no quintile score assigned")

    table.note(
        "derived: recency_days, frequency (distinct orders), monetary "
        "(SUM of item price, freight excluded), avg_order_value, lifespan_days, "
        "and 1-5 quintile scores for R, F and M"
    )
    table.note(
        f"grain is customer_unique_id: {rfm['customer_unique_id'].nunique():,} real "
        f"people (vs {customers['customer_id'].nunique():,} order-scoped customer_id)"
    )
    write_processed(manifest, source.id, "customer_rfm", rfm, table)
    return rfm


def run(manifest: Manifest, source: Source) -> EtlReport:
    """Clean and transform the whole source. Returns the lineage report."""
    report = EtlReport(source.id)
    print(f"\n=== olist: {source.name}")

    orders = clean_orders(manifest, source, report)
    items = clean_order_items(manifest, source, report)
    payments = clean_payments(manifest, source, report)
    clean_order_payments_agg(manifest, source, payments, report)
    clean_reviews(manifest, source, report)
    customers = clean_customers(manifest, source, report)
    clean_products(manifest, source, report)
    clean_sellers(manifest, source, report)
    clean_geolocation(manifest, source, report)
    clean_marketing_funnel(manifest, source, report)
    build_customer_rfm(manifest, source, customers, orders, items, report)

    return report
