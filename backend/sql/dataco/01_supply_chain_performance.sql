-- =============================================================================
-- DATACO - Supply Chain Dataset (118 products, 180,519 order lines)
-- Domain: SUPPLY CHAIN. Orders, shipping performance, margin, discounting.
--
-- Reads ONLY from the dataco schema. DataCo is a different company from Olist
-- and from Dunn & Bradstreet: its order_id 1 is not Olist's order_id 1, and its
-- customer 123 is neither Olist's customer 123 nor dunnhumby's household 123.
-- There is deliberately nothing in this file that joins another source.
--
-- Two money columns exist on orders: order_value (goods + freight as charged)
-- and order_sales (goods only, net of returns). Both are real; the queries below
-- say which one they mean rather than picking silently.
-- =============================================================================


-- [query: order_flow_by_month]
-- How did order volume and value move month by month? DataCo is a 2-year
-- window, so this shows both the level and whether the business was growing.
SELECT
    to_char(date_trunc('month', order_date), 'YYYY-MM')     AS month,
    count(*)                                                 AS orders,
    count(DISTINCT customer_id)                              AS customers,
    round(sum(order_value), 2)                               AS order_value,
    round(sum(order_sales), 2)                               AS order_sales,
    round(sum(order_profit), 2)                              AS profit,
    round(100.0 * sum(order_profit) / sum(order_sales), 2)   AS margin_pct,
    round(avg(total_quantity), 2)                            AS avg_units_per_order
FROM dataco.orders
GROUP BY 1
ORDER BY 1;


-- [query: order_status_mix]
-- Not every order completed, and the ones that did not are where the money
-- quietly disappears. Reports value by terminal status.
SELECT
    order_status,
    delivery_status,
    count(*)                                                 AS orders,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)      AS pct_of_orders,
    round(sum(order_sales), 2)                               AS sales,
    round(sum(order_profit), 2)                              AS profit,
    round(100.0 * sum(order_profit) / sum(order_sales), 2)  AS margin_pct
FROM dataco.orders
GROUP BY 1, 2
ORDER BY orders DESC;


-- [query: shipping_performance]
-- DataCo publishes a promise (days_for_shipment_scheduled) and an outcome
-- (days_for_shipping_real). This measures the gap, by shipping mode.
SELECT
    shipping_mode,
    count(*)                                                 AS orders,
    round(100.0 * count(*) FILTER (WHERE is_late) / count(*), 2) AS pct_late,
    round(avg(days_for_shipment_scheduled), 2)              AS avg_days_promised,
    round(avg(days_for_shipping_real), 2)                   AS avg_days_actual,
    round(avg(shipping_delay_days), 2)                      AS avg_delay_days,
    round(avg(lead_time_days)::numeric, 2)                  AS avg_lead_time_days,
    max(shipping_delay_days)                                 AS worst_delay_days
FROM dataco.orders
GROUP BY 1
ORDER BY orders DESC;


-- [query: margin_by_department]
-- The central profitability question. DataCo is widely reported to be close to
-- break-even overall; this shows whether that is uniform or hides a mix shift.
SELECT
    p.department_name                                        AS department,
    count(DISTINCT p.product_card_id)                        AS products,
    count(*)                                                 AS order_lines,
    round(sum(i.sales), 2)                                   AS sales,
    round(sum(i.order_profit_per_order), 2)                  AS profit,
    round(100.0 * sum(i.order_profit_per_order) / sum(i.sales), 2) AS margin_pct,
    round(sum(i.order_item_discount), 2)                     AS discount_given,
    round(100.0 * sum(i.order_item_discount) / sum(i.sales), 2) AS discount_pct
FROM dataco.order_items i
JOIN dataco.products p USING (product_card_id)
GROUP BY 1
ORDER BY sales DESC;


-- [query: worst_categories_by_margin]
-- The bottom of the margin table. A category can be high-volume and still lose
-- money on every unit, which no revenue ranking would reveal.
SELECT
    p.category_name                                          AS category,
    p.department_name                                        AS department,
    count(DISTINCT p.product_card_id)                        AS products,
    count(*)                                                 AS order_lines,
    round(sum(i.sales), 2)                                   AS sales,
    round(sum(i.order_profit_per_order), 2)                  AS profit,
    round(100.0 * sum(i.order_profit_per_order) / sum(i.sales), 2) AS margin_pct,
    count(*) FILTER (WHERE i.order_profit_per_order < 0)     AS lines_losing_money
