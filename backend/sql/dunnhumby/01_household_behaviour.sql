-- =============================================================================
-- DUNNHUMBY - Dunn & Bradstreet Complete Journey (2,500 households, 711 days)
-- Domain: CPG / Retail. Household panels, baskets, promotions, coupons, RFM.
--
-- Reads ONLY from the dunnhumby schema. This is a panel of 2,500 US households
-- observed shopping one retailer's banner. A household_key is a real household
-- at that one retailer and belongs to nobody else: dunnhumby household 123 is
-- not Olist customer 123 and not DataCo customer 123.
--
-- The unit is a BASKET LINE (dunnhumby.transactions), not an order: a basket can
-- hold the same product more than once. money is gross_sales_value BEFORE
-- discount and sales_value AFTER; using the wrong one overstates the business.
-- =============================================================================


-- [query: household_activity_over_time]
-- How many households actually shopped, week by week? The panel is fixed at
-- 2,500 households, so what moves is participation, not population.
SELECT
    week_no,
    count(DISTINCT household_key)                            AS households_shopping,
    count(DISTINCT basket_id)                                 AS baskets,
    count(*)                                                  AS basket_lines,
    round(avg(quantity), 2)                                   AS avg_units_per_line,
    round(sum(gross_sales_value), 2)                          AS gross_sales,
    round(sum(sales_value), 2)                                AS net_sales,
    round(sum(gross_sales_value) / NULLIF(count(DISTINCT basket_id), 0), 2)
                                                           AS avg_basket_value
FROM dunnhumby.transactions
GROUP BY 1
ORDER BY 1;


-- [query: household_participation_rate]
-- How concentrated is shopping in a few weeks? A panel dataset can look healthy
-- on totals while most households visit only a handful of times all year.
SELECT
    count(*)                                                  AS households_with_basket,
    round(avg(transactions_per_household), 2)                AS avg_transactions,
    min(transactions_per_household)                           AS min_transactions,
    max(transactions_per_household)                           AS max_transactions,
    round(avg(total_spend), 2)                                AS avg_total_spend,
    round(avg(active_days), 2)                                AS avg_active_days,
    round(avg(transactions_per_household) FILTER (WHERE household_key IN (
            SELECT household_key FROM dunnhumby.households)), 2)
                                                           AS avg_tx_households_with_demographics,
    round(avg(transactions_per_household) FILTER (WHERE household_key NOT IN (
            SELECT household_key FROM dunnhumby.households)), 2)
                                                           AS avg_tx_households_without_demographics
FROM (
    SELECT
        household_key,
        count(DISTINCT basket_id) AS transactions_per_household,
        count(DISTINCT day)      AS active_days,
        sum(sales_value)         AS total_spend
    FROM dunnhumby.transactions
    GROUP BY 1
) h;


-- [query: demographic_coverage_gap]
-- The headline data-quality finding for this source. transaction_data.csv names
-- 2,500 distinct households; hh_demographic.csv names only 801, and the file
-- is byte-identical to the copy inside the publisher's own zip, so this is the
-- publisher's coverage and not a truncated download. Any demographic split is
-- therefore a minority-sample split and has to be described as one.
SELECT
    (SELECT count(DISTINCT household_key) FROM dunnhumby.transactions)
                                                           AS households_transacting,
    (SELECT count(*) FROM dunnhumby.households)             AS households_with_demographics,
    (SELECT count(DISTINCT t.household_key) FROM dunnhumby.transactions t
       WHERE NOT EXISTS (SELECT 1 FROM dunnhumby.households h
                         WHERE h.household_key = t.household_key))
                                                           AS households_missing_demographics,
    round(100.0 * (SELECT count(*) FROM dunnhumby.households)
          / (SELECT count(DISTINCT household_key) FROM dunnhumby.transactions), 2)
                                                           AS pct_with_demographics,
    round(100.0 * (SELECT count(*) FROM dunnhumby.households)
          / (SELECT count(*) FROM dunnhumby.household_rfm), 2)
                                                           AS pct_of_rfm_households_described;


-- [query: sales_mix_by_department]
-- Which parts of the store carry the business. Department is the top level of
-- the dunnhumby product hierarchy (manufacturer > brand > category > commodity).
SELECT
    p.department,
    count(DISTINCT p.product_id)                              AS products,
    count(*)                                                  AS basket_lines,
    round(sum(t.quantity), 0)                                 AS units,
    round(sum(t.gross_sales_value), 2)                        AS gross_sales,
    round(sum(t.sales_value), 2)                              AS net_sales,
    round(100.0 * sum(t.sales_value) / sum(sum(t.sales_value)) OVER (), 2)
                                                           AS pct_of_net_sales,
    round(sum(t.total_discount), 2)                           AS discount_given,
    round(100.0 * sum(t.total_discount) / sum(t.gross_sales_value), 2)
                                                           AS discount_rate_pct
