# Data Dictionary

This document records **what the columns actually mean and how they behave**,
based on measuring the real files. Per-column statistics for all 25 files —
row counts, dtypes, null rates, cardinality, min/max — live in
[`docs/profiling/`](profiling/) and its machine-readable companion
`docs/profiling/_summary.json`. Read this file for *meaning*; read the profiling
reports for *numbers*.

Four sources, four unrelated organisations. Identifiers never cross a source
boundary (see `docs/data_sources.md` §6).

---

## 1. `dunnhumby` — grocery customer & promotion analytics

### ⚠️ The demographic column mapping (corrected)

This is the most important entry in this document. The official dunnhumby
release **anonymises five demographic columns** as `classification_1` …
`classification_5`. The widely-mirrored Kaggle revision has readable names, so
most published code and tutorials use those names and will break here.

We measured the **value ranges** of each column, and they identify the meaning
unambiguously:

| Measured column | Distinct | Observed values | Meaning (inferred) | Kaggle-era name |
|---|---:|---|---|---|
| `classification_1` | 6 | `Age Group1` … `Age Group6` | age band | `AGE_DESC` |
| `classification_2` | 3 | `X`, `Y`, `Z` | marital status (coded) | `MARITAL_STATUS_CODE` |
| `classification_3` | 12 | `Level1` … `Level9` | income band | `INCOME_DESC` |
| `classification_4` | 5 | `1` … `5+` | household size | `HOUSEHOLD_SIZE_DESC` |
| `classification_5` | 6 | `Group1` … `Group6` | household composition | `HH_COMP_DESC` |
| `HOMEOWNER_DESC` | 5 | `Homeowner`, `Unknown`, … | home ownership | *(unchanged)* |
| `KID_CATEGORY_DESC` | 4 | `1`, `None/Unknown`, … | children in household | *(unchanged)* |

**These mappings are inferred, not published.** They are consistent with the
value shapes and with the Kaggle-era schema, but they are an inference and are
labelled as such. Anything built on them must be described as
"demographic band", not as a claim about a specific attribute, until confirmed
against the dunnhumby User Guide PDF shipped in `data/raw/dunnhumby/`.

### Entities and keys

| Table | Grain | True key |
|---|---|---|
| `transaction_data.csv` | one product on one shopping trip | **(household_key, BASKET_ID, PRODUCT_ID)** |
| `product.csv` | one product | `PRODUCT_ID` |
| `hh_demographic.csv` | one household | `household_key` (covers 801 of ~2,500 households) |
| `campaign_desc.csv` | one campaign | `CAMPAIGN` (9 campaigns) |
| `campaign_table.csv` | one household↔campaign contact | `(household_key, CAMPAIGN)` |
| `coupon.csv` | one coupon↔product mapping | `(COUPON_UPC, PRODUCT_ID, CAMPAIGN)` |
| `coupon_redempt.csv` | one redemption event | `(household_key, DAY, COUPON_UPC)` |
| `causal_data.csv` | one product×store×week promotional exposure | `(PRODUCT_ID, STORE_ID, WEEK_NO)` |

### Traps

- **`BASKET_ID` identifies a shopping trip, not a row.** Measured over the whole
  file: 276,484 distinct `BASKET_ID` values against 2,595,732 lines, and the
  distinct count equals the number of `(household_key, BASKET_ID)` groups — so
  `BASKET_ID` *is* globally unique. The trap is the opposite of the usual
  assumption: grouping by `BASKET_ID` gives baskets, and treating it as a line
  key over-counts nothing but under-reports lines. (An earlier draft of this
  document repeated the common claim that it repeats across households; measuring
  the full file disproved that.)
- **Discounts are negative.** `RETAIL_DISC`, `COUPON_DISC` and
  `COUPON_MATCH_DISC` are all `<= 0` (verified across the file). So:
  `total_discount = -(RETAIL_DISC + COUPON_DISC + COUPON_MATCH_DISC)` is a
  positive magnitude, and pre-discount value is
  `SALES_VALUE - (RETAIL_DISC + COUPON_DISC + COUPON_MATCH_DISC)`. Subtracting the
  negative discounts *adds* them back; a sign error here flips the direction of
  every promotion conclusion.
