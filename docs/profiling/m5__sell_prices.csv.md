# Profiling: `m5/sell_prices.csv`

- **Size:** 203,395,785 bytes (194.0 MB)
- **Rows:** 6,841,121
- **Columns:** 4
- **Method:** streamed in chunks
- _Note:_ Profiled in chunks; distinct counts are capped at 50,000 and duplicate-row detection was skipped (it needs the whole frame to be meaningful).

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `store_id` | str | 6,841,121 | 0 | 0.00% | 10 | CA_1 | WI_3 |
| 2 | `item_id` | str | 6,841,121 | 0 | 0.00% | 3,049 | FOODS_1_001 | HOUSEHOLD_2_516 |
| 3 | `wm_yr_wk` | int64 | 6,841,121 | 0 | 0.00% | 282 | 11101 | 11621 |
| 4 | `sell_price` | float64 | 6,841,121 | 0 | 0.00% | 1,048 | 0.01 | 9.98 |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
