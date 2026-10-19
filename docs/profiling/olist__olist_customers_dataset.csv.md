# Profiling: `olist/olist_customers_dataset.csv`

- **Size:** 9,033,957 bytes (8.6 MB)
- **Rows:** 99,441
- **Columns:** 5
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `customer_id` | str | 99,441 | 0 | 0.00% | 99,441 | 00012a2ce6f8dcda20d059ce98491703 | ffffe8b65bbe3087b653a978c870db99 |
| 2 | `customer_unique_id` | str | 99,441 | 0 | 0.00% | 96,096 | 0000366f3b9a7992bf8c76cfdf3221e2 | ffffd2657e2aad2907e67c3e9daecbeb |
| 3 | `customer_zip_code_prefix` | int64 | 99,441 | 0 | 0.00% | 14,994 | 1003 | 99990 |
| 4 | `customer_city` | str | 99,441 | 0 | 0.00% | 4,119 | abadia dos dourados | zortea |
| 5 | `customer_state` | str | 99,441 | 0 | 0.00% | 27 | AC | TO |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
