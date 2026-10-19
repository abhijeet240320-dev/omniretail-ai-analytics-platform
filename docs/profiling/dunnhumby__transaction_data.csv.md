# Profiling: `dunnhumby/transaction_data.csv`

- **Size:** 141,742,346 bytes (135.2 MB)
- **Rows:** 2,595,732
- **Columns:** 12
- **Method:** streamed in chunks
- _Note:_ Profiled in chunks; distinct counts are capped at 50,000 and duplicate-row detection was skipped (it needs the whole frame to be meaningful).

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `household_key` | int64 | 2,595,732 | 0 | 0.00% | 2,500 | 1 | 2500 |
| 2 | `BASKET_ID` | int64 | 2,595,732 | 0 | 0.00% | >=55,855 | 26984851472 | 42305362535 |
| 3 | `DAY` | int64 | 2,595,732 | 0 | 0.00% | 711 | 1 | 711 |
| 4 | `PRODUCT_ID` | int64 | 2,595,732 | 0 | 0.00% | >=53,185 | 25671 | 18316298 |
| 5 | `QUANTITY` | int64 | 2,595,732 | 0 | 0.00% | 11,746 | 0 | 89638 |
| 6 | `SALES_VALUE` | float64 | 2,595,732 | 0 | 0.00% | 5,502 | 0.0 | 840.0 |
| 7 | `STORE_ID` | int64 | 2,595,732 | 0 | 0.00% | 582 | 1 | 34280 |
| 8 | `RETAIL_DISC` | float64 | 2,595,732 | 0 | 0.00% | 2,414 | -129.98 | 6.938894e-18 |
| 9 | `TRANS_TIME` | int64 | 2,595,732 | 0 | 0.00% | 1,440 | 0 | 2359 |
| 10 | `WEEK_NO` | int64 | 2,595,732 | 0 | 0.00% | 102 | 1 | 99 |
| 11 | `COUPON_DISC` | float64 | 2,595,732 | 0 | 0.00% | 415 | -16.98 | 0.0 |
| 12 | `COUPON_MATCH_DISC` | float64 | 2,595,732 | 0 | 0.00% | 80 | -1.4 | 0.0 |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
