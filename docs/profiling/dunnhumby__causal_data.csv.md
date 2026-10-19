# Profiling: `dunnhumby/causal_data.csv`

- **Size:** 695,858,427 bytes (663.6 MB)
- **Rows:** 36,786,524
- **Columns:** 5
- **Method:** streamed in chunks
- _Note:_ Profiled in chunks; distinct counts are capped at 50,000 and duplicate-row detection was skipped (it needs the whole frame to be meaningful).

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `PRODUCT_ID` | int64 | 36,786,524 | 0 | 0.00% | >=50,128 | 1000925 | 995242 |
| 2 | `STORE_ID` | int64 | 36,786,524 | 0 | 0.00% | 115 | 286 | 34280 |
| 3 | `WEEK_NO` | int64 | 36,786,524 | 0 | 0.00% | 93 | 11 | 101 |
| 4 | `display` | str | 36,786,524 | 0 | 0.00% | 10 | 0 | A |
| 5 | `mailer` | str | 36,786,524 | 0 | 0.00% | 11 | 0 | Z |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