- **`QUANTITY` has a real long tail**: 99th percentile is 99, but the maximum is
  **89,638** and 23,101 lines exceed 1,000. These are flagged
  (`quantity_outlier = QUANTITY > 100`) and **kept** — deleting them would bias
  every quantity average.
- **There are no returns in this dataset.** No line has a negative `QUANTITY`,
  which is why **no return-rate KPI is defined for dunnhumby**. Note the contrast
  with the original design assumption that a negative quantity might mean a
  return — here it simply never happens.
- **18,850 lines have `SALES_VALUE = 0`** (free / fully-discounted lines). They
  are kept, because dropping them would understate promotional impact.
- `causal_data.csv` ships with the plain header
  `PRODUCT_ID,STORE_ID,WEEK_NO,display,mailer` — there is **no unnamed index
  column**. (An earlier draft claimed one; the real header disproved it.)
- `DAY` is a **relative day index** (1…711), not a calendar date. There is no
  real calendar in this dataset, so calendars must be derived and labelled as
  derived. `WEEK_NO` runs 1…102. Coupon redemptions only occur on `DAY` 225…704.
- 36.8M rows in `causal_data.csv` — cleaned to `promo_exposure.csv` by streaming,
  and bulk loaded with `COPY`, never row-by-row.

---

## 2. `dataco` — supply chain & operations

### Entities and keys

| Table | Grain | True key |
|---|---|---|
| `DataCoSupplyChainDataset.csv` | one **order line** | not unique on any single column; `Order Id` repeats |

Normalisation targets (built later in the SQL layer): `orders`, `order_items`,
`customers`, `products`, `shipping`.

### Traps

- **Column names contain spaces and capitals** — `Order Id`,
  `Order Item Total`, `order date (DateOrders)`, `Days for shipping (real)`.
  Every reference must be double-quoted in SQL. This is why the analytics layer
  normalises to snake_case names.
- **`Order Id` is not unique** — one order spans multiple lines.
- **`Product Description` is 100% NULL** (measured: 180,519 of 180,519 rows).
  Do not build a product-description feature; there is nothing behind it.
- **`Order Zipcode` is 86.24% NULL**, `Customer Zipcode` has 3 nulls,
  `Customer Lname` has 8. Null handling must be explicit, not incidental.
- **`Latitude`/`Longitude` are populated** — 11,250 / 4,487 distinct values
  (lat −33.94…48.78, lng −158.03…115.26). Geographic analysis with real
  coordinates is therefore possible. The common claim that they are blank is
  wrong for this release.
- **`Customer Email` and `Customer Password` are both the single masked value
  `XXXXXXXXX`**, and `Product Status` is single-valued. All three are dropped.
- `Delivery Status` and `Late_delivery_risk` are the **discrete** targets;
  `Days for shipping (real)` and `Days for shipment (scheduled)` are the
  **continuous** ones. Delivery delay = real − scheduled, and it can be negative
  (early delivery) — that is valid business behaviour, not a data error.
- Dates are **US-format strings** (`1/1/2015 0:00`) and need explicit parsing.
  This is also why the file is read as `latin-1`: it is not valid UTF-8.

---

## 3. `olist` — e-commerce analytics

### Entities and keys

| Table | Grain | Key | Foreign keys |
|---|---|---|---|
| `olist_orders_dataset.csv` | one order | `order_id` | → `customer_id` |
| `olist_order_items_dataset.csv` | one item in an order | `(order_id, order_item_id)` | → `product_id`, `seller_id` |
| `olist_order_payments_dataset.csv` | one payment **method** on an order | `(order_id, payment_sequential)` | → `order_id` |
| `olist_order_reviews_dataset.csv` | one **review-to-order link** | `(review_id, order_id)` | → `order_id` |
| `olist_customers_dataset.csv` | one **order-scoped** customer record | `customer_id` | `customer_unique_id` is the person |
| `olist_products_dataset.csv` | one product | `product_id` | → `product_category_name` |
| `olist_sellers_dataset.csv` | one seller | `seller_id` | |
| `olist_geolocation_dataset.csv` | one zip-prefix observation | none (duplicated) | `geolocation_zip_code_prefix` |
| `product_category_name_translation.csv` | one category | `product_category_name` | |

