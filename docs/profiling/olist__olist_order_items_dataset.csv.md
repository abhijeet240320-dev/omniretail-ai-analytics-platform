# Profiling: `olist/olist_order_items_dataset.csv`

- **Size:** 15,438,671 bytes (14.7 MB)
- **Rows:** 112,650
- **Columns:** 7
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `order_id` | str | 112,650 | 0 | 0.00% | 98,666 | 00010242fe8c5a6d1ba2dd792cb16214 | fffe41c64501cc87c801fd61db3f6244 |
| 2 | `order_item_id` | int64 | 112,650 | 0 | 0.00% | 21 | 1 | 21 |
| 3 | `product_id` | str | 112,650 | 0 | 0.00% | 32,951 | 00066f42aeeb9f3007548bb9d3f33c38 | fffe9eeff12fcbd74a2f2b007dde0c58 |
| 4 | `seller_id` | str | 112,650 | 0 | 0.00% | 3,095 | 0015a82c2db000af6aaaf3ae2ecb0532 | ffff564a4f9085cd26170f4732393726 |
| 5 | `shipping_limit_date` | str | 112,650 | 0 | 0.00% | 93,318 | 2016-09-19 00:15:34 | 2020-04-09 22:35:08 |
| 6 | `price` | float64 | 112,650 | 0 | 0.00% | 5,968 | 0.85 | 6735.0 |
| 7 | `freight_value` | float64 | 112,650 | 0 | 0.00% | 6,999 | 0.0 | 409.68 |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
