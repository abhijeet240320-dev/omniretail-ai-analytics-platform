# Data Lineage

> **The rule this document exists to satisfy:** for every number the
> platform shows, we must be able to prove where it came from. If we
> cannot, the number does not ship.

This file is **generated** by `backend/scripts/docs/generate_lineage.py` from the dataset manifest, the
raw checksum lockfile and the per-source ETL reports. It is not written by
hand, because hand-written lineage is correct only until the next commit.

## The chain

```
Original public source (publisher, licence, citation)
        |
        v
data/raw/<source>/<file>.csv        <- frozen, SHA-256 recorded, never modified
        |
        v
backend/etl/<source>.py             <- cleaning + derivation (every action logged)
        |
        v
data/processed/<source>/<table>.csv <- analytics-ready, rebuilt from raw every run
        |
        v
PostgreSQL schema <source>          <- COPY load + measured constraints
        |
        v
SQL / SQLAlchemy / FastAPI / React / Power BI
        |
        v
Rendered KPI
```

Two properties make the chain trustworthy:

1. **Raw data is never modified.** Every stage reads from `data/raw` and
   writes to `data/processed`. The raw files are therefore evidence, not
   working state.
2. **Every transformation is recorded.** Each table below carries the
   decisions applied to it - what was dropped, what was filled, what was
   derived - so a total can be reconciled rather than believed.

## Cross-source boundary

The four sources are four unrelated organisations and their identifiers are
**not** interoperable. Lineage chains terminate at their source: there is no
step anywhere in this pipeline that joins a dunnhumby key to an Olist key.
See `docs/data_sources.md` section 6.

---

## dunnhumby

### dunnhumby - The Complete Journey

- **Publisher:** dunnhumby
- **Domain:** Grocery retail - customer, basket, promotion and direct-marketing analytics
- **Authoritative page:** https://www.dunnhumby.com/source-files/
- **Acquisition:** `automatic`
- **Licence:** Published by dunnhumby as an open "Source Files" dataset for teaching, research and hackathons. dunnhumby states the data is "(nearly) real-world" and modelled on real retail patterns rather than being a verbatim extract of a live retailer. See the Source Files terms on the homepage. Redistributed here only as a download pointer, not re-hosted.
- **Citation:** dunnhumby. "The Complete Journey." dunnhumby Source Files. https://www.dunnhumby.com/source-files/ (accessed 2026-10-02).

**Raw files (frozen; never modified by any stage):**

| Raw file | Bytes | SHA-256 (first 16) |
|---|---:|---|
| `dunnhumby_The-Complete-Journey.zip` | 134,716,463 | `5e0a3d72fe8562fe...` |
| `transaction_data.csv` (extracted) | 141,742,346 | `3a685c0729cef664...` |
| `product.csv` (extracted) | 6,429,896 | `7ecbcec41e0f1e5a...` |
| `hh_demographic.csv` (extracted) | 43,233 | `3764c70d5e38b5da...` |
| `campaign_desc.csv` (extracted) | 540 | `632535c6bcdd97b5...` |
| `campaign_table.csv` (extracted) | 95,874 | `a716f88953c9ddf8...` |
| `coupon.csv` (extracted) | 2,822,804 | `718f4d466df8a54b...` |
| `coupon_redempt.csv` (extracted) | 54,108 | `70a98f3bfb28be0d...` |
| `causal_data.csv` (extracted) | 695,858,427 | `60ed7021fefc209c...` |

**Processed output:** 9 pipeline step(s), 39,606,850 rows across the output tables, written to `data/processed/dunnhumby/`.

**Raw file &rarr; processed table:**

| Processed table | Built from raw file(s) | Rows in | Rows out | Kind |
|---|---|---:|---:|---|
| `households` | `hh_demographic.csv` | 801 | 801 | cleaned |
| `products` | `product.csv` | 92,353 | 92,353 | cleaned |
| `campaigns` | `campaign_desc.csv` | 30 | 30 | cleaned |
| `campaign_contacts` | `campaign_table.csv` | 7,208 | 7,208 | cleaned |
| `coupons` | `coupon.csv` | 124,548 | 119,384 | cleaned |
| `coupon_redemptions` | `coupon_redempt.csv` | 2,318 | 2,318 | cleaned |
| `transactions` | `transaction_data.csv` | 2,595,732 | 2,595,732 | cleaned |
| `promo_exposure` | `causal_data.csv` | 36,786,524 | 36,786,524 | cleaned |
| `household_rfm` | `transaction_data.csv`, `hh_demographic.csv` | 2,595,732 | 2,500 | aggregate |

**Recorded decisions:**

<details><summary><code>households</code> - 2 recorded decision(s)</summary>

