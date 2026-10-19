# SQL

Hand-written SQL lives here. Every file is a set of **named queries** that
answer a stated business question against one source.

## Why the folders are per SOURCE, not per business area

An earlier version of this README planned `sales/`, `customers/`, `products/`,
`inventory/` and `operations/`. That layout was wrong for this project, and
wrong in a way that mattered:

- **`inventory/` and `operations/` had no data behind them.** No dataset here
  contains stock levels, suppliers, warehouses or purchase orders. Writing
  queries against them would have meant inventing tables. Nothing in this repo
  may be fabricated, so those folders were deleted rather than filled with
  invented data.
- **A `products/` folder would have invited the exact mistake this project
  exists to avoid.** It is natural to put "olist product analysis" and "dataco
  product analysis" in the same folder, and then to join them. That join would
  be meaningless: Olist product `abc` and DataCo product card `7` are unrelated
  SKUs at unrelated companies, and dunnhumby household 123 is neither Olist
  customer 123 nor DataCo customer 123.

So the folders follow the data:

| Folder      | Source                          | Domain             | Tables |
|-------------|---------------------------------|--------------------|-------:|
| `olist/`    | Brazilian E-Commerce (Olist)    | ECOMMERCE          |     12 |
| `dataco/`   | Supply Chain Dataset (DataCo)   | SUPPLY CHAIN       |      4 |
| `dunnhumby/`| Dunn & Bradstreet Complete Journey | CPG / Retail    |      9 |
| `m5/`       | Walmart Selling Competition    | DEMAND FORECASTING |      5 |
| `00_bootstrap/` | roles, database, grants     | infrastructure     |      - |

**No query in any folder reads from another source's schema.** The single
meeting point is the `analytics` schema, and everything written there carries
`source_system` so a row from one company can never be silently pooled with a
row from another.

## File and query conventions

- `NN_topic.sql`, where `NN` is the order we want to read them in.
- Each query starts with a marker and a plain-language question:

```sql
-- [query: monthly_revenue_trend]
-- How did order volume and revenue move month by month?
SELECT ...
```

- One SQL statement batch per query, so a failure is attributable to a named
  question rather than to a file.

## Running them

```bash
.venv/Scripts/python.exe backend/scripts/sql/run_analytics.py
.venv/Scripts/python.exe backend/scripts/sql/run_analytics.py --source m5
.venv/Scripts/python.exe backend/scripts/sql/run_analytics.py --only revenue_x
```

The runner connects with the session pinned **read-only**, executes every query,
and writes the real result rows to `data/processed/_sql_results.json`. A query
that fails is reported and excluded from the generated documentation, so a
number in `docs/sql_analytics.md` always came back from the database.

```bash
.venv/Scripts/python.exe backend/scripts/docs/generate_sql_analytics.py
```

The written interpretation lives in `backend/config/sql_findings.yaml`, keyed by
`<source>.<query_name>`. The generator refuses to write the document if any
figure quoted in that prose is not present in that query's result rows, so the
findings cannot drift away from the data.

Two habits fall out of this. Ratios are computed in SQL rather than in the
sentence, so nothing has to be recalculated by hand; and a number that no query
returns has to earn its place by making the query return it first.

## Rules the queries follow

1. **Compute ratios in SQL.** Every percentage in the documentation is produced
   by a `round(100.0 * ... / ..., 2)` in the query itself, so no figure is
   recalculated by hand and none can drift from the rows.
2. **Say which money column is meant.** Olist has `payment_total`; DataCo has
   both `order_value` (goods + freight) and `order_sales` (goods, net of
   returns); dunnhumby has `gross_sales_value` (before discount) and
   `sales_value` (after). Silently picking one overstates the business.
3. **Guard the join that fans out.** Several of these tables are one-to-many
   against the key you would naturally reach for - `olist.reviews` to
   `olist.orders`, `dunnhumby.coupons` to `dunnhumby.coupon_redemptions` by
   `coupon_upc`. Queries that cross those use `count(DISTINCT ...)` or
   pre-aggregate, and the reason is commented at the query.
4. **Never delete or round away a row to make a number look better.**