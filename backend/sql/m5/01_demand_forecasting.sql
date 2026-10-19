-- =============================================================================
-- M5 - Walmart Global Selling Competition (30,490 series x 1,941 days)
-- Domain: DEMAND FORECASTING. Daily unit sales for items in ten stores.
--
-- TEN stores, not three. The version of M5 most write-ups describe has three
-- (CA_1, TX_1, WI_1); this dataset has all ten - CA_1..CA_4, TX_1..TX_3,
-- WI_1..WI_3 - each carrying exactly 3,049 series, which is where the 30,490
-- comes from. Writing "three" here because it is the number most people expect
-- would be exactly the kind of remembered-not-measured error this project is
-- built to avoid.
--
-- Reads ONLY from the m5 schema. The M5 series ids look like ordinary retail
-- item ids ("HOBBIES_1_001_CA_1_evaluation") but they belong to this Kaggle
-- competition and to nobody else. They are NOT olist product_ids and NOT
-- dataco product_card_ids.
--
-- sales_long is the melted form of the original wide train.csv: one row per
-- (series, day) with `sales` in UNITS, not currency. `id` includes the horizon
-- suffix ("_evaluation", "_validation", "_train"), so a series is not just an
-- item: the same item appears once per horizon.
-- =============================================================================


-- [query: demand_scale_by_hierarchy]
-- How big is each part of the hierarchy? M5 is famously small on the shelf and
-- huge on volume, so item counts and unit counts are reported together.
SELECT
    dept_id,
    cat_id,
    count(DISTINCT id)                                        AS series,
    count(DISTINCT item_id)                                   AS items,
    count(DISTINCT store_id)                                  AS stores,
    round(sum(sales), 0)                                      AS units,
    round(avg(sales), 2)                                      AS avg_units_per_series_day,
    round(100.0 * sum(sales) / sum(sum(sales)) OVER (), 2)   AS pct_of_all_units
FROM m5.sales_long
GROUP BY 1, 2
ORDER BY units DESC;


-- [query: store_comparison]
-- All ten stores carry exactly the same 3,049 series, which makes this a clean
-- like-for-like comparison. CA_3 sells more than double CA_4, so "Walmart demand"
-- is meaningless without naming the store.
SELECT
    store_id,
    count(DISTINCT id)                                        AS series,
    round(sum(sales), 0)                                      AS total_units,
    round(100.0 * sum(sales) / sum(sum(sales)) OVER (), 2)   AS pct_of_units,
    round(avg(sales), 3)                                      AS avg_units_per_series_day,
    min(date)                                                 AS first_date,
    max(date)                                                 AS last_date
FROM m5.sales_long
JOIN m5.calendar USING (d_num)
GROUP BY 1
ORDER BY total_units DESC;


-- [query: weekday_seasonality]
-- Retail demand is weekly before it is anything else. This is the weekday
-- seasonality a forecaster has to model, measured on the full daily total rather
-- than on a sample. M5 numbers weekdays from 1 = Saturday.
SELECT
    c.wday,
    c.weekday,
    count(*)                                                  AS days,
    round(avg(t.total_units), 0)                              AS avg_units_per_day,
    min(t.total_units)                                        AS min_units,
    max(t.total_units)                                        AS max_units,
    round(100.0 * sum(t.total_units) / sum(sum(t.total_units)) OVER (), 2)
                                                           AS pct_of_all_units
FROM m5.sales_daily_total t
JOIN m5.calendar c USING (d_num)
GROUP BY 1, 2
ORDER BY 1;


-- [query: yearly_trend]
-- How did demand move across the 5.4-year window? Any forecast has to start
-- from the level and direction actually observed, not an assumed trend.
SELECT
    c.year,
    count(*)                                                  AS days,
    round(sum(t.total_units), 0)                              AS total_units,
    round(avg(t.total_units), 0)                              AS avg_units_per_day,
    round(avg(t.total_units) FILTER (WHERE c.wday = 6), 0)   AS avg_saturday_units,
    round(avg(t.total_units) FILTER (WHERE c.wday = 7), 0)   AS avg_sunday_units
FROM m5.sales_daily_total t
JOIN m5.calendar c USING (d_num)
GROUP BY 1
ORDER BY 1;