- five demographic columns are anonymised by the publisher as classification_1..classification_5. They are KEPT under those names. The likely mapping (age, marital status, income, household size, household composition) is inferred from the value ranges and recorded in docs/data_dictionary.md as an INFERENCE, not as fact.
- covers only 801 households against ~2,500 with transactions, so demographic analysis is a subset analysis and must say so

</details>

<details><summary><code>products</code> - 2 recorded decision(s)</summary>

- BRAND has only 2 values (National / Private); DEPARTMENT carries the category, COMMODITY_DESC and SUB_COMMODITY_DESC the finer levels
- CURR_SIZE_OF_PRODUCT is a free-text size string (e.g. '16 OZ'), not a number, so it is kept as text rather than silently coerced

</details>

<details><summary><code>campaigns</code> - 2 recorded decision(s)</summary>

- 30 campaigns of 3 types (TypeA/TypeB/TypeC); start_day and end_day use the same relative DAY scale as transactions
- campaign durations overlap, so campaigns must be treated as concurrent rather than sequenced - a lift analysis that assumes one campaign at a time would be wrong

</details>

<details><summary><code>campaign_contacts</code> - 1 recorded decision(s)</summary>

- which households were targeted by which campaign; the joined key to transactions is household_key

</details>

<details><summary><code>coupons</code> - 2 recorded decision(s)</summary>

- removed 5,164 duplicate rows on (coupon_upc, product_id, campaign)
- the raw file repeats the same (coupon, product, campaign) mapping many times; de-duplicated so the coupon-to-product mapping is unambiguous

</details>

<details><summary><code>coupon_redemptions</code> - 2 recorded decision(s)</summary>

- only 2,318 redemptions against 119,384 coupon-product mappings - redemption is a rare event, so any redemption rate has a small numerator and must be reported with its denominator
- DAY here runs 225-704, a narrower range than transactions (1-711): coupon redemption only occurs in the later part of the panel

</details>

<details><summary><code>transactions</code> - 8 recorded decision(s)</summary>

- BASKET_ID is globally unique: 276,484 baskets for 2,595,732 lines. It identifies a SHOPPING TRIP, so a basket covers several product lines - grouping by it gives baskets, not lines.
- grain is the transaction LINE; the basket key is basket_id (2,500 households, 92,339 products, DAY 1-711, WEEK_NO 1-102)
- derived: total_discount = -(retail_disc + coupon_disc + coupon_match_disc) as a positive magnitude; gross_sales_value = sales_value - discounts. All discount columns are <= 0 in the real data, so subtracting them correctly recovers the pre-discount value.
- 18,850 line(s) have sales_value = 0 (0.73%) - real free/fully-discounted lines, kept because removing them would understate promotion impact
- 36 line(s) have a net POSITIVE discount, which is unexpected for a discount column - flagged for review, not removed
- flagged 23,136 line(s) (0.89%) with quantity > 100 as quantity_outlier. Max measured quantity is 89,638. Rows are KEPT - an outlier is a fact, not an error, and deleting it would bias every quantity average.
- no negative quantities exist in this dataset, so it contains no returns; consequently no return-rate KPI is defined for dunnhumby
- DAY is a RELATIVE day index (1-711), not a calendar date. dunnhumby publishes no calendar, so any date dimension here is derived, and is labelled as derived wherever it is used.

</details>

<details><summary><code>promo_exposure</code> - 2 recorded decision(s)</summary>

- grain is (product_id, store_id, week_no). display and mailer are the promotional treatments; their combination defines the exposure level.
- streamed in chunks of 1,000,000 rows; this is the largest table in the project and is loaded with COPY, never row-by-row

</details>

<details><summary><code>household_rfm</code> - 3 recorded decision(s)</summary>

- derived: recency_days (against panel last day 711), frequency (distinct baskets), monetary (SUM sales_value), avg_basket_value, lifespan_days, discount_share, total_discount, and 1-5 quintile scores
- monetary is customer SPEND, not profit: this dataset has no cost column, so no margin or CLV-in-profit is computed
- LEFT-joined demographics; 1,699 household(s) have no demographic record. An inner join would have silently dropped them.

</details>

---

## dataco

### dataco - DataCo SMART Supply Chain for Big Data Analysis

- **Publisher:** DataCo Global / Mendeley Data
- **Domain:** Global supply chain - orders, shipping, profitability, late-delivery risk
- **Authoritative page:** https://data.mendeley.com/datasets/8gx2fvg2k6/5
- **Acquisition:** `automatic`
- **Licence:** Mendeley Data, Constante, F.; Silva, F.; Pereira, A. (2019), "DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS", Mendeley Data, V5. Distributed for research use under the terms stated on the Mendeley Data record. Check that record for the current licence before redistribution; here we only download it.
- **Citation:** Constante, F., Silva, F., Pereira, A. (2019). DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS. Mendeley Data, V5. doi:10.17632/8gx2fvg2k6.5

