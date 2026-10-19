-- =============================================================================
-- OLIST - Brazilian E-Commerce Marketplace
-- Domain: ECOMMERCE. Order lifecycle, sellers, payments, delivery, reviews.
--
-- Every query here reads ONLY from the olist schema. Olist is one company with
-- 99,441 real orders; its customer 123 has nothing to do with DataCo's customer
-- 123 or dunnhumby's household 123, so there is deliberately no query in this
-- folder that touches another source.
--
-- Revenue is defined as olist.order_payments_agg.payment_total, which the load
-- report reconciles exactly against the raw olist.payments rows (16,008,872.12).
-- =============================================================================


-- [query: monthly_revenue_trend]
-- How did order volume and revenue move month by month across the whole period?
-- Olist is widely described as a high-growth marketplace; the shape of this
-- series is the first thing any dashboard should show.
-- count(DISTINCT c.customer_unique_id), NOT customer_id: orders.customer_id is
-- the primary key of olist.customers and is unique per order, so counting it
-- here would report the same number of "customers" as orders in every month and
-- make it look as though nobody ever bought twice.
SELECT
    to_char(date_trunc('month', o.purchase_date), 'YYYY-MM') AS month,
    count(*)                                              AS orders,
    count(DISTINCT c.customer_unique_id)                  AS customers,
    round(sum(a.payment_total), 2)                        AS revenue,
    round(avg(a.payment_total), 2)                        AS avg_order_value,
    round(sum(a.payment_count), 0)                        AS payment_rows
FROM olist.orders o
JOIN olist.order_payments_agg a USING (order_id)
JOIN olist.customers c USING (customer_id)
WHERE a.payment_total > 0
GROUP BY 1
ORDER BY 1;


-- [query: customer_repeat_behaviour]
-- Does anyone come back?
--
-- GROUPING IS THE WHOLE QUESTION HERE. olist.orders.customer_id is the primary
-- key of olist.customers, so it is unique BY CONSTRUCTION - one row per order.
-- Counting distinct customer_id therefore always returns the order count and
-- makes repeat purchasing impossible to see; an earlier draft of this query
-- returned exactly 1.000 orders per customer for that reason alone. The person is
-- identified by customer_unique_id, which repeats across orders.
WITH per_customer AS (
    SELECT
        c.customer_unique_id,
        count(*)                              AS orders,
        count(DISTINCT date_trunc('month', o.purchase_date)) AS active_months
    FROM olist.orders o
    JOIN olist.customers c USING (customer_id)
    GROUP BY 1
)
SELECT
    (SELECT count(*) FROM olist.orders)                     AS orders,
    count(*)                                                AS distinct_people,
    round((SELECT count(*) FROM olist.orders)::numeric / count(*), 3)
                                                          AS orders_per_person,
    count(*) FILTER (WHERE orders = 1)                     AS people_ordering_once,
    round(100.0 * count(*) FILTER (WHERE orders = 1) / count(*), 2)
                                                          AS pct_people_ordering_once,
    count(*) FILTER (WHERE orders >= 2)                    AS people_ordering_twice_plus,
    round(100.0 * count(*) FILTER (WHERE orders >= 2) / count(*), 2)
                                                          AS pct_people_returning,
    max(orders)                                            AS most_orders_by_one_person
FROM per_customer;