FROM dataco.order_items i
JOIN dataco.products p USING (product_card_id)
GROUP BY 1, 2
HAVING sum(i.sales) > 0
ORDER BY margin_pct ASC
LIMIT 12;


-- [query: discount_vs_margin]
-- Does discounting actually pay? Buckets order lines by the discount they
-- carried so the relationship can be read without assuming one exists.
SELECT
    CASE
        WHEN order_item_discount_rate <= 0.00 THEN '1. no discount'
        WHEN order_item_discount_rate <  0.15 THEN '2. 1-14% off'
        WHEN order_item_discount_rate <  0.20 THEN '3. 15-19% off'
        WHEN order_item_discount_rate <  0.30 THEN '4. 20-29% off'
        WHEN order_item_discount_rate <  0.50 THEN '5. 30-49% off'
        ELSE                              '6. 50%+ off'
    END                                                      AS discount_band,
    count(*)                                                 AS order_lines,
    round(avg(order_item_discount_rate), 4)                 AS avg_discount_rate,
    round(sum(sales), 2)                                     AS sales,
    round(sum(order_profit_per_order), 2)                    AS profit,
    round(100.0 * sum(order_profit_per_order) / sum(sales), 2) AS margin_pct,
    round(avg(order_item_profit_ratio), 4)                  AS avg_profit_ratio
FROM dataco.order_items
GROUP BY 1
ORDER BY 1;


-- [query: customer_segments]
-- Consumer, Corporate and Home Office are DataCo's three segments. Segment
-- value per order and per customer is the comparison that matters.
SELECT
    c.customer_segment                                       AS segment,
    count(DISTINCT c.customer_id)                           AS customers,
    count(DISTINCT o.order_id)                               AS orders,
    round(sum(o.order_sales), 2)                             AS sales,
    round(sum(o.order_profit), 2)                            AS profit,
    round(100.0 * sum(o.order_profit) / sum(o.order_sales), 2) AS margin_pct,
    round(sum(o.order_sales) / count(DISTINCT o.order_id), 2) AS avg_order_sales,
    round(sum(o.order_sales) / count(DISTINCT c.customer_id), 2) AS sales_per_customer
FROM dataco.orders o
JOIN dataco.customers c USING (customer_id)
GROUP BY 1
ORDER BY sales DESC;


-- [query: revenue_by_market]
-- DataCo is a multinational. Market share shows how concentrated the operation
-- is geographically before any per-country drill-down.
SELECT
    market,
    count(DISTINCT order_region)                             AS regions,
    count(DISTINCT order_country)                            AS countries,
    count(*)                                                 AS orders,
    round(sum(order_sales), 2)                               AS sales,
    round(100.0 * sum(order_sales) / sum(sum(order_sales)) OVER (), 2)
                                                              AS pct_of_sales,
    round(avg(shipping_delay_days), 2)                       AS avg_delay_days,
    round(100.0 * count(*) FILTER (WHERE is_late) / count(*), 2) AS pct_late
FROM dataco.orders
GROUP BY 1
ORDER BY sales DESC;


-- [query: loss_making_orders]
-- Orders that lost money outright. If an operations review only looks at totals
-- these disappear inside an average; counting them makes them arguable.
SELECT
    CASE
        WHEN order_profit >= 0    THEN '1. profitable or break-even'
        WHEN order_profit > -100  THEN '2. small loss (0 to -100)'
        WHEN order_profit > -500  THEN '3. moderate loss (-100 to -500)'
        ELSE                           '4. large loss (below -500)'
    END                                                      AS profit_band,
    count(*)                                                 AS orders,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)      AS pct_of_orders,
    round(sum(order_sales), 2)                               AS sales,
    round(sum(order_profit), 2)                              AS profit,
    min(order_profit)                                        AS worst_single_order
FROM dataco.orders
GROUP BY 1
ORDER BY 1;