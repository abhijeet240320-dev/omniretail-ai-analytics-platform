"""Clean and transform the dunnhumby "The Complete Journey" dataset.

Source: https://www.dunnhumby.com/source-files/   (official CDN download)
Publisher: dunnhumby.

MEASURED FACTS that drive this module (all verified against the real files):
    2,595,732 transaction lines, 2,500 households, 92,353 products
    276,484 BASKET_ID values == 276,484 (household, basket) groups
        -> BASKET_ID is globally unique, NOT per-household. It is a basket key,
           not a line key: a basket spans several product lines.
    DAY runs 1..711, WEEK_NO runs 1..102
    Discount columns are NEGATIVE (RETAIL_DISC <= 0)
    QUANTITY has a long tail: max 89,638 with 23,101 lines above 1,000
    18,850 lines have SALES_VALUE = 0
    36,786,524 promotion-exposure rows (product x store x week)

The demographic columns are ANONYMISED in this release as classification_1..5.
We keep those names rather than renaming them to the Kaggle-era names, because
that mapping is an inference, not a published fact - see docs/data_dictionary.md.
"""

from __future__ import annotations

import pandas as pd

from etl.common import (
    EtlReport,
    deduplicate,
    read_raw,
    read_raw_chunked,
    to_snake_case,
    write_processed,
    write_processed_chunked,
)
from etl.registry import Manifest, Source

# Above this quantity a line is flagged as an outlier for review. Chosen from the
# measured distribution (99th percentile is 99; 23,101 lines exceed 1,000) and
# documented rather than applied silently.
QUANTITY_OUTLIER_THRESHOLD = 100

PROMO_CHUNK_ROWS = 1_000_000


def run(manifest: Manifest, source: Source) -> EtlReport:
    report = EtlReport(source.id)
    print(f"\n=== dunnhumby: {source.name}")

    households = clean_households(manifest, source, report)
    products = clean_products(manifest, source, report)
    campaigns(manifest, source, report)
    coupons(manifest, source, report)
    transactions = clean_transactions(manifest, source, report)
    promo_exposure(manifest, source, report)
    build_household_rfm(manifest, source, households, products, transactions, report)

    return report


def clean_transactions(
    manifest: Manifest, source: Source, report: EtlReport
) -> pd.DataFrame:
    frame = read_raw(manifest, source, "transaction_data.csv")
    frame = frame.rename(columns={c: to_snake_case(c) for c in frame.columns})
    table = report.new_table(
        "transactions", rows_in=len(frame), raw_inputs=["transaction_data.csv"]
    )

    table.note(
        f"BASKET_ID is globally unique: {frame['basket_id'].nunique():,} baskets "
        f"for {len(frame):,} lines. It identifies a SHOPPING TRIP, so a basket "
        "covers several product lines - grouping by it gives baskets, not lines."
    )
    table.note(
        f"grain is the transaction LINE; the basket key is basket_id "
        f"({frame['household_key'].nunique():,} households, "
        f"{frame['product_id'].nunique():,} products, DAY {frame['day'].min()}-"
        f"{frame['day'].max()}, WEEK_NO {frame['week_no'].min()}-"
        f"{frame['week_no'].max()})"
    )

    # --- DERIVED: gross vs net sales ---------------------------------
    # SALES_VALUE is what the customer actually paid (net). The three discount
    # columns are NEGATIVE, so subtracting them ADDS the discount back to
    # recover the pre-discount shelf value. Verified: all discount values are
    # <= 0, so this arithmetic is sound and is not a sign-convention guess.
    discounts = frame["retail_disc"] + frame["coupon_disc"] + frame["coupon_match_disc"]
    frame["total_discount"] = -discounts  # positive magnitude, easier to read
    frame["gross_sales_value"] = frame["sales_value"] - discounts
    table.note(
        "derived: total_discount = -(retail_disc + coupon_disc + coupon_match_disc) "
        "as a positive magnitude; gross_sales_value = sales_value - discounts. "
        "All discount columns are <= 0 in the real data, so subtracting them "
        "correctly recovers the pre-discount value."
    )

    # --- Data-quality observations, recorded not silently fixed ------
    zero_sales = int((frame["sales_value"] == 0).sum())
    table.note(
        f"{zero_sales:,} line(s) have sales_value = 0 "
        f"({zero_sales / len(frame) * 100:.2f}%) - real free/fully-discounted "
        "lines, kept because removing them would understate promotion impact"
    )

    negative_discount = int((frame["total_discount"] < 0).sum())
    if negative_discount:
        table.note(
            f"{negative_discount:,} line(s) have a net POSITIVE discount, which is "
            "unexpected for a discount column - flagged for review, not removed"
        )

    # --- Outliers are flagged, never deleted -------------------------
    # An extreme quantity is more likely to be a real bulk purchase or a keying
    # error; either way it is a fact about the data. Deleting it would hide the
    # issue, so we keep the row and mark it.
    frame["quantity_outlier"] = frame["quantity"] > QUANTITY_OUTLIER_THRESHOLD
    outliers = int(frame["quantity_outlier"].sum())
    table.note(
        f"flagged {outliers:,} line(s) ({outliers / len(frame) * 100:.2f}%) with "
        f"quantity > {QUANTITY_OUTLIER_THRESHOLD} as quantity_outlier. Max measured "
        f"quantity is {frame['quantity'].max():,}. Rows are KEPT - an outlier is a "
        "fact, not an error, and deleting it would bias every quantity average."
    )
    table.note(
        "no negative quantities exist in this dataset, so it contains no returns; "
        "consequently no return-rate KPI is defined for dunnhumby"
    )
    table.note(
        "DAY is a RELATIVE day index (1-711), not a calendar date. dunnhumby "
        "publishes no calendar, so any date dimension here is derived, and is "
        "labelled as derived wherever it is used."
    )

    write_processed(manifest, source.id, "transactions", frame, table)
    return frame


