# Data Sources

> **This document answers one question: where exactly did every number in this
> platform come from?**
>
> Every figure below was measured on this machine from the downloaded files, not
> copied from a blog. The machine-readable version lives in
> `data/raw/_measurements.json`; the integrity record lives in
> `data/raw/_checksums.json`.

**Acquisition date:** 2026-10-03
**Total raw data on disk:** ~1.48 GB across 27 files (plus one PDF)
**Synthetic data in this project:** none.

---

## 1. What we are building, and why that decides the data

The platform must support four genuinely different analytical capabilities:

| Capability | Needs | Cannot be done with |
|---|---|---|
| Customer & promotion analytics | household transactions, promotions, coupons, campaign contact | order-level data with no customer history |
| Operations & supply-chain analytics | order lifecycle, shipping dates, delivery status, profitability | behavioural event streams |
| E-commerce analytics | orders, items, payments, reviews, geography, sellers | grocery basket data |
| Demand forecasting | long, dense, regular daily time series per item/store | short or irregular histories |

No single public dataset covers all four. So the project uses **four datasets,
one per domain** — and, critically, keeps them **separate**. That separation is
a design decision, not a limitation, and section 6 explains why in detail.

---

## 2. The four selected datasets

### A. dunnhumby — "The Complete Journey"

| | |
|---|---|
| **Publisher** | dunnhumby |
| **Source (authoritative)** | https://www.dunnhumby.com/source-files/ |
| **Direct download used** | `downloads.ctfassets.net` CDN asset `dunnhumby_The-Complete-Journey.zip` |
| **Licence** | Published by dunnhumby as an open "Source Files" dataset for teaching, research and hackathons |
| **Citation** | dunnhumby. "The Complete Journey." *dunnhumby Source Files*. https://www.dunnhumby.com/source-files/ (accessed 2026-10-03). |
| **Acquisition** | Fully automatic — direct download, **no login required** |
| **Archive** | 134,716,463 bytes, sha256 `5e0a3d72fe8562fe0ab995f70fb58b74359e8ec4bbccd1521e2b137da0558f9a` |

**Domain:** grocery retail — customer, basket, promotion and direct-marketing analytics.

**Measured contents** (all columns verified against the real headers):

| File | Bytes | Rows | Cols |
|---|---:|---:|---:|
| `transaction_data.csv` | 141,742,346 | **2,595,732** | 12 |
| `causal_data.csv` | 695,858,427 | **36,786,524** | 5 |
| `product.csv` | 6,429,896 | 92,353 | 7 |
| `coupon.csv` | 2,822,804 | 124,548 | 3 |
| `campaign_table.csv` | 95,874 | 7,208 | 3 |
| `coupon_redempt.csv` | 54,108 | 2,318 | 4 |
| `hh_demographic.csv` | 43,233 | 801 | 8 |
| `campaign_desc.csv` | 540 | 30 | 4 |
| `dunnhumby - The Complete Journey User Guide.pdf` | 970,346 | — | — |

The published figure is "around 2.6 million transactions"; we measured
**2,595,732**. The match is the point: it confirms we downloaded the real dataset
and not a related sample. The same check was applied to every other source below.

**⚠️ Real finding — an important discrepancy from the internet's assumptions.**
Nearly every tutorial on the web assumes this file has columns named
`AGE_DESC`, `INCOME_DESC`, `MARITAL_STATUS_CODE`, `HH_COMP_DESC` and
`HOUSEHOLD_SIZE_DESC`. Those names come from an **older dated revision** that was
widely mirrored to Kaggle. The **current official release has anonymised them**:

```
classification_1, classification_2, classification_3, HOMEOWNER_DESC,
classification_5, classification_4, KID_CATEGORY_DESC, household_key
```

The *values* are still readable (`Age Group6`, `Level4`, `Group5`, `2`,
`X`, …), so demographic segmentation remains entirely possible — but the five
`classification_N` columns must be mapped to their meaning before use, and any
code copied from the internet that expects `AGE_DESC` will fail immediately.
This was caught by `02_verify.py` failing the schema contract, which is exactly
what that contract is for. The mapping is recorded in `docs/data_dictionary.md`.

**Known limitations**
- Not a real retailer's literal ledger. dunnhumby describes the set as
  *"(nearly) real-world… a representation of household level transactions"* and
  says the data is modelled on real retail patterns. We document it as
  publisher-published realistic retail data, not as a production extract.