**Raw files (frozen; never modified by any stage):**

| Raw file | Bytes | SHA-256 (first 16) |
|---|---:|---|
| `DataCoSupplyChainDataset.csv` | 95,729,629 | `994b3c8d24049cc4...` |
| `DescriptionDataCoSupplyChain.csv` | 3,391 | `1812b786861128b6...` |

**Processed output:** 6 pipeline step(s), 267,041 rows across the output tables, written to `data/processed/dataco/`.

**Raw file &rarr; processed table:**

| Processed table | Built from raw file(s) | Rows in | Rows out | Kind |
|---|---|---:|---:|---|
| `(column hygiene)` | `DataCoSupplyChainDataset.csv` | 180,519 | - | bookkeeping (not an output table) |
| `(derived shipping fields)` | `DataCoSupplyChainDataset.csv` | 180,519 | - | bookkeeping (not an output table) |
| `orders` | `DataCoSupplyChainDataset.csv` | 180,519 | 65,752 | aggregate |
| `order_items` | `DataCoSupplyChainDataset.csv` | 180,519 | 180,519 | cleaned |
| `customers` | `DataCoSupplyChainDataset.csv` | 180,519 | 20,652 | aggregate |
| `products` | `DataCoSupplyChainDataset.csv` | 180,519 | 118 | aggregate |

**Recorded decisions:**

<details><summary><code>(column hygiene)</code> - 5 recorded decision(s)</summary>

- order_date: parsed from 'order date (DateOrders)' (US MM/DD/YYYY HH:MM); 0 value(s) unparseable
- shipping_date: parsed from 'shipping date (DateOrders)' (US MM/DD/YYYY HH:MM); 0 value(s) unparseable
- dropped always-useless columns: Customer Email and Customer Password (both are the single masked placeholder 'XXXXXXXXX'), and Product Status (one single value in every row)
- dropped 100%-null columns (no data exists in the source): product_description
- dropped constant columns (single distinct value): customer_email (always 'XXXXXXXXX'), customer_password (always 'XXXXXXXXX'), product_status (always np.int64(0))

</details>

<details><summary><code>(derived shipping fields)</code> - 3 recorded decision(s)</summary>

- shipping_delay_days = days_for_shipping_real - days_for_shipment_scheduled: 43,366 early, 33,753 exactly on time, 103,400 late
- is_late mirrors the publisher's own late_delivery_risk flag (kept as the source of truth rather than recomputed, so we can cross-check it)
- lead_time_days = shipping_date - order_date; profit_margin = order_profit_per_order / sales (NULL where sales = 0, never infinite)

</details>

<details><summary><code>orders</code> - 2 recorded decision(s)</summary>

- collapsed 180,519 lines into 65,752 orders; line-level numerics were SUMMED per order (verified to vary per line, so a first-row pick would have been wrong)
- derived: order_items_count, total_quantity, order_value, order_sales, order_profit

</details>

<details><summary><code>order_items</code> - 2 recorded decision(s)</summary>

- grain is the ORDER LINE: order_item_id is unique across all 180,519 rows, while order_id repeats (65,752 distinct orders)
- these numeric columns were verified to VARY within an order, so summing them per order is correct - they are line-level, not order-level repeats

</details>

<details><summary><code>customers</code> - 1 recorded decision(s)</summary>

- collapsed 180,519 lines into 20,652 distinct customers. Latitude/Longitude are REAL in this release (11,250 / 4,487 distinct values) - the widely repeated claim that they are blank is wrong.

</details>

<details><summary><code>products</code> - 1 recorded decision(s)</summary>

- collapsed 180,519 lines into 118 distinct products (one per product_card_id)

</details>

---

## olist

### olist - Brazilian E-Commerce Public Dataset by Olist

- **Publisher:** Olist (published on Kaggle)
- **Domain:** E-commerce - orders, items, payments, reviews, freight, geography
- **Authoritative page:** https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
- **Acquisition:** `automatic`
- **Licence:** Published by Olist as a public dataset for analysis, under CC BY-NC-SA 4.0 as stated on the Kaggle dataset page. Non-commercial use with attribution. We download from a public GitHub mirror because the Kaggle page requires an authenticated account.
- **Citation:** Olist. "Brazilian E-Commerce Public Dataset by Olist." Kaggle. https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce (accessed 2026-10-02).

**Raw files (frozen; never modified by any stage):**