def clean_products(
    manifest: Manifest, source: Source, report: EtlReport
) -> pd.DataFrame:
    frame = read_raw(manifest, source, "product.csv")
    frame = frame.rename(columns={c: to_snake_case(c) for c in frame.columns})
    table = report.new_table(
        "products", rows_in=len(frame), raw_inputs=["product.csv"]
    )
    table.note(
        "BRAND has only 2 values (National / Private); DEPARTMENT carries the "
        "category, COMMODITY_DESC and SUB_COMMODITY_DESC the finer levels"
    )
    table.note(
        "CURR_SIZE_OF_PRODUCT is a free-text size string (e.g. '16 OZ'), not a "
        "number, so it is kept as text rather than silently coerced"
    )
    write_processed(manifest, source.id, "products", frame, table)
    return frame


def clean_households(
    manifest: Manifest, source: Source, report: EtlReport
) -> pd.DataFrame:
    frame = read_raw(manifest, source, "hh_demographic.csv")
    table = report.new_table(
        "households", rows_in=len(frame), raw_inputs=["hh_demographic.csv"]
    )

    # Deliberately NOT renamed to AGE_DESC / INCOME_DESC etc. The mapping is an
    # inference from value ranges; asserting it in the column names would turn a
    # hypothesis into something that looks like fact.
    table.note(
        "five demographic columns are anonymised by the publisher as "
        "classification_1..classification_5. They are KEPT under those names. "
        "The likely mapping (age, marital status, income, household size, "
        "household composition) is inferred from the value ranges and recorded "
        "in docs/data_dictionary.md as an INFERENCE, not as fact."
    )
    table.note(
        f"covers only {len(frame):,} households against ~2,500 with transactions, "
        "so demographic analysis is a subset analysis and must say so"
    )
    write_processed(manifest, source.id, "households", frame, table)
    return frame


def campaigns(manifest: Manifest, source: Source, report: EtlReport) -> None:
    desc = read_raw(manifest, source, "campaign_desc.csv")
    desc = desc.rename(columns={c: to_snake_case(c) for c in desc.columns})
    table = report.new_table(
        "campaigns", rows_in=len(desc), raw_inputs=["campaign_desc.csv"]
    )
    table.note(
        f"{len(desc)} campaigns of 3 types (TypeA/TypeB/TypeC); start_day and "
        "end_day use the same relative DAY scale as transactions"
    )
    table.note(
        "campaign durations overlap, so campaigns must be treated as concurrent "
        "rather than sequenced - a lift analysis that assumes one campaign at a "
        "time would be wrong"
    )
    write_processed(manifest, source.id, "campaigns", desc, table)

    contacts = read_raw(manifest, source, "campaign_table.csv")
    contacts = contacts.rename(columns={c: to_snake_case(c) for c in contacts.columns})
    contacts_table = report.new_table(
        "campaign_contacts", rows_in=len(contacts), raw_inputs=["campaign_table.csv"]
    )
    contacts_table.note(
        "which households were targeted by which campaign; the joined key to "
        "transactions is household_key"
    )
    write_processed(manifest, source.id, "campaign_contacts", contacts, contacts_table)


def coupons(manifest: Manifest, source: Source, report: EtlReport) -> None:
    frame = read_raw(manifest, source, "coupon.csv")
    frame = frame.rename(columns={c: to_snake_case(c) for c in frame.columns})
    table = report.new_table(
        "coupons", rows_in=len(frame), raw_inputs=["coupon.csv"]
    )
    frame = deduplicate(frame, table, subset=["coupon_upc", "product_id", "campaign"])
    table.note(
        "the raw file repeats the same (coupon, product, campaign) mapping many "
        "times; de-duplicated so the coupon-to-product mapping is unambiguous"
    )
    write_processed(manifest, source.id, "coupons", frame, table)

    redemptions = read_raw(manifest, source, "coupon_redempt.csv")
    redemptions = redemptions.rename(
        columns={c: to_snake_case(c) for c in redemptions.columns}
    )
    red_table = report.new_table(
        "coupon_redemptions",
        rows_in=len(redemptions),
        raw_inputs=["coupon_redempt.csv"],
    )
    red_table.note(
        f"only {len(redemptions):,} redemptions against "
        f"{len(frame):,} coupon-product mappings - redemption is a rare event, "
        "so any redemption rate has a small numerator and must be reported with "
        "its denominator"
    )
    red_table.note(
        "DAY here runs 225-704, a narrower range than transactions (1-711): "
        "coupon redemption only occurs in the later part of the panel"
    )
    write_processed(manifest, source.id, "coupon_redemptions", redemptions, red_table)


