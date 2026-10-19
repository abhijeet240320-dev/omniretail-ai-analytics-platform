# Profiling: `olist/olist_closed_deals_dataset.csv`

- **Size:** 171,352 bytes (167.3 KB)
- **Rows:** 842
- **Columns:** 14
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `mql_id` | str | 842 | 0 | 0.00% | 842 | 000dd3543ac84d906eae52e7c779bb2a | fff8db9478d2fd72df65a67ee6b62f67 |
| 2 | `seller_id` | str | 842 | 0 | 0.00% | 842 | 00065220becb8785e2cf78355eb9bf68 | ffc470761de7d0232558ba5e786e57b7 |
| 3 | `sdr_id` | str | 842 | 0 | 0.00% | 32 | 068066e24f0c643eb1d089c7dd20cd73 | fdb16d3cbbeb5798f2f66c4096be026d |
| 4 | `sr_id` | str | 842 | 0 | 0.00% | 22 | 060c0a26f19f4d66b42e0d8796688490 | fbf4aef3f6915dc0c3c97d6812522f6a |
| 5 | `won_date` | str | 842 | 0 | 0.00% | 824 | 2017-12-05 02:00:00 | 2018-11-14 18:04:19 |
| 6 | `business_segment` | str | 841 | 1 | 0.12% | 33 | air_conditioning | watches |
| 7 | `lead_type` | str | 836 | 6 | 0.71% | 8 | industry | other |
| 8 | `lead_behaviour_profile` | str | 665 | 177 | 21.02% | 9 | cat | wolf |
| 9 | `has_company` | object | 63 | 779 | 92.52% | 2 | False | True |
| 10 | `has_gtin` | object | 64 | 778 | 92.40% | 2 | False | True |
| 11 | `average_stock` | str | 66 | 776 | 92.16% | 6 | 1-5 | unknown |
| 12 | `business_type` | str | 832 | 10 | 1.19% | 3 | manufacturer | reseller |
| 13 | `declared_product_catalog_size` | float64 | 69 | 773 | 91.81% | 33 | 1.0 | 2000.0 |
| 14 | `declared_monthly_revenue` | float64 | 842 | 0 | 0.00% | 27 | 0.0 | 50000000.0 |

## Columns with missing values (top 10 by null %)

- `has_company`: 779 nulls (92.52%)
- `has_gtin`: 778 nulls (92.40%)
- `average_stock`: 776 nulls (92.16%)
- `declared_product_catalog_size`: 773 nulls (91.81%)
- `lead_behaviour_profile`: 177 nulls (21.02%)
- `business_type`: 10 nulls (1.19%)
- `lead_type`: 6 nulls (0.71%)
- `business_segment`: 1 nulls (0.12%)