-- [query: event_day_impact]
-- M5's calendar flags holidays (Super Bowl, Christmas, Black Friday). This
-- compares flagged days with ordinary ones, which is the first thing a
-- forecaster needs to know about its own calendar feature.
SELECT
    CASE
        WHEN c.event_name_1 IS NULL AND c.event_name_2 IS NULL THEN 'no event flagged'
        WHEN c.event_name_1 IS NOT NULL                       THEN 'one event'
        ELSE                                                        'two events'
    END                                                       AS event_count,
    count(*)                                                  AS days,
    round(avg(t.total_units), 0)                              AS avg_units_per_day,
    round(avg(t.total_units) / (
            SELECT avg(total_units) FROM m5.sales_daily_total), 2)
                                                           AS vs_average_day
FROM m5.sales_daily_total t
JOIN m5.calendar c USING (d_num)
GROUP BY 1
ORDER BY 1;


-- [query: intermittency]
-- The single most important fact for choosing a forecasting method. M5 series
-- are famously intermittent: most series sell zero on most days, so a mean
-- forecast and a naive "same as yesterday" behave very differently here.
SELECT
    count(*)                                                  AS series_days,
    count(*) FILTER (WHERE sales = 0)                         AS zero_sales_days,
    round(100.0 * count(*) FILTER (WHERE sales = 0) / count(*), 2)
                                                           AS pct_zero_sales_days,
    round(avg(sales), 3)                                      AS avg_units,
    count(*) FILTER (WHERE sales > 0)                         AS nonzero_sales_days,
    round(avg(sales) FILTER (WHERE sales > 0), 3)             AS avg_units_on_selling_days,
    max(sales)                                                AS max_units_one_day
FROM m5.sales_long;


-- [query: intermittency_by_series]
-- Intermittency is not uniform across series, and an aggregate percentage hides
-- that. This buckets each series by the share of days it sold nothing.
WITH per_series AS (
    SELECT
        id,
        count(*)                                   AS days,
        count(*) FILTER (WHERE sales = 0)          AS zero_days,
        sum(sales)                                 AS units
    FROM m5.sales_long
    GROUP BY 1
)
SELECT
    CASE
        WHEN 100.0 * zero_days / days >= 90 THEN '1. silent 90%+ of days'
        WHEN 100.0 * zero_days / days >= 70 THEN '2. silent 70-89% of days'
        WHEN 100.0 * zero_days / days >= 40 THEN '3. silent 40-69% of days'
        WHEN 100.0 * zero_days / days >  0  THEN '4. sells something most days'
        ELSE                                      '5. never zero'
    END                                           AS intermittency_band,
    count(*)                                      AS series,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_of_series,
    round(sum(units), 0)                          AS total_units,
    round(100.0 * sum(units) / sum(sum(units)) OVER (), 2) AS pct_of_all_units
FROM per_series
GROUP BY 1
ORDER BY 1;


-- [query: price_and_demand]
-- Does a higher sell price go with lower demand? Prices live in m5.sell_prices
-- per store, item and wm_yr_wk, so the daily facts are first collapsed to the
-- same weekly grain before joining - joining 59 million daily rows to the price
-- table on a daily key that the price table does not have is both wrong and slow.
WITH weekly AS (
    SELECT
        s.store_id,
        s.item_id,
        c.wm_yr_wk,
        sum(s.sales) AS units
    FROM m5.sales_long s
    JOIN m5.calendar c USING (d_num)
    GROUP BY 1, 2, 3
),
priced AS (
    SELECT
        w.store_id,
        w.item_id,
        w.wm_yr_wk,
        w.units,
        p.sell_price
    FROM weekly w
    JOIN m5.sell_prices p
      ON p.store_id = w.store_id
     AND p.item_id  = w.item_id
     AND p.wm_yr_wk = w.wm_yr_wk
)
SELECT
    CASE
        WHEN sell_price <= 0        THEN '1. non-positive price'
        WHEN sell_price <  1        THEN '2. under 1'
        WHEN sell_price <  3        THEN '3. 1 to 3'
        WHEN sell_price <  5        THEN '4. 3 to 5'
        WHEN sell_price <  10       THEN '5. 5 to 10'
        ELSE                             '6. 10 and over'
    END                                               AS price_band,
    count(*)                                          AS item_store_weeks,
    round(avg(sell_price), 2)                         AS avg_price,
    round(avg(units), 2)                              AS avg_weekly_units,
    round(100.0 * count(*) FILTER (WHERE units = 0) / count(*), 2)
                                                      AS pct_weeks_with_no_sales
FROM priced
GROUP BY 1
ORDER BY 1;