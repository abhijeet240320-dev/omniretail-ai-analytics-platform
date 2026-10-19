# Profiling: `olist/olist_order_reviews_dataset.csv`

- **Size:** 14,451,670 bytes (13.8 MB)
- **Rows:** 99,224
- **Columns:** 7
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `review_id` | str | 99,224 | 0 | 0.00% | 98,410 | 0001239bc1de2e33cb583967c2ca4c67 | fffefe7a48d22f7b32046421062219d1 |
| 2 | `order_id` | str | 99,224 | 0 | 0.00% | 98,673 | 00010242fe8c5a6d1ba2dd792cb16214 | fffe41c64501cc87c801fd61db3f6244 |
| 3 | `review_score` | int64 | 99,224 | 0 | 0.00% | 5 | 1 | 5 |
| 4 | `review_comment_title` | str | 11,568 | 87,656 | 88.34% | 4,527 |   | 🔟  |
| 5 | `review_comment_message` | str | 40,977 | 58,247 | 58.70% | 36,159 | 
 | 😡😡😡😡😡👎👎👎👎👎
Empresa sem compromisso com o cliente |
| 6 | `review_creation_date` | str | 99,224 | 0 | 0.00% | 636 | 2016-10-02 00:00:00 | 2018-08-31 00:00:00 |
| 7 | `review_answer_timestamp` | str | 99,224 | 0 | 0.00% | 98,248 | 2016-10-07 18:32:28 | 2018-10-29 12:27:35 |

## Columns with missing values (top 10 by null %)

- `review_comment_title`: 87,656 nulls (88.34%)
- `review_comment_message`: 58,247 nulls (58.70%)
