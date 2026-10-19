# Profiling: `olist/olist_orders_dataset.csv`

- **Size:** 17,654,914 bytes (16.8 MB)
- **Rows:** 99,441
- **Columns:** 8
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `order_id` | str | 99,441 | 0 | 0.00% | 99,441 | 00010242fe8c5a6d1ba2dd792cb16214 | fffe41c64501cc87c801fd61db3f6244 |
| 2 | `customer_id` | str | 99,441 | 0 | 0.00% | 99,441 | 00012a2ce6f8dcda20d059ce98491703 | ffffe8b65bbe3087b653a978c870db99 |
| 3 | `order_status` | str | 99,441 | 0 | 0.00% | 8 | approved | unavailable |
| 4 | `order_purchase_timestamp` | str | 99,441 | 0 | 0.00% | 98,875 | 2016-09-04 21:15:19 | 2018-10-17 17:30:18 |
| 5 | `order_approved_at` | str | 99,281 | 160 | 0.16% | 90,733 | 2016-09-15 12:16:38 | 2018-09-03 17:40:06 |
| 6 | `order_delivered_carrier_date` | str | 97,658 | 1,783 | 1.79% | 81,018 | 2016-10-08 10:34:01 | 2018-09-11 19:48:28 |
| 7 | `order_delivered_customer_date` | str | 96,476 | 2,965 | 2.98% | 95,664 | 2016-10-11 13:46:32 | 2018-10-17 13:22:46 |
| 8 | `order_estimated_delivery_date` | str | 99,441 | 0 | 0.00% | 459 | 2016-09-30 00:00:00 | 2018-11-12 00:00:00 |

## Columns with missing values (top 10 by null %)

- `order_delivered_customer_date`: 2,965 nulls (2.98%)
- `order_delivered_carrier_date`: 1,783 nulls (1.79%)
- `order_approved_at`: 160 nulls (0.16%)
