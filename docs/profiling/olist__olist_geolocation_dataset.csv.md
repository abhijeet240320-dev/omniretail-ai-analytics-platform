# Profiling: `olist/olist_geolocation_dataset.csv`

- **Size:** 61,273,883 bytes (58.4 MB)
- **Rows:** 1,000,163
- **Columns:** 5
- **Exact duplicate rows:** 261,831 (26.18% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `geolocation_zip_code_prefix` | int64 | 1,000,163 | 0 | 0.00% | 19,015 | 1001 | 99990 |
| 2 | `geolocation_lat` | float64 | 1,000,163 | 0 | 0.00% | 717,360 | -36.6053744107061 | 45.06593318269697 |
| 3 | `geolocation_lng` | float64 | 1,000,163 | 0 | 0.00% | 717,613 | -101.46676644931476 | 121.10539381057764 |
| 4 | `geolocation_city` | str | 1,000,163 | 0 | 0.00% | 8,011 | * cidade | óleo |
| 5 | `geolocation_state` | str | 1,000,163 | 0 | 0.00% | 27 | AC | TO |

## Columns with missing values (top 10 by null %)

None - every column is fully populated.