def promo_exposure(manifest: Manifest, source: Source, report: EtlReport) -> None:
    """Promotion exposure: which product was on display / in the mailer, by week.

    36,786,524 rows. It cannot be loaded whole, so it is streamed and written in
    chunks. This is the table that makes a REAL promotion-lift analysis possible:
    display and mailer are the actual promotional treatments, so comparing sales
    on treated versus untreated product-weeks is measurement, not invention.
    """
    table = report.new_table("promo_exposure", raw_inputs=["causal_data.csv"])
    table.note(
        "grain is (product_id, store_id, week_no). display and mailer are the "
        "promotional treatments; their combination defines the exposure level."
    )

    def chunks():
        """Yield renamed chunks straight from the raw file.

        A generator rather than a list: materialising 36.8M rows to rename them
        would need several gigabytes for no benefit. The consumer writes each
        chunk and discards it, so peak memory stays at roughly one chunk.
        """
        reader = read_raw_chunked(manifest, source, "causal_data.csv", PROMO_CHUNK_ROWS)
        for chunk in reader:
            yield chunk.rename(columns={c: to_snake_case(c) for c in chunk.columns})

    # write_processed_chunked counts the rows as it writes, so we stream once.
    write_processed_chunked(manifest, source.id, "promo_exposure", chunks(), table)
    table.note(
        f"streamed in chunks of {PROMO_CHUNK_ROWS:,} rows; this is the largest "
        "table in the project and is loaded with COPY, never row-by-row"
    )


def build_household_rfm(
    manifest: Manifest,
    source: Source,
    households: pd.DataFrame,
    products: pd.DataFrame,
    transactions: pd.DataFrame,
    report: EtlReport,
) -> pd.DataFrame:
    """Household-level RFM over the real 711-day panel.

    MONETARY uses `sales_value` (what the household actually paid). Freight does
    not exist in this dataset, and there is no cost column, so no profit-based
    value is computed - inventing one would be fabrication.

    Recency is measured against the LAST DAY in the panel (711), not against
    today, because DAY is a relative index with no calendar mapping.
    """
    table = report.new_table(
        "household_rfm",
        rows_in=len(transactions),
        derived=True,
        raw_inputs=["transaction_data.csv", "hh_demographic.csv"],
    )
    snapshot_day = int(transactions["day"].max())

    rfm = transactions.groupby("household_key", as_index=False).agg(
        last_day=("day", "max"),
        first_day=("day", "min"),
        frequency=("basket_id", "nunique"),
        monetary=("sales_value", "sum"),
        total_discount=("total_discount", "sum"),
        lines=("product_id", "size"),
        distinct_products=("product_id", "nunique"),
    )
    rfm["recency_days"] = snapshot_day - rfm["last_day"]
    rfm["lifespan_days"] = rfm["last_day"] - rfm["first_day"]
    rfm["avg_basket_value"] = rfm["monetary"] / rfm["frequency"].where(
        rfm["frequency"] != 0
    )
    rfm["discount_share"] = rfm["total_discount"] / rfm["monetary"].where(
        rfm["monetary"] != 0
    )

    for metric in ("recency_days", "frequency", "monetary"):
        column = f"{metric.split('_')[0]}_score"
        try:
            rfm[column] = pd.qcut(
                rfm[metric].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]
            ).astype("Int64")
        except ValueError as exc:
            table.note(f"could not compute {column}: {exc}")

    table.note(
        f"derived: recency_days (against panel last day {snapshot_day}), "
        "frequency (distinct baskets), monetary (SUM sales_value), "
        "avg_basket_value, lifespan_days, discount_share, total_discount, "
        "and 1-5 quintile scores"
    )
    table.note(
        "monetary is customer SPEND, not profit: this dataset has no cost column, "
        "so no margin or CLV-in-profit is computed"
    )

    # Attach the demographic columns where they exist, so segmentation can use
    # both spend and attributes. A LEFT join is used because only 801 households
    # have demographics - an inner join would silently drop most customers.
    rfm = rfm.merge(households, on="household_key", how="left")
    missing_demo = int(rfm["classification_1"].isna().sum())
    table.note(
        f"LEFT-joined demographics; {missing_demo:,} household(s) have no "
        "demographic record. An inner join would have silently dropped them."
    )
    write_processed(manifest, source.id, "household_rfm", rfm, table)
    return rfm