| Raw file | Bytes | SHA-256 (first 16) |
|---|---:|---|
| `olist_orders_dataset.csv` | 17,654,914 | `8df58ef3d2d7e994...` |
| `olist_order_items_dataset.csv` | 15,438,671 | `0bc4d068c4fe38cb...` |
| `olist_order_payments_dataset.csv` | 5,777,138 | `4f713964f2815dbb...` |
| `olist_order_reviews_dataset.csv` | 14,451,670 | `012b61c7593e34f5...` |
| `olist_customers_dataset.csv` | 9,033,957 | `983a422239e1712d...` |
| `olist_products_dataset.csv` | 2,379,446 | `3e6569628a17fbc7...` |
| `olist_sellers_dataset.csv` | 174,703 | `1f643d2b950373b8...` |
| `product_category_name_translation.csv` | 2,729 | `80b25e95fc4a0860...` |
| `olist_marketing_qualified_leads_dataset.csv` | 703,836 | `5537753b7549ce40...` |
| `olist_closed_deals_dataset.csv` | 171,352 | `1433f402d8fff00c...` |
| `Geolocation Dataset.zip` | 15,757,127 | `b5a004dbb871a63b...` |
| `olist_geolocation_dataset.csv` (extracted) | 61,273,883 | `b514f6fc991b9566...` |

**Processed output:** 12 pipeline step(s), 774,081 rows across the output tables, written to `data/processed/olist/`.

**Raw file &rarr; processed table:**

| Processed table | Built from raw file(s) | Rows in | Rows out | Kind |
|---|---|---:|---:|---|
| `orders` | `olist_orders_dataset.csv` | 99,441 | 99,441 | cleaned |
| `order_items` | `olist_order_items_dataset.csv` | 112,650 | 112,650 | cleaned |
| `payments` | `olist_order_payments_dataset.csv` | 103,886 | 103,886 | cleaned |
| `order_payments_agg` | `olist_order_payments_dataset.csv` | 103,886 | 99,440 | aggregate |
| `reviews` | `olist_order_reviews_dataset.csv` | 99,224 | 99,224 | cleaned |
| `customers` | `olist_customers_dataset.csv` | 99,441 | 99,441 | cleaned |
| `products` | `olist_products_dataset.csv`, `product_category_name_translation.csv` | 32,951 | 32,951 | cleaned |
| `sellers` | `olist_sellers_dataset.csv` | 3,095 | 3,095 | cleaned |
| `geolocation_by_zip` | `olist_geolocation_dataset.csv` | 1,000,163 | 19,015 | aggregate |
| `mql` | `olist_marketing_qualified_leads_dataset.csv` | 8,000 | 8,000 | cleaned |
| `closed_deals` | `olist_closed_deals_dataset.csv` | 842 | 842 | cleaned |
| `customer_rfm` | `olist_orders_dataset.csv`, `olist_customers_dataset.csv`, `olist_order_items_dataset.csv` | 99,441 | 96,096 | aggregate |

**Recorded decisions:**

<details><summary><code>orders</code> - 3 recorded decision(s)</summary>

- 8 order(s) have status 'delivered' but no delivered_customer_date - they are excluded from delivery-day KPIs
- order_status distribution: approved=2, canceled=625, created=5, delivered=96,478, invoiced=314, processing=301, shipped=1,107, unavailable=609
- derived: delivery_days, estimated_days, delay_days (positive = late), is_late, is_delivered

</details>

<details><summary><code>order_items</code> - 1 recorded decision(s)</summary>

- derived: item_total = price + freight_value (product revenue and freight kept separately as well)

</details>

<details><summary><code>payments</code> - 1 recorded decision(s)</summary>

- 2,961 order(s) are paid in more than one row - this is why payments must be aggregated before being joined to items

</details>

<details><summary><code>order_payments_agg</code> - 1 recorded decision(s)</summary>

- derived: payment_total = SUM(payment_value), plus payment_count, payment_installments_max, primary_payment_type

</details>

<details><summary><code>reviews</code> - 2 recorded decision(s)</summary>

- 789 review_id(s) appear more than once; review rows are not strictly 1:1 with orders (99,224 reviews vs 99,441 orders)
- derived: review_response_hours = answer_timestamp - creation_date; review_comment_title/message are deliberately kept despite high null rates (88%/59%) because the nulls are real 'no comment left' values

</details>

<details><summary><code>customers</code> - 2 recorded decision(s)</summary>

- customer_id is per-order (99,441 unique) while customer_unique_id identifies the person (96,096 unique). Repeat-buyer, RFM and CLV analysis MUST use customer_unique_id.
- 3.4% of customer_id values belong to a person with more than one order