- Household IDs are anonymous and meaningless outside this dataset.
- Households covered by transactions (~2,500) far exceed those with
  demographics (801 rows), so demographic analysis covers a subset only.
- **No supplier, warehouse, or inventory-level data exists.** Therefore this
  project creates **no supplier table and no warehouse table**. Fabricating them
  would break the core rule of this build.

---

### B. DataCo — SMART Supply Chain for Big Data Analysis

| | |
|---|---|
| **Publisher** | DataCo Global, published via Mendeley Data |
| **Authoritative record** | https://data.mendeley.com/datasets/8gx2fvg2k6/5 |
| **Download URL used** | public GitHub mirror `ashishpatel26/DataCo-SMART-SUPPLY-CHAIN-FOR-BIG-DATA-ANALYSIS` |
| **Licence** | Mendeley Data, V5, distributed for research use |
| **Citation** | Constante, F., Silva, F., Pereira, A. (2019). *DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS*. Mendeley Data, V5. doi:10.17632/8gx2fvg2k6.5 |
| **Acquisition** | Fully automatic |

**Domain:** global supply chain — orders, shipping, profitability, late-delivery risk.

**Measured contents**

| File | Bytes | Rows | Cols |
|---|---:|---:|---:|
| `DataCoSupplyChainDataset.csv` | 95,729,629 | **180,519** | 53 |
| `DescriptionDataCoSupplyChain.csv` | 3,391 | — | — |

sha256 (main file): `994b3c8d24049cc46bf20e8161fedece9a9b95376cc0dc929921d76cf6b9bc0d`

**Measured date coverage** (from actual column values):
- `order date (DateOrders)`: **2015-01-01 → 2017-09-09**
- `shipping date (DateOrders)`: **2016-01-01 → 2017-09-09**

**Why it was selected:** it is the only dataset in the portfolio that carries a
genuine *operations* signal — `Days for shipping (real)` versus
`Days for shipment (scheduled)`, a `Delivery Status` field and a
`Late_delivery_risk` flag. That gives real, non-fabricated material for the
Operations pages, the late-delivery KPI and the operations anomaly detection.

**Known limitations**
- **Column names contain spaces and capitalisation**: `Order Id`,
  `Order Item Total`, `order date (DateOrders)`. Every SQL reference must quote
  them. This is a real ergonomics tax and it is why the SQL layer normalises the
  file into properly named tables.
- It is **denormalised**: one row per order *line*, and the same order repeats.
  `Order Id` is therefore **not** a unique key of the raw file.
- Customer, product and logistics attributes are all stacked in one table. The
  SQL layer has to split them; the raw layer keeps them as published.
- **`Customer Email` and `Customer Password` are the literal masked placeholder
  `XXXXXXXXX`** — one distinct value each. They are dropped: they carry no
  information, and loading a column called `password` into an analytics database
  is a needless liability. `Product Status` is also single-valued and dropped.
- **Correction to a widespread claim:** `Latitude`/`Longitude` are **not** empty
  in this release. Measured: 11,250 and 4,487 distinct values respectively
  (lat −33.94…48.78, lng −158.03…115.26). DataCo therefore *does* support
  geographic analysis with real coordinates. We initially recorded the common
  "they are blank" claim and the real data disproved it.
- **No warehouse table and no supplier table exist** — only a `Market` /
  `Order Region` geography. So we fabricate neither.

---

### C. Olist — Brazilian E-Commerce Public Dataset

| | |
|---|---|
| **Publisher** | Olist (published on Kaggle) |
| **Authoritative page** | https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce |
| **Download URL used** | public GitHub mirror `Ganesh7699/Brazilian-E-Commerce-OList` |
| **Licence** | CC BY-NC-SA 4.0, as stated on the Kaggle dataset page — non-commercial, attribution required |
| **Citation** | Olist. "Brazilian E-Commerce Public Dataset by Olist." Kaggle. https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce (accessed 2026-10-03). |
| **Acquisition** | Fully automatic (from the mirror) |

**Domain:** e-commerce — order lifecycle, items, payments, reviews, freight, geography.

**Measured contents** — this is the only **genuinely relational** dataset in the
portfolio, which is why it is the backbone of the SQL and join work:

| File | Bytes | Rows | Cols | Role |
|---|---:|---:|---:|---|
| `olist_orders_dataset.csv` | 17,654,914 | **99,441** | 8 | fact: order lifecycle |
| `olist_order_items_dataset.csv` | 15,438,671 | **112,650** | 7 | fact: order lines |
| `olist_order_payments_dataset.csv` | 5,777,138 | **103,886** | 5 | fact: payments |
| `olist_order_reviews_dataset.csv` | 14,451,670 | **99,224** | 7 | fact: reviews |
| `olist_customers_dataset.csv` | 9,033,957 | **99,441** | 5 | dimension: customer |
| `olist_products_dataset.csv` | 2,379,446 | **32,951** | 9 | dimension: product |
| `olist_sellers_dataset.csv` | 174,703 | **3,095** | 4 | dimension: seller |
| `product_category_name_translation.csv` | 2,729 | 73 | 2 | dimension: category (PT→EN) |
| `olist_geolocation_dataset.csv` | 61,273,883 | **1,000,163** | 5 | dimension: geo |
| `olist_marketing_qualified_leads_dataset.csv` | 703,836 | 8,000 | 4 | marketing funnel |
| `olist_closed_deals_dataset.csv` | 171,352 | 842 | 14 | marketing funnel |

**Measured date coverage:** `order_purchase_timestamp` **2016-09-04 → 2018-10-17**.

Every row count matches the published dataset exactly (99,441 orders / 112,650
items / 103,886 payments / 99,224 reviews / 1,000,163 geolocation rows). The
mirror is a faithful copy.

**Why it was selected:** it is the only source with real *relational integrity* —
foreign keys that actually connect orders to items to products to sellers to
customers. That makes it the correct place to teach and demonstrate joins, star
schemas, window functions and referential constraints. It also carries genuine
delivery-performance and review signals for the Operations domain.

**Known limitations** (these become real engineering tasks, not excuses)
- `customer_id` is **per-order**, not per-person. Repeat-customer analysis must
  use `customer_unique_id`. Using the wrong one silently collapses every
  customer into a one-time buyer.
- Payments are split across rows (one per payment method), so joining payments to
  items **double counts revenue** unless payments are aggregated per order first.
  This is a genuine, classic modelling trap that the KPI definitions must handle.
- Reviews are not strictly 1:1 with orders; 99,224 reviews against 99,441 orders.
- `olist_products_dataset.csv` contains the **publisher's own typo**:
  `product_name_lenght`, `product_description_lenght`. We keep the typo in the
  **raw** layer so raw stays byte-faithful, and correct it only in processed and
  analytics layers. Raw data is evidence; renaming it would destroy that.
- Geolocation has heavy duplication per zip prefix (~1M rows). It must be
  aggregated before use, never joined row-for-row.
- Aug/Sep 2018 is visibly incomplete at the tail — end-of-period comparisons
  must account for that.

---

### D. Walmart M5 Forecasting — Accuracy

| | |
|---|---|
| **Publisher** | Walmart / Kaggle competition; academic write-up by Makridakis et al. |
| **Authoritative page** | https://www.kaggle.com/competitions/m5-forecasting-accuracy |
| **Download URL used** | Hugging Face mirror `kashif/M5` |
| **Licence** | Released by Walmart for the M5 competition; used here for non-commercial research/portfolio analysis with attribution |
| **Citation** | Makridakis, S., Spiliotis, E., Assimakopoulos, V. (2022). "M5 accuracy competition: Results, findings, and conclusions." *International Journal of Forecasting*, 38(4), 1346–1364. |
| **Acquisition** | Fully automatic |

**Domain:** demand forecasting — daily unit sales across stores, items and states.

**Measured contents**

| File | Bytes | Rows | Cols |
|---|---:|---:|---:|
| `calendar.csv` | 103,469 | **1,969** | 14 |
| `sales_train_evaluation.csv` | 121,736,518 | **30,490** | **1,947** |
| `sell_prices.csv` | 203,395,785 | **6,841,121** | 4 |

**Measured date coverage:** `calendar.date` **2011-01-29 → 2016-06-19** — about
5.4 years of *daily* history, which is what makes credible backtesting possible.

`30,490 items × 1,941 day columns + 6 identifier columns = 1,947` — the column
count checks out arithmetically, confirming the full horizon is present.

**Why it was selected:** forecasting needs long, dense, regular daily series with
real seasonality, calendar events and prices. M5 is the only candidate that
provides all of that at genuine scale. Nothing else in the portfolio has enough
history depth to train and honestly backtest a model.

