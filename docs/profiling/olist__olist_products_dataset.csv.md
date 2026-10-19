# Profiling: `olist/olist_products_dataset.csv`

- **Size:** 2,379,446 bytes (2.3 MB)
- **Rows:** 32,951
- **Columns:** 9
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `product_id` | str | 32,951 | 0 | 0.00% | 32,951 | 00066f42aeeb9f3007548bb9d3f33c38 | fffe9eeff12fcbd74a2f2b007dde0c58 |
| 2 | `product_category_name` | str | 32,341 | 610 | 1.85% | 73 | agro_industria_e_comercio | utilidades_domesticas |
| 3 | `product_name_lenght` | float64 | 32,341 | 610 | 1.85% | 66 | 5.0 | 76.0 |
| 4 | `product_description_lenght` | float64 | 32,341 | 610 | 1.85% | 2,960 | 4.0 | 3992.0 |
| 5 | `product_photos_qty` | float64 | 32,341 | 610 | 1.85% | 19 | 1.0 | 20.0 |
| 6 | `product_weight_g` | float64 | 32,949 | 2 | 0.01% | 2,204 | 0.0 | 40425.0 |
| 7 | `product_length_cm` | float64 | 32,949 | 2 | 0.01% | 99 | 7.0 | 105.0 |
| 8 | `product_height_cm` | float64 | 32,949 | 2 | 0.01% | 102 | 2.0 | 105.0 |
| 9 | `product_width_cm` | float64 | 32,949 | 2 | 0.01% | 95 | 6.0 | 118.0 |

## Columns with missing values (top 10 by null %)

- `product_category_name`: 610 nulls (1.85%)
- `product_name_lenght`: 610 nulls (1.85%)
- `product_description_lenght`: 610 nulls (1.85%)
- `product_photos_qty`: 610 nulls (1.85%)
- `product_weight_g`: 2 nulls (0.01%)
- `product_length_cm`: 2 nulls (0.01%)
- `product_height_cm`: 2 nulls (0.01%)
- `product_width_cm`: 2 nulls (0.01%)