-- [query: review_id_forensics]
-- WHY review_id IS NOT A PRIMARY KEY HERE
--
-- review_id repeats: 99,224 rows carry only 98,410 distinct values. The obvious
-- reading is that a customer re-submitted the same review, but the evidence says
-- something else entirely. This query lays out the test, one row per duplicated
-- review_id.
--
-- The discriminator is the timestamp. A genuine re-submission is a second event
-- and would carry its own creation and answer times. If every duplicated row
-- shares both timestamps exactly, there was only ever ONE review, and the
-- dataset is repeating it across several of that customer's orders.
--
-- The second column, orders_placed_after_the_review, is the clincher. A customer
-- cannot review an order they had not yet placed, so any row where it happens
-- proves the association was manufactured rather than reported.
WITH duplicated AS (
    SELECT review_id, count(*) AS rows_in_group
    FROM olist.reviews
    GROUP BY 1
    HAVING count(*) > 1
),
per_review AS (
    SELECT
        r.review_id,
        max(d.rows_in_group)                                    AS rows_in_group,
        count(DISTINCT r.order_id)                               AS distinct_orders,
        count(DISTINCT c.customer_unique_id)                     AS distinct_people,
        count(DISTINCT r.review_score)                           AS distinct_scores,
        -- coalesce matters: most reviews carry no title at all, and
        -- count(DISTINCT review_comment_title) ignores NULLs, so it would
        -- report 0 distinct titles for a group and make "all the same" look
        -- false when it is merely absent.
        count(DISTINCT coalesce(r.review_comment_title, '<none>'))   AS distinct_titles,
        count(DISTINCT coalesce(r.review_comment_message, '<none>')) AS distinct_messages,
        count(DISTINCT r.review_creation_date)                   AS distinct_creation_dates,
        count(DISTINCT r.review_answer_timestamp)                AS distinct_answer_dates,
        count(*) FILTER (WHERE o.purchase_date > r.review_creation_date)
                                                               AS orders_placed_after_the_review,
        count(DISTINCT o.customer_id)                            AS customer_rows_behind_the_orders
    FROM olist.reviews r
    JOIN duplicated d ON d.review_id = r.review_id
    JOIN olist.orders o    ON o.order_id = r.order_id
    JOIN olist.customers c ON c.customer_id = o.customer_id
    GROUP BY 1
)
SELECT
    rows_in_group,
    count(*)                                                     AS review_ids,
    count(*) FILTER (WHERE distinct_people = 1)                  AS all_one_person,
    count(*) FILTER (WHERE distinct_scores = 1)                  AS all_same_score,
    count(*) FILTER (WHERE distinct_titles = 1 AND distinct_messages = 1)
                                                                 AS all_same_text,
    count(*) FILTER (WHERE distinct_creation_dates = 1 AND distinct_answer_dates = 1)
                                                                 AS all_same_both_timestamps,
    count(*) FILTER (WHERE orders_placed_after_the_review > 0)   AS impossible_chronology,
    sum(distinct_orders)                                         AS order_links,
    sum(orders_placed_after_the_review)                          AS impossible_order_links
FROM per_review
-- GROUPING SETS adds an "all" row so the totals are measured rather than
-- summed by hand in the write-up.
GROUP BY GROUPING SETS ((rows_in_group), ())
ORDER BY rows_in_group NULLS LAST;


-- [query: review_grain_candidates]
-- Which key actually holds? Tests the three plausible grains rather than
-- asserting one. rows - distinct pairs is the surplus; zero means the key is a
-- valid primary key.
SELECT 'review_id alone'   AS candidate_key, '1 row per review'        AS claimed_grain,
       count(*) - count(DISTINCT review_id) AS surplus_rows,
       count(*) FILTER (WHERE review_id IS NULL) AS null_rows
FROM olist.reviews
UNION ALL
SELECT 'review_id + order_id', '1 row per review-per-order link',
       count(*) - count(DISTINCT (review_id, order_id)),
       count(*) FILTER (WHERE review_id IS NULL OR order_id IS NULL)
FROM olist.reviews
UNION ALL
SELECT 'order_id + review_creation_date', '1 row per order, repeated reviews impossible',
       count(*) - count(DISTINCT (order_id, review_creation_date)),
       count(*) FILTER (WHERE order_id IS NULL OR review_creation_date IS NULL)
FROM olist.reviews
ORDER BY surplus_rows;


-- [query: order_status_funnel]
-- What actually happened to every order? Revenue is only real once an order
-- reaches a terminal state, so this separates money that was collected from
-- money that was attempted.
SELECT
    o.order_status,
    count(*)                                              AS orders,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)     AS pct_of_orders,
    round(sum(a.payment_total), 2)                        AS payment_attempted,
    round(100.0 * sum(a.payment_total) / sum(sum(a.payment_total)) OVER (), 2)
                                                          AS pct_of_payment_attempted