</details>

<details><summary><code>products</code> - 3 recorded decision(s)</summary>

- corrected the publisher's misspelling of 'lenght' -> 'length' in the processed layer only (raw is preserved unchanged): product_name_lenght->product_name_length, product_description_lenght->product_description_length
- 610 product(s) have no category; filled with the literal 'unknown' rather than inventing one. This is a real gap in the source.
- joined the PT->EN category translation; 610 row(s) have no English name (including 'unknown')

</details>

<details><summary><code>geolocation_by_zip</code> - 3 recorded decision(s)</summary>

- removed 261,831 duplicate rows on (all columns)
- de-duplicated 261,831 identical rows
- derived: one row per zip prefix with the MEAN lat/lng of all observations, the modal city/state, and the observation count

</details>

<details><summary><code>closed_deals</code> - 1 recorded decision(s)</summary>

- columns that are majority-NULL (kept, but not useful as features): average_stock=92.16%, declared_product_catalog_size=91.81%, has_company=92.52%, has_gtin=92.4%

</details>

<details><summary><code>customer_rfm</code> - 3 recorded decision(s)</summary>

- recency is measured against the last purchase in the dataset (2018-10-17), not against today - the data ends in 2018
- derived: recency_days, frequency (distinct orders), monetary (SUM of item price, freight excluded), avg_order_value, lifespan_days, and 1-5 quintile scores for R, F and M
- grain is customer_unique_id: 96,096 real people (vs 99,441 order-scoped customer_id)

</details>

---

## m5

### m5 - M5 Forecasting - Accuracy (Walmart)

- **Publisher:** Walmart / Kaggle (Makridakis et al.)
- **Domain:** Demand forecasting - daily unit sales across stores, items and states
- **Authoritative page:** https://www.kaggle.com/competitions/m5-forecasting-accuracy
- **Acquisition:** `automatic`
- **Licence:** Released by Walmart for the M5 forecasting competition and used widely in academic work (Makridakis, Spiliotis & Assimakopoulos, 2022, International Journal of Forecasting). Kaggle competition rules apply to the competition itself; the data is used here for non-commercial research/portfolio analysis with attribution.
- **Citation:** Makridakis, S., Spiliotis, E., Assimakopoulos, V. (2022). "M5 accuracy competition: Results, findings, and conclusions." International Journal of Forecasting, 38(4), 1346-1364. Data: Kaggle, "M5 Forecasting - Accuracy", Walmart.

**Raw files (frozen; never modified by any stage):**

| Raw file | Bytes | SHA-256 (first 16) |
|---|---:|---|
| `calendar.csv` | 103,469 | `d12b5914ef03e666...` |
| `sales_train_evaluation.csv` | 121,736,518 | `4b4a47c44c38380d...` |
| `sell_prices.csv` | 203,395,785 | `9da3ad1f8b8ccacd...` |

**Processed output:** 5 pipeline step(s), 66,056,611 rows across the output tables, written to `data/processed/m5/`.

**Raw file &rarr; processed table:**

| Processed table | Built from raw file(s) | Rows in | Rows out | Kind |
|---|---|---:|---:|---|
| `calendar` | `calendar.csv` | 1,969 | 1,969 | cleaned |
| `sell_prices` | `sell_prices.csv` | 6,841,121 | 6,841,121 | cleaned |
| `series` | `sales_train_evaluation.csv` | 30,490 | 30,490 | aggregate |
| `sales_long` | `sales_train_evaluation.csv` | 59,181,090 | 59,181,090 | aggregate |
| `sales_daily_total` | `sales_train_evaluation.csv`, `calendar.csv` | 1,969 | 1,941 | aggregate |

**Recorded decisions:**

<details><summary><code>calendar</code> - 2 recorded decision(s)</summary>

- calendar covers 2011-01-29 to 2016-06-19 (1,969 days); 'd' is the day code and d_num is its integer form, added so joins are numeric rather than string-prefix matches
- event_name_2 is NULL on 1,964 of 1,969 days (99.75%) - most days simply have one event. This is real sparsity, not missing data, and is left as NULL rather than filled with a placeholder that would look like a real event.

</details>

<details><summary><code>sell_prices</code> - 3 recorded decision(s)</summary>

- 0 row(s) have a non-positive price; kept, because removing them would break the price history of those item-weeks
- prices are WEEKLY (keyed by wm_yr_wk) while sales are DAILY. Revenue derived as units x sell_price is therefore an ESTIMATE that assumes the week's price applies on each day of that week.
- the table is SPARSE: not every item-store has a price in every week (items enter and leave assortment), so joins must tolerate missing prices rather than treating them as zero

</details>