**Known limitations**
- The sales file is in **wide format** (one column per day). It must be melted to
  long `(item, store, date, sales)` form before it can go anywhere near SQL.
  This is a real transformation step, not a detail.
- `sales` are **integer units, not revenue**. Prices are **weekly**, not daily.
  Any "revenue" figure for M5 is therefore an *estimate* and is labelled as such.
- IDs (`FOODS_1_001`, `CA_1`) are Walmart-specific. They have **no meaning** in
  Olist, DataCo or dunnhumby. Joining them across sources is impossible, not
  merely inadvisable.
- 6.8M price rows need care in memory and in bulk loading.

---

### E. RetailRocket — **evaluated and deliberately NOT selected**

| | |
|---|---|
| **Publisher** | RetailRocket |
| **Page** | https://www.kaggle.com/datasets/retailrocket/ecommerce-dataset |
| **Decision** | **Rejected at the research stage — not downloaded** |

**Why it was rejected.** It is an anonymised clickstream event log (views,
add-to-cart, transactions) with **no product names, no prices, no revenue and no
customer attributes**. It would therefore add no analytical capability this
platform does not already have: e-commerce behaviour is already covered by Olist
with far richer structure (real payments, real reviews, real geography, real
sellers). Adding it would enlarge storage and surface area while adding nothing —
which the project's own selection rule forbids. Recording the rejection is as
important as recording the selections: a dataset portfolio is defined by what
you refuse as much as by what you take.

---

## 3. Acquisition method and honesty statement

Three of the four datasets are published behind an authenticated Kaggle page.
Rather than require a manual login before *anything* could proceed, the pipeline
downloads from **documented public mirrors** and then **proves the data is
correct**:

1. `01_download.py` fetches each file and **refuses to keep it** unless the byte
   count matches the size researched from the publisher.
2. It records the **SHA-256** of every file in `data/raw/_checksums.json`.
3. `02_verify.py` re-hashes every file and re-checks the **columns, row counts and
   date ranges** against `backend/config/expected_schemas.yaml`.

For Olist and M5 this is a strong guarantee: **every row count matched the
published dataset exactly**, so the mirror served the real thing. If a mirror ever
serves a truncated or re-ordered copy, verification fails and the load is blocked.
That is the difference between "we downloaded a file called
`olist_orders_dataset.csv`" and "we downloaded *the* Olist orders dataset".

**Nothing is claimed to be downloaded until it is on disk and hashed.** No file is
overwritten once present; `--force` is the only way, and it is explicit.

---

## 4. Reproducing the acquisition

```bash
# 1. Download everything (skips files already present and correct)
.venv/Scripts/python.exe backend/scripts/data/01_download.py

# 2. Verify columns, row counts, date ranges and checksums
.venv/Scripts/python.exe backend/scripts/data/02_verify.py

# 3. Machine-readable results
cat data/raw/_measurements.json
cat data/raw/_checksums.json
```

Expected final line of step 2: `VERIFICATION RESULT: ALL SOURCES OK`.

---

## 5. File layout on disk

Raw data is stored per source and **never modified in place**:

```
data/
├── raw/
│   ├── _checksums.json        # sha256 + byte count for every downloaded file
│   ├── _measurements.json     # measured columns, rows, date ranges
│   ├── dunnhumby/             # 9 files + user guide PDF
│   ├── dataco/                # 2 files
│   ├── olist/                 # 11 CSV files (+ source zip)
│   └── m5/                    # 3 files
└── processed/                 # cleaned/transformed output, per source
    ├── dunnhumby/
    ├── dataco/
    ├── olist/
    └── m5/
```

Raw files are excluded from git (they are ~1.5 GB and reproducible), but the
checksums and measurements **are** committed — so anyone can verify that the data
behind any published figure is the data we actually used.

---

## 6. How the sources are combined — and how they are NOT

This is the single most important design rule in the project.

```
Dunnhumby customer 123   ≠   Olist customer 123   ≠   DataCo customer 123
```

These are **four unrelated organisations**. Their identifier spaces were minted
independently. `household_key = 123` in dunnhumby and `customer_id = 123` in Olist
describe two different human beings who have never been connected. Any join
between them is not merely "risky" — it is **factually meaningless**, and it would
manufacture a relationship that does not exist.

Therefore:

- **Each source gets its own PostgreSQL schema**: `dunnhumby`, `dataco`, `olist`,
  `m5`. Table names carry their provenance by construction.
- **Cross-source joins on identifiers are forbidden.** Source IDs never cross a
  schema boundary.