FROM dunnhumby.transactions t
JOIN dunnhumby.products p USING (product_id)
GROUP BY 1
ORDER BY net_sales DESC;


-- [query: discount_composition]
-- Retailer discounts versus manufacturer coupons. Splitting them shows who is
-- actually funding the promotion, which a single discount total cannot.
--
-- SIGN CONVENTION, measured not assumed. This source stores signs two different
-- ways and both are real:
--   * retail_disc / coupon_disc keep the RAW sign: a discount is NEGATIVE.
--     retail_disc is negative on 1,303,026 of 2,595,732 lines (min -180,
--     max +3.99); coupon_disc is negative on 36,422 lines (min -55.93, max 0).
--   * total_discount was normalised during ETL to a POSITIVE magnitude:
--     1,319,391 lines are > 0 (max 180) and only 10 lines are < 0.
-- Bucketing therefore has to test retail_disc/coupon_disc for "< 0", not
-- total_discount for "> 0", or every line lands in "no discount".
SELECT
    CASE
        WHEN retail_disc < 0 AND coupon_disc < 0 THEN '1. retailer and coupon together'
        WHEN retail_disc < 0                     THEN '2. retailer discount only'
        WHEN coupon_disc < 0                     THEN '3. manufacturer coupon only'
        ELSE                                         '4. no discount at all'
    END                                                       AS discount_type,
    count(*)                                                 AS basket_lines,
    round(sum(gross_sales_value), 2)                         AS gross_sales,
    round(abs(sum(total_discount)), 2)                       AS discount_given,
    round(100.0 * abs(sum(total_discount)) / sum(gross_sales_value), 2) AS effective_discount_pct,
    round(abs(sum(retail_disc))::numeric, 2)                 AS retailer_funded,
    round(abs(sum(coupon_disc))::numeric, 2)                 AS manufacturer_funded,
    round(100.0 * abs(sum(coupon_disc)::numeric) / NULLIF(abs(sum(total_discount)), 0), 2)
                                                           AS pct_funded_by_manufacturer
FROM dunnhumby.transactions
GROUP BY 1
ORDER BY 1;


-- [query: campaign_performance]
-- Dunn & Bradstreet ran 30 named campaigns. The control group matters: comparing
-- contacted households against the panel AVERAGE is not a control at all, because
-- campaigns were aimed at frequent shoppers, so the contacted group is pre-loaded
-- with high spenders. This compares each campaign's contacted households against
-- the households that were NEVER contacted by anything.
WITH household_spend AS (
    SELECT household_key, sum(sales_value) AS spend
    FROM dunnhumby.transactions
    GROUP BY 1
),
ever_contacted AS (
    SELECT DISTINCT household_key FROM dunnhumby.campaign_contacts
),
control AS (
    SELECT avg(spend) AS avg_control_spend, count(*) AS control_households
    FROM household_spend h
    WHERE NOT EXISTS (SELECT 1 FROM ever_contacted e WHERE e.household_key = h.household_key)
)
SELECT
    c.campaign,
    c.description                                            AS campaign_name,
    (c.end_day - c.start_day)                                AS duration_days,
    count(DISTINCT cc.household_key)                         AS households_contacted,
    round(avg(h.spend), 2)                                   AS avg_spend_contacted,
    (SELECT round(avg_control_spend, 2) FROM control)        AS avg_spend_never_contacted,
    round(avg(h.spend) - (SELECT avg_control_spend FROM control), 2)
                                                           AS lift_vs_never_contacted
FROM dunnhumby.campaigns c
JOIN dunnhumby.campaign_contacts cc ON cc.campaign = c.campaign
JOIN household_spend h ON h.household_key = cc.household_key
GROUP BY 1, 2, 3
ORDER BY households_contacted DESC;