<details><summary><code>series</code> - 3 recorded decision(s)</summary>

- 30,490 item-store series across 10 stores, 3 states, 3 categories and 3 departments
- id decomposes as {cat}_{dept}_{item}_{state}_{store}_{horizon}; the horizon suffix ('evaluation') marks which Kaggle split the file holds
- all rows share the same horizon suffix: 'evaluation'

</details>

<details><summary><code>sales_long</code> - 4 recorded decision(s)</summary>

- melted the WIDE sales file (2,000 series per chunk, 1,969 day columns) into 59,181,090 long rows. Wide format cannot be stored relationally or fed to a time-series model.
- sales are INTEGER UNITS, not currency. Any revenue figure is units x sell_price and is an ESTIMATE - it must never be presented as revenue comparable to Olist or DataCo.
- the date for each row comes from joining calendar on d_num; the calendar table is the single source of truth for the day -> date mapping
- rows with sales = 0 are KEPT. A zero-sales day is real information (the item was stocked but unsold) and is essential for forecasting; dropping zeros would inflate every average and destroy the series shape.

</details>

<details><summary><code>sales_daily_total</code> - 2 recorded decision(s)</summary>

- total_units = SUM of sales across all 30,490 series for that day; derived during the same pass as the melt (single scan of the source file)
- 0 day(s) could not be matched to a calendar date

</details>

---

## Derived field catalogue

Fields that do not exist in the source and were computed by this pipeline.
Each entry records its formula, so a number can be recomputed rather than
taken on trust. Anything **not** listed here is an observed source value.

| Source | Table | Derived fields |
|---|---|---|
| `dataco` | `orders` | order_items_count, total_quantity, order_value, order_sales, order_profit |
| `dunnhumby` | `transactions` | total_discount = -(retail_disc + coupon_disc + coupon_match_disc) as a positive magnitude; gross_sales_value = sales_value - discounts. All discount columns are <= 0 in the real data, so subtracting them correctly recovers the pre-discount value. |
| `dunnhumby` | `household_rfm` | recency_days (against panel last day 711), frequency (distinct baskets), monetary (SUM sales_value), avg_basket_value, lifespan_days, discount_share, total_discount, and 1-5 quintile scores |
| `olist` | `orders` | delivery_days, estimated_days, delay_days (positive = late), is_late, is_delivered |
| `olist` | `order_items` | item_total = price + freight_value (product revenue and freight kept separately as well) |
| `olist` | `order_payments_agg` | payment_total = SUM(payment_value), plus payment_count, payment_installments_max, primary_payment_type |
| `olist` | `reviews` | review_response_hours = answer_timestamp - creation_date; review_comment_title/message are deliberately kept despite high null rates (88%/59%) because the nulls are real 'no comment left' values |
| `olist` | `geolocation_by_zip` | one row per zip prefix with the MEAN lat/lng of all observations, the modal city/state, and the observation count |
| `olist` | `customer_rfm` | recency_days, frequency (distinct orders), monetary (SUM of item price, freight excluded), avg_order_value, lifespan_days, and 1-5 quintile scores for R, F and M |

## Loaded into PostgreSQL

`backend/scripts/db/04_load.py` COPYed the processed CSVs into tables that Alembic had already created, then re-measured every constraint the migration declared against the rows that were actually loaded. It issues no DDL of its own. The counts below come from `data/processed/_load_report.json`, written by the loader itself.

**30 tables, 106,704,583 rows.** 46 declared constraints verified against the live rows; 1 candidate(s) were rejected by the data.

The schema these verdicts belong to is Alembic revision `baseline`.

This database was assembled over 10 loader runs, because a scoped run (`--source`, `--table`) merges into the report rather than overwriting it. The table and constraint counts below therefore describe the whole database, not the most recent invocation.

The most recent run touched 30 of the 30 tables with no COPY: verified in place without a COPY (105.5s for all 30 tables). Row counts below are read from the database either way, and the key tests were re-run against the rows in all cases.

