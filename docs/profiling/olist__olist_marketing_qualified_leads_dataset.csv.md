# Profiling: `olist/olist_marketing_qualified_leads_dataset.csv`

- **Size:** 703,836 bytes (687.3 KB)
- **Rows:** 8,000
- **Columns:** 4
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `mql_id` | str | 8,000 | 0 | 0.00% | 8,000 | 0002ac0d783338cfeab0b2bdbd872cda | fffffe98d0963d27015c198262d97221 |
| 2 | `first_contact_date` | str | 8,000 | 0 | 0.00% | 336 | 2017-06-14 | 2018-05-31 |
| 3 | `landing_page_id` | str | 8,000 | 0 | 0.00% | 495 | 007f9098284a86ee80ddeb25d53e0af8 | ffc62d6945fd1423539ec6d96326c37c |
| 4 | `origin` | str | 7,940 | 60 | 0.75% | 10 | direct_traffic | unknown |

## Columns with missing values (top 10 by null %)

- `origin`: 60 nulls (0.75%)
