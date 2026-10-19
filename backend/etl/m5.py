"""Clean and transform the Walmart M5 Forecasting dataset.

Source: Kaggle "M5 Forecasting - Accuracy" (Walmart); mirrored on Hugging Face.
Citation: Makridakis, Spiliotis & Assimakopoulos (2022), IJF 38(4), 1346-1364.

THE CENTRAL PROBLEM WITH THIS DATASET
-------------------------------------
``sales_train_evaluation.csv`` is stored WIDE: 30,490 rows, one per item-store
series, with **one column per day** (d_1 … d_1941) - 1,947 columns in total. No
relational database and no time-series model can consume that shape, so the
single most valuable transformation in this module is the **melt** from wide to
long:

    30,490 rows x 1,941 days   ->   59,181,090 rows of (series, day, units)

That output is far too large to hold in memory at once, so it is produced
chunk-by-chunk: 2,000 series are read, melted and written, then discarded. Peak
memory stays around one chunk instead of growing with the file.

In the same pass we accumulate a daily total across every series. That gives the
dashboard a real daily aggregate for free, rather than requiring a second scan of
a 59-million-row table.
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from etl.common import (
    EtlReport,
    parse_datetimes,
    read_raw,
    to_snake_case,
    write_processed,
    write_processed_chunked,
)
from etl.registry import Manifest, Source, raw_dir

# Series per chunk. 2,000 x 1,941 days = ~3.9M melted rows per chunk, which
# keeps peak memory comfortably under a gigabyte.
SERIES_CHUNK = 2_000

ID_COLUMNS = ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]


def run(manifest: Manifest, source: Source) -> EtlReport:
    report = EtlReport(source.id)
    print(f"\n=== m5: {source.name}")

    calendar = clean_calendar(manifest, source, report)
    clean_sell_prices(manifest, source, report)
    build_series_dimension(manifest, source, report)
    sales_long(manifest, source, calendar, report)

    return report


def clean_calendar(
    manifest: Manifest, source: Source, report: EtlReport
) -> pd.DataFrame:
    frame = read_raw(manifest, source, "calendar.csv")
    table = report.new_table(
        "calendar", rows_in=len(frame), raw_inputs=["calendar.csv"]
    )

    frame = frame.rename(columns={c: to_snake_case(c) for c in frame.columns})
    frame["date"] = parse_datetimes(frame["date"], fmt="%Y-%m-%d")

    # d_num is the integer N in "d_N". Without it, joining the sales table to the
    # calendar would mean string-matching on "d_1234", which is slower and invites
    # mistakes ("d_1" would also match a prefix of "d_1000").
    frame["d_num"] = frame["d"].str.replace("d_", "", regex=False).astype("int32")

    table.note(
        f"calendar covers {frame['date'].min().date()} to {frame['date'].max().date()} "
        f"({len(frame):,} days); 'd' is the day code and d_num is its integer form, "
        "added so joins are numeric rather than string-prefix matches"
    )
    event_2_null = int(frame["event_name_2"].isna().sum())
    table.note(
        f"event_name_2 is NULL on {event_2_null:,} of {len(frame):,} days "
        f"({event_2_null / len(frame) * 100:.2f}%) - most days simply have one event. "
        "This is real sparsity, not missing data, and is left as NULL rather than "
        "filled with a placeholder that would look like a real event."
    )
    write_processed(manifest, source.id, "calendar", frame, table)
    return frame


def clean_sell_prices(manifest: Manifest, source: Source, report: EtlReport) -> None:
    frame = read_raw(manifest, source, "sell_prices.csv")
    table = report.new_table(
        "sell_prices", rows_in=len(frame), raw_inputs=["sell_prices.csv"]
    )

    frame["sell_price"] = pd.to_numeric(frame["sell_price"], errors="coerce")
    invalid = int(frame["sell_price"].isna().sum())
    if invalid:
        table.note(f"{invalid:,} price value(s) could not be parsed as numbers")
    non_positive = int((frame["sell_price"] <= 0).sum())
    table.note(
        f"{non_positive:,} row(s) have a non-positive price; kept, because removing "
        "them would break the price history of those item-weeks"
    )

    # Prices are WEEKLY while sales are DAILY. Stating that explicitly matters,
    # because any "daily revenue" derived from these two tables carries an
    # assumption that the weekly price holds for each day of that week.
    table.note(
        "prices are WEEKLY (keyed by wm_yr_wk) while sales are DAILY. Revenue "
        "derived as units x sell_price is therefore an ESTIMATE that assumes the "
        "week's price applies on each day of that week."
    )
    table.note(
        "the table is SPARSE: not every item-store has a price in every week "
        "(items enter and leave assortment), so joins must tolerate missing prices "
        "rather than treating them as zero"
    )
    write_processed(manifest, source.id, "sell_prices", frame, table)


def build_series_dimension(
    manifest: Manifest, source: Source, report: EtlReport
) -> None:
    """Extract the item/store dimension while reading only the id columns.

    Reading just six columns of a 1,947-column, 121 MB file is dramatically
    cheaper than reading all of it, and this dimension is all the melt actually
    needs in order to label its rows.
    """
    table = report.new_table(
        "series", derived=True, raw_inputs=["sales_train_evaluation.csv"]
    )
    frame = read_raw(
        manifest, source, "sales_train_evaluation.csv", usecols=ID_COLUMNS
    )
    table.rows_in = len(frame)

    # Parse the composite id rather than trusting the separate columns alone:
    # keeping both lets us verify they agree.
    parsed = frame["id"].str.rsplit("_", n=1, expand=True)
    frame["id_base"] = parsed[0]
    frame["horizon"] = parsed[1]

    table.note(
        f"{len(frame):,} item-store series across {frame['store_id'].nunique()} "
        f"stores, {frame['state_id'].nunique()} states, {frame['cat_id'].nunique()} "
        "categories and 3 departments"
    )
    table.note(
        "id decomposes as {cat}_{dept}_{item}_{state}_{store}_{horizon}; the "
        "horizon suffix ('evaluation') marks which Kaggle split the file holds"
    )
    if frame["horizon"].nunique() == 1:
        table.note(f"all rows share the same horizon suffix: {frame['horizon'].iloc[0]!r}")
    write_processed(manifest, source.id, "series", frame, table)


def sales_long(
    manifest: Manifest, source: Source, calendar: pd.DataFrame, report: EtlReport
) -> None:
    """Melt the wide day columns into long rows, and accumulate a daily total."""
    table = report.new_table(
        "sales_long", derived=True, raw_inputs=["sales_train_evaluation.csv"]
    )
    day_columns = calendar["d"].tolist()

    # Running totals per day code, so the dashboard aggregate is produced in the
    # same single pass rather than a second scan of 59 million rows.
    totals: dict[str, int] = defaultdict(int)

    def chunks():
        # Closes over day_columns read-only, so no nonlocal is needed.
        reader = pd.read_csv(
            raw_dir(manifest, source.id) / "sales_train_evaluation.csv",
            chunksize=SERIES_CHUNK,
            low_memory=False,
        )
        for wide in reader:
            id_columns = [c for c in ID_COLUMNS if c in wide.columns]
            days_present = [c for c in day_columns if c in wide.columns]
            if not days_present:
                # Defensive: if the chunk lacks day columns we must not emit a
                # table that silently loses the sales values.
                raise RuntimeError(
                    "no day columns found in sales chunk - refusing to write a "
                    "sales table with no sales"
                )

            for column in days_present:
                totals[column] += int(wide[column].sum())

            long = wide.melt(
                id_vars=id_columns,
                value_vars=days_present,
                var_name="d",
                value_name="sales",
            )
            long["d_num"] = (
                long["d"].str.replace("d_", "", regex=False).astype("int32")
            )
            long["sales"] = long["sales"].astype("int32")
            # `d` itself is redundant once d_num exists (the calendar joins on
            # d_num), so it is dropped to save roughly 59M short strings.
            long = long.drop(columns=["d"])
            yield long

    write_processed_chunked(manifest, source.id, "sales_long", chunks(), table)

    table.note(
        f"melted the WIDE sales file ({SERIES_CHUNK:,} series per chunk, "
        f"{len(day_columns):,} day columns) into {table.rows_out:,} long rows. "
        "Wide format cannot be stored relationally or fed to a time-series model."
    )
    table.note(
        "sales are INTEGER UNITS, not currency. Any revenue figure is "
        "units x sell_price and is an ESTIMATE - it must never be presented as "
        "revenue comparable to Olist or DataCo."
    )
    table.note(
        "the date for each row comes from joining calendar on d_num; the calendar "
        "table is the single source of truth for the day -> date mapping"
    )
    table.note(
        "rows with sales = 0 are KEPT. A zero-sales day is real information "
        "(the item was stocked but unsold) and is essential for forecasting; "
        "dropping zeros would inflate every average and destroy the series shape."
    )

    # --- Daily aggregate across every series --------------------------
    daily_table = report.new_table(
        "sales_daily_total",
        derived=True,
        raw_inputs=["sales_train_evaluation.csv", "calendar.csv"],
    )
    daily = pd.DataFrame(
        {
            "d_num": [int(code.replace("d_", "")) for code in totals],
            "total_units": list(totals.values()),
        }
    ).astype({"d_num": "int32", "total_units": "int64"})
    daily = daily.merge(
        calendar[["d_num", "date", "weekday", "month", "year"]], on="d_num", how="left"
    ).sort_values("d_num")
    daily_table.rows_in = len(calendar)
    daily_table.note(
        "total_units = SUM of sales across all 30,490 series for that day; "
        "derived during the same pass as the melt (single scan of the source file)"
    )
    daily_table.note(
        f"{int(daily['date'].isna().sum()):,} day(s) could not be matched to a "
        "calendar date"
    )
    write_processed(manifest, source.id, "sales_daily_total", daily, daily_table)