| Schema.table | Rows | How the data got there |
|---|---:|---|
| `dataco.customers` | 20,652 | verified in place (no COPY) |
| `dataco.order_items` | 180,519 | verified in place (no COPY) |
| `dataco.orders` | 65,752 | verified in place (no COPY) |
| `dataco.products` | 118 | verified in place (no COPY) |
| `dunnhumby.campaign_contacts` | 7,208 | verified in place (no COPY) |
| `dunnhumby.campaigns` | 30 | verified in place (no COPY) |
| `dunnhumby.coupon_redemptions` | 2,318 | verified in place (no COPY) |
| `dunnhumby.coupons` | 119,384 | verified in place (no COPY) |
| `dunnhumby.household_rfm` | 2,500 | verified in place (no COPY) |
| `dunnhumby.households` | 801 | verified in place (no COPY) |
| `dunnhumby.products` | 92,353 | verified in place (no COPY) |
| `dunnhumby.promo_exposure` | 36,786,524 | verified in place (no COPY) |
| `dunnhumby.transactions` | 2,595,732 | verified in place (no COPY) |
| `m5.calendar` | 1,969 | verified in place (no COPY) |
| `m5.sales_daily_total` | 1,941 | verified in place (no COPY) |
| `m5.sales_long` | 59,181,090 | verified in place (no COPY) |
| `m5.sell_prices` | 6,841,121 | verified in place (no COPY) |
| `m5.series` | 30,490 | verified in place (no COPY) |
| `olist.closed_deals` | 842 | verified in place (no COPY) |
| `olist.customer_rfm` | 96,096 | verified in place (no COPY) |
| `olist.customers` | 99,441 | verified in place (no COPY) |
| `olist.geolocation_by_zip` | 19,015 | verified in place (no COPY) |
| `olist.mql` | 8,000 | verified in place (no COPY) |
| `olist.order_items` | 112,650 | verified in place (no COPY) |
| `olist.order_payments_agg` | 99,440 | verified in place (no COPY) |
| `olist.orders` | 99,441 | verified in place (no COPY) |
| `olist.payments` | 103,886 | verified in place (no COPY) |
| `olist.products` | 32,951 | verified in place (no COPY) |
| `olist.reviews` | 99,224 | verified in place (no COPY) |
| `olist.sellers` | 3,095 | verified in place (no COPY) |

### Declared constraints, verified against the live rows

| Kind | Table | Columns |
|---|---|---|
| primary key | `dataco.customers` | `customer_id` |
| foreign key | `dataco.order_items` | `order_id` |
| foreign key | `dataco.order_items` | `product_card_id` |
| primary key | `dataco.order_items` | `order_item_id` |
| foreign key | `dataco.orders` | `customer_id` |
| primary key | `dataco.orders` | `order_id` |
| primary key | `dataco.products` | `product_card_id` |
| primary key | `dunnhumby.campaign_contacts` | `household_key, campaign` |
| primary key | `dunnhumby.campaigns` | `campaign` |
| primary key | `dunnhumby.coupons` | `coupon_upc, product_id, campaign` |
| primary key | `dunnhumby.household_rfm` | `household_key` |
| primary key | `dunnhumby.households` | `household_key` |
| primary key | `dunnhumby.products` | `product_id` |
| foreign key | `dunnhumby.promo_exposure` | `product_id` |
| primary key | `dunnhumby.promo_exposure` | `product_id, store_id, week_no, display, mailer` |
| foreign key | `dunnhumby.transactions` | `product_id` |
| primary key | `dunnhumby.transactions` | `household_key, basket_id, product_id` |
| primary key | `m5.calendar` | `d_num` |
| unique | `m5.calendar` | `date` |
| foreign key | `m5.sales_daily_total` | `d_num` |
| primary key | `m5.sales_daily_total` | `d_num` |
| foreign key | `m5.sales_long` | `d_num` |
| foreign key | `m5.sales_long` | `id` |
| primary key | `m5.sales_long` | `id, d_num` |
| primary key | `m5.sell_prices` | `store_id, item_id, wm_yr_wk` |
| primary key | `m5.series` | `id` |
| foreign key | `olist.closed_deals` | `mql_id` |
| primary key | `olist.closed_deals` | `mql_id` |
| primary key | `olist.customer_rfm` | `customer_unique_id` |
| primary key | `olist.customers` | `customer_id` |
| unique | `olist.customers` | `customer_unique_id, customer_id` |
| primary key | `olist.geolocation_by_zip` | `geolocation_zip_code_prefix` |
| primary key | `olist.mql` | `mql_id` |
| foreign key | `olist.order_items` | `order_id` |
| foreign key | `olist.order_items` | `product_id` |
| foreign key | `olist.order_items` | `seller_id` |
| primary key | `olist.order_items` | `order_id, order_item_id` |
| foreign key | `olist.order_payments_agg` | `order_id` |
| primary key | `olist.order_payments_agg` | `order_id` |
| foreign key | `olist.orders` | `customer_id` |
| primary key | `olist.orders` | `order_id` |
| foreign key | `olist.payments` | `order_id` |
| primary key | `olist.payments` | `order_id, payment_sequential` |
| primary key | `olist.products` | `product_id` |
| primary key | `olist.reviews` | `review_id, order_id` |
| primary key | `olist.sellers` | `seller_id` |

### Candidate keys the data refused