FROM olist.orders o
LEFT JOIN olist.order_payments_agg a USING (order_id)
GROUP BY 1
ORDER BY payment_attempted DESC NULLS LAST;


-- [query: delivery_performance]
-- How reliable are Olist's delivery promises? Compares what was promised against
-- what actually happened, and separates "late" from "so late it matters".
-- delay_days <= 0 means delivered on or before the estimate.
SELECT
    o.is_delivered,
    count(*)                                              AS orders,
    count(*) FILTER (WHERE o.delay_days <= 0)              AS on_or_before_estimate,
    round(100.0 * count(*) FILTER (WHERE o.delay_days <= 0) / count(*), 2)
                                                          AS pct_on_time,
    round(avg(o.delay_days)::numeric, 2)                  AS avg_delay_days,
    round(avg(o.delivery_days)::numeric, 2)               AS avg_delivery_days,
    round(avg(o.estimated_days)::numeric, 2)              AS avg_promised_days,
    round(max(o.delay_days)::numeric, 0)                  AS worst_delay_days
FROM olist.orders o
WHERE o.is_delivered
GROUP BY 1
ORDER BY 1 DESC;


-- [query: delay_distribution]
-- On-time deliveries are an average; the tail is what generates complaints.
-- This buckets the delivered orders by how far past the estimate they landed.
SELECT
    CASE
        WHEN delay_days <= 0    THEN '1. on or before estimate'
        WHEN delay_days <= 1    THEN '2. up to 1 day late'
        WHEN delay_days <= 3    THEN '3. 2-3 days late'
        WHEN delay_days <= 7    THEN '4. 4-7 days late'
        WHEN delay_days <= 14   THEN '5. 8-14 days late'
        ELSE                         '6. more than 14 days late'
    END                                                     AS delay_band,
    count(*)                                              AS orders,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)     AS pct_of_delivered
FROM olist.orders
WHERE is_delivered AND delay_days IS NOT NULL
GROUP BY 1
ORDER BY 1;


-- [query: review_coverage_and_score]
-- Only customers who were happy enough to write can be measured at all, so this
-- reports review COVERAGE before it reports score. A 4.5 average over 6% of
-- orders is a very different claim from a 4.5 average over 90%.
-- DISTINCT is required, not decorative: olist.reviews holds more review rows
-- than there are reviewed orders, so a plain count(*) over this join counts a
-- multi-review order several times and inflates the coverage rate.
SELECT
    count(DISTINCT o.order_id)                            AS orders_total,
    count(DISTINCT o.order_id) FILTER (WHERE r.order_id IS NOT NULL)
                                                          AS orders_with_review,
    count(DISTINCT o.order_id) FILTER (WHERE r.order_id IS NULL)
                                                          AS orders_without_review,
    round(100.0 * count(DISTINCT o.order_id) FILTER (WHERE r.order_id IS NOT NULL)
          / count(DISTINCT o.order_id), 2)                AS pct_reviewed,
    round(avg(r.review_score), 2)                         AS avg_score_across_reviews,
    min(r.review_score)                                   AS min_score,
    max(r.review_score)                                   AS max_score,
    count(DISTINCT r.review_id)                           AS distinct_review_ids,
    count(*) FILTER (WHERE r.review_id IS NOT NULL)       AS review_rows
FROM olist.orders o
LEFT JOIN olist.reviews r USING (order_id);


-- [query: review_score_distribution]
-- Score distribution alongside the comment volume behind each score. A 1-star
-- with no comment is a different event from a 1-star with a paragraph.
SELECT
    r.review_score,
    count(*)                                              AS reviews,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)     AS pct_of_reviews,
    round(100.0 * count(*) FILTER (WHERE r.review_comment_message IS NOT NULL)
          / count(*), 2)                                  AS pct_with_comment,
    round(avg(length(r.review_comment_message)), 0)       AS avg_comment_chars
FROM olist.reviews r
GROUP BY 1
ORDER BY 1;