-- [query: coupon_redemption]
-- Coupons were sent far more often than they were redeemed. The gap between
-- coupons issued and redemptions is the redemption rate that actually matters.
--
-- coupons has 119,384 rows over only 1,135 distinct coupon_upc values, so
-- joining it to the 2,318 redemption rows on coupon_upc alone multiplies the
-- two together and reports millions of redemptions that never happened. Each
-- side is therefore aggregated to coupon_upc BEFORE the join.
WITH issued AS (
    SELECT
        coupon_upc,
        count(*)                       AS product_campaign_rows,
        count(DISTINCT campaign)       AS campaigns,
        count(DISTINCT product_id)     AS products
    FROM dunnhumby.coupons
    GROUP BY 1
),
redeemed AS (
    SELECT
        coupon_upc,
        count(*)                       AS redemption_rows,
        count(DISTINCT household_key)  AS households_redeeming
    FROM dunnhumby.coupon_redemptions
    GROUP BY 1
)
SELECT
    count(*)                                                  AS coupons_defined,
    count(r.coupon_upc)                                       AS coupons_ever_redeemed,
    round(100.0 * count(r.coupon_upc) / count(*), 2)          AS pct_of_coupons_redeemed,
    COALESCE(sum(r.redemption_rows), 0)                       AS redemption_rows,
    COALESCE(sum(r.households_redeeming), 0)                  AS households_redeeming,
    sum(issued.product_campaign_rows)                         AS product_campaign_rows_issued
FROM issued
LEFT JOIN redeemed r USING (coupon_upc);


-- [query: promo_exposure_coverage]
-- What share of product-store-weeks were on display or in a mailer. display and
-- mailer are text codes in the source ('0','6','A','D'), not numbers, so they
-- are reported as counts per code rather than summed.
SELECT
    display,
    count(*)                                                 AS product_store_weeks,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)       AS pct_of_exposure_rows
FROM dunnhumby.promo_exposure
GROUP BY 1
ORDER BY 2 DESC;


-- [query: exposure_vs_sales]
-- Do products that appear on display actually sell more?
--
-- Two traps here, both measured rather than guessed. First, promo_exposure is a
-- dense product x store x week grid: 36.8 million rows, and 98.9% of those
-- product-store-weeks have NO matching transaction at all. Comparing averages
-- across the whole grid compares mostly zeros, which says nothing about display.
-- Second, a display comparison over the whole grid also mixes in the fact that
-- retailers promote things they already expect to sell, so any gap is an
-- association and not a causal lift. The query therefore restricts to the
-- product-store-weeks that actually sold, and says so.
WITH sold AS (
    SELECT product_id, store_id, week_no, sum(quantity) AS units
    FROM dunnhumby.transactions
    GROUP BY 1, 2, 3
)
SELECT
    CASE WHEN p.display = '0' THEN 'not on display' ELSE 'on display' END AS display_status,
    count(*)                                                 AS product_store_weeks_with_sales,
    round(avg(s.units), 2)                                   AS avg_weekly_units,
    round(sum(s.units), 0)                                   AS total_units,
    round(100.0 * count(*) / (SELECT count(*) FROM dunnhumby.promo_exposure), 4)
                                                           AS pct_of_all_exposure_rows
FROM dunnhumby.promo_exposure p
JOIN sold s USING (product_id, store_id, week_no)
GROUP BY 1
ORDER BY 1;


-- [query: exposure_grid_density]
-- Why the query above has to restrict. Most of the 36.8 million row exposure
-- grid corresponds to no sale in that product, store and week at all.
SELECT
    count(*)                                                 AS exposure_rows,
    count(*) FILTER (WHERE s.product_id IS NOT NULL)         AS rows_matching_a_sale,
    count(*) FILTER (WHERE s.product_id IS NULL)             AS rows_with_no_sale,
    round(100.0 * count(*) FILTER (WHERE s.product_id IS NULL) / count(*), 2)
                                                           AS pct_of_grid_with_no_sale
FROM dunnhumby.promo_exposure p
LEFT JOIN (
    SELECT DISTINCT product_id, store_id, week_no FROM dunnhumby.transactions
) s USING (product_id, store_id, week_no);


-- [query: rfm_segment_value]
-- The panel's RFM segmentation. Value per household by segment shows which
-- segment actually carries the panel rather than which is largest.
SELECT
    classification_3                                         AS segment,
    count(*)                                                  AS households,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)       AS pct_of_households,
    round(avg(recency_days), 2)                              AS avg_recency_days,
    round(avg(frequency), 2)                                 AS avg_frequency,
    round(avg(monetary), 2)                                  AS avg_monetary,
    round(sum(monetary), 2)                                  AS total_monetary,
    round(100.0 * sum(monetary) / sum(sum(monetary)) OVER (), 2) AS pct_of_panel_value
FROM dunnhumby.household_rfm
GROUP BY 1
ORDER BY total_monetary DESC;