# Profiling: `olist/olist_order_payments_dataset.csv`

- **Size:** 5,777,138 bytes (5.5 MB)
- **Rows:** 103,886
- **Columns:** 5
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `order_id` | str | 103,886 | 0 | 0.00% | 99,440 | 00010242fe8c5a6d1ba2dd792cb16214 | fffe41c64501cc87c801fd61db3f6244 |
| 2 | `payment_sequential` | int64 | 103,886 | 0 | 0.00% | 29 | 1 | 29 |
| 3 | `payment_type` | str | 103,886 | 0 | 0.00% | 5 | boleto | voucher |
| 4 | `payment_installments` | int64 | 103,886 | 0 | 0.00% | 24 | 0 | 24 |
| 5 | `payment_value` | float64 | 103,886 | 0 | 0.00% | 29,077 | 0.0 | 13664.08 |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