-- [query: seller_concentration]
-- How dependent is the marketplace on its biggest sellers? This measures gross
-- merchandise value as the SUM OF order_items.item_total, NOT the order's
-- payment_total: an order with six items has six rows, so joining items to
-- payments and summing the order total would count that order's revenue six
-- times. Each item belongs to exactly one seller, so item-level GMV cannot
-- double-count.
WITH seller_gmv AS (
    SELECT
        i.seller_id,
        count(DISTINCT i.order_id) AS orders,
        sum(i.item_total)          AS gmv
    FROM olist.order_items i
    GROUP BY 1
),
ranked AS (
    SELECT
        row_number() OVER (ORDER BY gmv DESC) AS seller_rank,
        orders,
        gmv
    FROM seller_gmv
),
thresholds(n) AS (VALUES (1), (10), (50), (100), (500), (1000), (3095))
SELECT
    t.n                                                   AS top_n_sellers,
    round(100.0 * t.n / (SELECT count(*) FROM seller_gmv), 2) AS pct_of_sellers,
    count(*)                                              AS sellers_cumulative,
    -- An order with items from three sellers is counted once per seller, so this
    -- is (seller, order) pairs, not distinct orders.
    sum(r.orders)                                         AS seller_order_pairs,
    round(sum(r.gmv), 2)                                  AS gmv_cumulative,
    round(100.0 * sum(r.gmv) / (SELECT sum(gmv) FROM seller_gmv), 2)
                                                          AS pct_of_gmv
FROM thresholds t
JOIN ranked r ON r.seller_rank <= t.n
GROUP BY 1, 2
ORDER BY 1;


-- [query: geography_revenue_concentration]
-- Where is the demand? Uses the order's own destination, not the seller's, and
-- reports how much revenue sits in states with no geolocation mapping at all.
SELECT
    COALESCE(c.customer_state, '(no state)')              AS destination_state,
    count(*)                                              AS orders,
    round(sum(a.payment_total), 2)                        AS revenue,
    round(100.0 * sum(a.payment_total) / sum(sum(a.payment_total)) OVER (), 2)
                                                          AS pct_of_revenue
FROM olist.orders o
JOIN olist.order_payments_agg a USING (order_id)
LEFT JOIN olist.customers c USING (customer_id)
GROUP BY 1
ORDER BY revenue DESC
LIMIT 12;


-- [query: payment_behaviour]
-- How do customers actually pay? Installments are the interesting column: a
-- single R$100 payment in 1 instalment and in 10 are different products.
SELECT
    p.payment_type,
    count(*)                                              AS payment_rows,
    round(sum(p.payment_value), 2)                        AS value,
    round(100.0 * sum(p.payment_value) / sum(sum(p.payment_value)) OVER (), 2)
                                                          AS pct_of_all_value,
    round(avg(p.payment_installments), 2)                 AS avg_installments,
    max(p.payment_installments)                           AS max_installments,
    count(*) FILTER (WHERE p.payment_installments > 1)    AS rows_on_installments,
    round(100.0 * count(*) FILTER (WHERE p.payment_installments > 1)
          / count(*), 2)                                  AS pct_rows_on_installments,
    round(sum(p.payment_value) FILTER (WHERE p.payment_installments > 1), 2)
                                                          AS value_on_installments,
    round(100.0 * sum(p.payment_value) FILTER (WHERE p.payment_installments > 1)
          / sum(p.payment_value), 2)                      AS pct_of_type_value_on_installments
FROM olist.payments p
GROUP BY 1
ORDER BY value DESC;


-- [query: multi_payment_orders]
-- Some orders are settled by several payments. This is why order_payments_agg
-- has fewer rows than payments, and why revenue must not be counted per row.
SELECT
    payment_count                                         AS payments_settling_order,
    count(*)                                              AS orders,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)     AS pct_of_orders,
    round(sum(payment_total), 2)                          AS revenue,
    round(avg(payment_total), 2)                          AS avg_order_value
FROM olist.order_payments_agg
GROUP BY 1
ORDER BY 1;