- The only permitted cross-source comparisons are of **aggregate business
  measures that are genuinely comparable in kind** — e.g. comparing a *monthly
  revenue trend* from Olist with one from DataCo, each labelled with its source.
  Those live in the `analytics` schema, and **every table there carries a
  `source_system` column**, so a result can never be read without its provenance.
- **"OmniRetail Total Revenue" does not exist** and will never appear in this
  project. Summing revenue across four unrelated companies would be a fabrication.
  The Overview page shows per-source KPIs, never a blended total.
- Concepts absent from the data get **no tables at all**: no warehouses, no
  suppliers, no inventory levels, no store-level M5 inventory.

---

## 7. Domain → source mapping (the platform's spine)

```
OmniRetail Platform
│
├── Customer & Promotion Analytics ..... dunnhumby   (2,595,732 transactions)
├── Supply Chain & Operations .......... DataCo      (180,519 order lines)
├── E-commerce Analytics ............... Olist       (99,441 orders, 11 tables)
└── Demand Forecasting ................. M5          (30,490 series × 1,941 days)
```

---

## 8. Provenance → capability matrix

Which dataset supports which capability, and — just as importantly — which
capabilities the data simply **cannot** support:

| Capability | dunnhumby | DataCo | Olist | M5 |
|---|:--:|:--:|:--:|:--:|
| Transactions / baskets | ✅ 2.6M | order lines | ✅ | daily units |
| Customer identity | household | customer id | ✅ per-order + unique | ✗ |
| Demographics | ✅ subset (801) | segment only | ✗ | ✗ |
| Products | ✅ 92k | ✅ | ✅ 33k | ✅ 30k series |
| Promotions / coupons | ✅ real | ✗ | ✗ | ✗ |
| Campaign contact history | ✅ | ✗ | funnel only | ✗ |
| Payments | ✗ | ✗ | ✅ | ✗ |
| Delivery performance | ✗ | ✅ real vs scheduled | ✅ real timestamps | ✗ |
| Profitability | ✗ | ✅ `Order Profit Per Order` | ✗ | ✗ |
| Reviews / returns | ✗ | ✗ | ✅ reviews | ✗ |
| Geography | ✗ | country/market/region | ✅ city/state + lat/lng | ✅ state |
| Long daily time series | ✗ | ✗ (dates only) | ✗ (2016–18, sparse) | ✅ 5.4 years daily |
| Prices over time | ✗ | ✗ | ✅ per item | ✅ weekly |
| Inventory / warehouse | ✗ | ✗ | ✗ | ✗ |

Two rows matter most. **Inventory and warehouse data exist in none of the four
sources**, so no such tables will be built — the architecture follows the data
rather than the other way round. And **only M5 has the time depth for honest
forecasting**, which is why forecasting is scoped to M5 alone instead of being
spread thinly across four unsuitable sources.

---

## 9. Licences and permitted use — summary

| Dataset | Licence / terms | Commercial use |
|---|---|---|
| dunnhumby Complete Journey | dunnhumby Source Files, for teaching / research / hackathons | See the Source Files terms; this project is non-commercial |
| DataCo SMART Supply Chain | Mendeley Data V5, research use | Verify the current Mendeley licence before any commercial use |
| Olist | CC BY-NC-SA 4.0 | **Non-commercial only**; attribution required; derivatives share-alike |
| M5 | Kaggle competition data, Walmart | Non-commercial research/portfolio use |

**Portfolio statement:** this is a non-commercial learning and portfolio project,
which is consistent with every licence above. Attribution for all four sources is
retained in this document, in `backend/config/datasets.yaml`, and in the Data
Sources page of the frontend.

---

## 10. Data-source decision log

| Decision | Reason |
|---|---|
| Four datasets, not one | No single public dataset covers customer + operations + e-commerce + forecasting |
| RetailRocket rejected | Adds storage, adds no new capability; overlaps Olist |
| Separate schema per source | Prevents meaningless cross-company ID joins by construction |
| `source_system` on every analytics table | A number can never be read without its provenance |
| No blended "total revenue" KPI | Four unrelated companies; a combined total would be fabricated |
| M5 used only for forecasting | Only source with enough time depth |
| dunnhumby used for marketing | Only source with real promotions, coupons and campaign contacts |
| Raw files hashed and frozen | The value we publish must trace to the bytes we analysed |
| No warehouse/supplier/inventory tables | These concepts do not exist in any selected source |
