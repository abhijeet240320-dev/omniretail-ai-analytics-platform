# The Power BI data model

## The one decision everything else follows from

**There is one star schema with four independent fact tables, and no
relationship connects them.**

That is not a modelling shortcut. It is the shape the data actually has:

| Schema | Source | Tables | Rows |
|---|---|---:|---:|
| `olist` | Brazilian marketplace | 12 | 774,081 |
| `dataco` | Supply chain | 4 | 267,041 |
| `dunnhumby` | US grocery panel | 9 | 39,606,850 |
| `m5` | Walmart daily sales | 5 | 66,056,611 |

Four companies. No shared customer, no shared order, no shared product
identifier with a verified mapping, and **no foreign key anywhere in this
database that crosses a schema boundary**. That last point is enforced by
`test_cross_source_joins_are_impossible_in_the_data` in
`backend/tests/test_ai_grounding_contract.py`, which walks the SQLAlchemy
metadata and fails if any FK points at another schema.

A star schema needs a conformed dimension. There isn't one here, so the model
uses a single `Source` dimension that every fact table relates to and **nothing
else relates to anything across sources**.

## Why not one big table with a `source` column

A single fact table with a source discriminator would let a slicer select
`olist` and `dunnhumby` at once, and a measure would then sum both. That
number would be a figure with no referent -- four companies, four accounting
systems, four different definitions of a sale. Olist counts `payment_total`,
M5 counts `sales` dollars per item per day, DataCo counts `Sales` per order
line.

The model refuses to make that mistake easy:

- Each fact table keeps its own measures. There is no `Total Revenue` measure
  that spans sources, and there will not be one added.
- `Source Count` in [measures.dax](measures.dax) reports how many sources are in
  the current filter context.
- `Cross Source Rows` returns `BLANK` rather than a number when evaluated
  outside its own source, so a report cannot accidentally place an Olist
  figure under a heading that implies otherwise.

## Tables and relationships

```
                         +----------------+
                         |    Source      |
                         |----------------|
                         | SourceKey      |
                         | SourceName     |  olist / dataco / dunnhumby / m5
                         | SourceRows     |
                         | SourceNote     |
                         +----------------+
                            ^     ^     ^     ^
                            |     |     |     |
        +-------------------+     |     |     +------------------+
        |                          |     |                        |
  +--------------+          +-------------+            +------------------+
  | olist_orders |          | dataco_*    |            | dunnhumby_*      |
  +--------------+          +-------------+            + m5_*             |
        |                                                            |
        +--> olist_order_items, olist_payments, olist_reviews,        |
             olist_customers, olist_products, olist_sellers ...       |
                                                                     |
                                                    each fact relates ONLY to Source
```

Every relationship is **single-direction, single-valued, from the fact to
`Source`**. No fact table relates to any other fact table.

### Why the relationships to `Source` and not a conformed dimension

A conformed `Date` dimension *would* be legitimate -- all four sources have a
real date axis except dunnhumby, whose axis is an integer `day` index with no
calendar behind it. Sharing a Date dimension across sources invites exactly the
comparison the project forbids, so `DateKey` is per-source and never shared.

dunnhumby's axis deserves its own note: `day` runs 1..711 and is **not** a
date. Nothing in the source maps it to a calendar. The model exposes it as
`DayIndex` and never renders it as a date, because inventing one would put a
timestamp in a report that the dataset never contained.

## Connection

Power BI connects to PostgreSQL through **`omniretail_readonly`**, which holds
`SELECT` and nothing else, with `default_transaction_read_only = on` at role
level. Verified at runtime by `/api/status`, which asks PostgreSQL what the
role actually holds rather than trusting the bootstrap script.

The credentials belong in Power BI's parameter, never in this repository:

- Windows: `File > Options and settings > DataSource settings > Data Source
  Settings > Edit permissions > Credentials > Database > Edit > Store securely`
- Store them in the Windows Credential Manager, not in the `.pbix`, which is
  why `.pbix` files are not committed here.

## What is not in this folder, and why

`.pbix` is a binary zip with an unpinnable format; two files that differ by one
DAX expression look identical in a diff and neither can be reviewed. So the
reviewable artefacts are:

- [measures.dax](measures.dax) -- every measure, as text, each with the reason
  it is written that way
- [data_model.md](data_model.md) -- this file

There is no `screenshots/` folder here either, for the same reason: an image of a
chart nobody rendered is an illustration, not evidence. See
[README.md](README.md) for what would have to exist first.

## Import vs DirectQuery

**Import mode**, for three reasons:

1. `m5.sales_long` is 59,181,090 rows and `dunnhumby.promo_exposure` is
   36.8 million. DirectQuery over either would time out on a pivot.
2. Import gives the measures a fast, consistent read. The figures are already
   verified by the SQL in `backend/sql/`; re-deriving them in DAX against a
   live table reintroduces exactly the grain bugs the measures document.
3. A refresh is explicit and reviewable. It re-runs
   `04_load.py` and `run_analytics.py`, which produce
   `_load_report.json` and `_sql_results.json` -- files the test suite checks
   the documentation against. DirectQuery has no equivalent checkpoint.

The cost is stated rather than hidden: import goes stale if the database is
reloaded without a refresh. The documented mitigation is that the documented
figures come from the JSON reports, not from the Power BI model, so a stale
import cannot make a documented number wrong -- only the dashboard out of date,
which its own refresh timestamp shows.