### Traps

- **`customer_id` ≠ `customer_unique_id`.** `customer_id` is minted per order, so
  it can never identify a repeat buyer. Repeat-purchase, RFM and CLV analysis
  **must** use `customer_unique_id`. Getting this wrong is the single most common
  Olist mistake and it collapses every customer into a one-time buyer.
- **Payments double-count revenue.** An order can have several payment rows
  (cards + vouchers). Joining payments to items without aggregating payments
  per order first multiplies item revenue. KPI definitions must state the
  aggregation explicitly.
- **Reviews are not 1:1 with orders** (99,224 reviews, 99,441 orders).
- **Publisher typos are preserved in raw**: `product_name_lenght`,
  `product_description_lenght`. Raw stays byte-faithful; corrected only in
  processed/analytics layers.
- **Missing delivery timestamps are meaningful, not junk**: measured 2,965 orders
  with no `order_delivered_customer_date` and 160 with no `order_approved_at`.
  These are overwhelmingly orders that were cancelled or never completed —
  they must be **excluded by status**, not dropped blindly, or cancelled orders
  will silently distort delivery KPIs.
- **`geolocation` must be aggregated** by zip prefix before joining (~1M rows,
  heavy duplication).
- Dates are ISO-ish strings (`2016-09-04 21:15:19`) and parse cleanly.
- The tail of the period (Aug–Sep 2018) is **incomplete**; period-over-period
  comparisons must account for it.

---

## 4. `m5` — demand forecasting

### Entities and keys

| Table | Grain | Key |
|---|---|---|
| `sales_train_evaluation.csv` | one item×store series | `id` (Walmart's own composite) |
| `calendar.csv` | one day | `date`, `d` |
| `sell_prices.csv` | one item×store×week price | `(store_id, item_id, wm_yr_wk)` |

### Structure

- `id` decomposes as `{cat_id}_{dept_id}_{item_id}_{state_id}_{store_id}_evaluation`.
- `d_1` … `d_1941` are **day columns**, not row-indexed observations. `calendar`
  is the only thing that maps `d_N` to a real date. This is a **wide → long
  reshape**, and it is a required transformation, not a nicety.
- `calendar` spans 2011-01-29 → 2016-06-19 (1,969 days) and carries the real
  event, holiday and SNAP signals that make event effects testable.

### Traps

- **`sales` are integer units, not currency.** "Revenue" for M5 is
  `units × sell_price` and is therefore an **estimate** — and because prices are
  **weekly** while sales are **daily**, the estimate carries a documented
  assumption. It must never be compared as if it were Olist or DataCo revenue.
- Prices do not exist for every item×week (items enter and leave ranges), so
  `sell_prices` is sparse. Joins must tolerate missing prices.
- **Walmart IDs are meaningless anywhere else.** `FOODS_1_001` cannot be joined
  to any other source. This is a hard boundary.
- 6.8M rows in `sell_prices.csv` — bulk load with `COPY`.

---

## 5. Cross-source conventions

- The `analytics` schema is the only place sources meet, and **every table there
  carries a `source_system` column** with values `'dunnhumby'`, `'dataco'`,
  `'olist'`, `'m5'`.
- No blended "OmniRetail total". Revenue, orders and customers are reported
  **per source**, always labelled.
- Derived fields are named so their origin is obvious (`derived_*` or documented
  in the transformation layer), because a derived number must be distinguishable
  from an observed one.
- Every derived field's formula is recorded in `docs/data_lineage.md`.

---

## 6. Open items

- The `classification_N` mapping in §1 should be **confirmed** against the
  dunnhumby User Guide PDF before any demographic claim is treated as final.
- Row-level grain and key claims above are from measurement; once tables are
  loaded, they will be enforced as real `PRIMARY KEY` and `FOREIGN KEY`
  constraints, which is the strongest possible statement of the same facts.
