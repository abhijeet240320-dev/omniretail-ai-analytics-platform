# Profiling: `m5/calendar.csv`

- **Size:** 103,469 bytes (101.0 KB)
- **Rows:** 1,969
- **Columns:** 14
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `date` | str | 1,969 | 0 | 0.00% | 1,969 | 2011-01-29 | 2016-06-19 |
| 2 | `wm_yr_wk` | int64 | 1,969 | 0 | 0.00% | 282 | 11101 | 11621 |
| 3 | `weekday` | str | 1,969 | 0 | 0.00% | 7 | Friday | Wednesday |
| 4 | `wday` | int64 | 1,969 | 0 | 0.00% | 7 | 1 | 7 |
| 5 | `month` | int64 | 1,969 | 0 | 0.00% | 12 | 1 | 12 |
| 6 | `year` | int64 | 1,969 | 0 | 0.00% | 6 | 2011 | 2016 |
| 7 | `d` | str | 1,969 | 0 | 0.00% | 1,969 | d_1 | d_999 |
| 8 | `event_name_1` | str | 162 | 1,807 | 91.77% | 30 | Chanukah End | VeteransDay |
| 9 | `event_type_1` | str | 162 | 1,807 | 91.77% | 4 | Cultural | Sporting |
| 10 | `event_name_2` | str | 5 | 1,964 | 99.75% | 4 | Cinco De Mayo | OrthodoxEaster |
| 11 | `event_type_2` | str | 5 | 1,964 | 99.75% | 2 | Cultural | Religious |
| 12 | `snap_CA` | int64 | 1,969 | 0 | 0.00% | 2 | 0 | 1 |
| 13 | `snap_TX` | int64 | 1,969 | 0 | 0.00% | 2 | 0 | 1 |
| 14 | `snap_WI` | int64 | 1,969 | 0 | 0.00% | 2 | 0 | 1 |

## Columns with missing values (top 10 by null %)

- `event_name_2`: 1,964 nulls (99.75%)
- `event_type_2`: 1,964 nulls (99.75%)
- `event_name_1`: 1,807 nulls (91.77%)
- `event_type_1`: 1,807 nulls (91.77%)