These were proposed, tested against the real rows, and found not to hold, so Alembic correctly declares no such constraint. **Nothing was deleted to make them pass.** Deleting real rows to satisfy a key a human guessed at would leave the data quietly wrong and the schema looking clean, so the failure is recorded instead - and re-measured on every load, so it stays a live number rather than a story:

| Kind | Table | Columns | Why it was rejected |
|---|---|---|---|
| primary key | `dunnhumby.coupon_redemptions` | `household_key, day, coupon_upc` | rejected: 14 duplicate row(s) across 14 key value(s) |

## Independent verification

`backend/scripts/db/05_verify_integrity.py` re-derives every integrity claim from the rows now in PostgreSQL instead of trusting the catalog. It reconciles every table's live row count against what the ETL reported, re-runs the orphan query behind each foreign key, re-runs the duplicate query behind each primary key, and confirms that no foreign key crosses a schema boundary.

**0 failure(s), 1 documented warning(s).**

- [pass] dataco line sum = order sum
- [pass] m5.sales_long total units
- [pass] m5 daily rollup vs sales_long
- [pass] olist aggregated payment total = raw total
- [pass] olist.orders -> customers(customer_id): 0 orphans
- [pass] olist.order_items -> orders(order_id): 0 orphans
- [pass] olist.order_items -> products(product_id): 0 orphans
- [pass] olist.order_items -> sellers(seller_id): 0 orphans
- [pass] olist.payments -> orders(order_id): 0 orphans
- [pass] olist.order_payments_agg -> orders(order_id): 0 orphans
- [pass] olist.closed_deals -> mql(mql_id): 0 orphans
- [pass] dataco.orders -> customers(customer_id): 0 orphans
- [pass] dataco.order_items -> orders(order_id): 0 orphans
- [pass] dataco.order_items -> products(product_card_id): 0 orphans
- [pass] dunnhumby.transactions -> products(product_id): 0 orphans
- [pass] dunnhumby.promo_exposure -> products(product_id): 0 orphans
- [pass] m5.sales_long -> series(id): 0 orphans
- [pass] m5.sales_long -> calendar(d_num): 0 orphans
- [pass] m5.sales_daily_total -> calendar(d_num): 0 orphans

## The cross-source rule, enforced by the schema

The hardest rule in this project is that the four sources must never be joined, because their identifiers are unrelated: dunnhumby household 123, olist customer 123 and dataco customer 123 are three different people at three different companies. A rule that lives only in a document gets violated the first time someone is in a hurry, so here it is a structural property instead - **every foreign key points inside its own schema**, and `05_verify_integrity.py` fails if one ever does not.

For the same reason there is no 'OmniRetail total revenue'. Four unrelated companies do not add up to a number.

---

## Reconciliation checks performed

These are checks that were actually executed against the real data, not
claims about intent:

| Check | Result |
|---|---|
| DataCo: sum of line-level `order_item_total` vs sum of order-level `order_value` | equal to the cent (difference `0.0`) |
| DataCo: derived `is_late` vs the publisher's own `delivery_status` | exact match - `Late delivery` &harr; `True`, 36,048 orders, no contradictions |
| Olist: `delivery_days` has no negative values | 0 rows - no order is delivered before it is placed |
| Olist: `customer_rfm` grain | 96,096 real people from 99,441 orders, proving `customer_unique_id` was used and not the per-order `customer_id` |
| M5: melted row count | 59,181,090 = 30,490 series &times; 1,941 days, exact |
| M5: processed total vs raw total units | 66,927,173 = 66,927,173, exact |
| Every raw file: SHA-256 re-hashed and compared to the lockfile | match |

## Known gaps in this document

- The chain above reaches the API and the frontend, but the *provenance*
  of a rendered number is enforced by different mechanisms, not by this
  document: a catalogued query's SQL is fixed and named
  (`backend/sql/<source>/*.sql`), its results are recorded
  (`data/processed/_sql_results.json`), and every figure written into
  documentation is checked against that file by
  `backend/tests/test_analytics_findings.py`. The AI layer additionally
  refuses to state a number that is not present in the rows the query
  returned. Those are the checks; this file is the map that points at
  them.
- Lineage currently stops at the loaded table. Column-level lineage
  (which raw column feeds which processed column) is described in
  `docs/data_dictionary.md` and the recorded decisions above, but is not
  yet machine-enforced.
- `/api/forecasting` and `/api/anomalies` serve recorded artefacts
  (`data/processed/_forecast_results.json`,
  `data/processed/_anomaly_results.json`) rather than querying the
  database. Their inputs are the same `m5.sales_long` table and the same
  measured calendar, and each artefact carries the `generated_utc` of
  the batch run that wrote it.
