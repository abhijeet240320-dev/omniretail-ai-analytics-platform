# Profiling: `dataco/DataCoSupplyChainDataset.csv`

- **Size:** 95,729,629 bytes (91.3 MB)
- **Rows:** 180,519
- **Columns:** 53
- **Exact duplicate rows:** 0 (0.00% of rows)
- **Method:** read whole

## Columns

| # | Column | dtype | non-null | nulls | null % | distinct | min | max |
|---:|---|---|---:|---:|---:|---:|---|---|
| 1 | `Type` | str | 180,519 | 0 | 0.00% | 4 | CASH | TRANSFER |
| 2 | `Days for shipping (real)` | int64 | 180,519 | 0 | 0.00% | 7 | 0 | 6 |
| 3 | `Days for shipment (scheduled)` | int64 | 180,519 | 0 | 0.00% | 4 | 0 | 4 |
| 4 | `Benefit per order` | float64 | 180,519 | 0 | 0.00% | 21,998 | -4274.97998 | 911.7999878 |
| 5 | `Sales per customer` | float64 | 180,519 | 0 | 0.00% | 2,927 | 7.489999771 | 1939.98999 |
| 6 | `Delivery Status` | str | 180,519 | 0 | 0.00% | 4 | Advance shipping | Shipping on time |
| 7 | `Late_delivery_risk` | int64 | 180,519 | 0 | 0.00% | 2 | 0 | 1 |
| 8 | `Category Id` | int64 | 180,519 | 0 | 0.00% | 51 | 2 | 76 |
| 9 | `Category Name` | str | 180,519 | 0 | 0.00% | 50 | Accessories | Women's Golf Clubs |
| 10 | `Customer City` | str | 180,519 | 0 | 0.00% | 563 | Aguadilla | Zanesville |
| 11 | `Customer Country` | str | 180,519 | 0 | 0.00% | 2 | EE. UU. | Puerto Rico |
| 12 | `Customer Email` | str | 180,519 | 0 | 0.00% | 1 | XXXXXXXXX | XXXXXXXXX |
| 13 | `Customer Fname` | str | 180,519 | 0 | 0.00% | 782 | Aaron | Zorita |
| 14 | `Customer Id` | int64 | 180,519 | 0 | 0.00% | 20,652 | 1 | 20757 |
| 15 | `Customer Lname` | str | 180,511 | 8 | 0.00% | 1,109 | Abbott | Zuniga |
| 16 | `Customer Password` | str | 180,519 | 0 | 0.00% | 1 | XXXXXXXXX | XXXXXXXXX |
| 17 | `Customer Segment` | str | 180,519 | 0 | 0.00% | 3 | Consumer | Home Office |
| 18 | `Customer State` | str | 180,519 | 0 | 0.00% | 46 | 91732 | WV |
| 19 | `Customer Street` | str | 180,519 | 0 | 0.00% | 7,458 | 1 Bright Manor | Elk Grove |
| 20 | `Customer Zipcode` | float64 | 180,516 | 3 | 0.00% | 995 | 603.0 | 99205.0 |
| 21 | `Department Id` | int64 | 180,519 | 0 | 0.00% | 11 | 2 | 12 |
| 22 | `Department Name` | str | 180,519 | 0 | 0.00% | 11 | Apparel | Technology |
| 23 | `Latitude` | float64 | 180,519 | 0 | 0.00% | 11,250 | -33.93755341 | 48.78193283 |
| 24 | `Longitude` | float64 | 180,519 | 0 | 0.00% | 4,487 | -158.0259857 | 115.2630768 |
| 25 | `Market` | str | 180,519 | 0 | 0.00% | 5 | Africa | USCA |
| 26 | `Order City` | str | 180,519 | 0 | 0.00% | 3,597 | Aachen | Ústí nad Labem |
| 27 | `Order Country` | str | 180,519 | 0 | 0.00% | 164 | Afganistán | Zimbabue |
| 28 | `Order Customer Id` | int64 | 180,519 | 0 | 0.00% | 20,652 | 1 | 20757 |
| 29 | `order date (DateOrders)` | str | 180,519 | 0 | 0.00% | 65,752 | 1/1/2015 0:00 | 9/9/2017 9:50 |
| 30 | `Order Id` | int64 | 180,519 | 0 | 0.00% | 65,752 | 1 | 77204 |
| 31 | `Order Item Cardprod Id` | int64 | 180,519 | 0 | 0.00% | 118 | 19 | 1363 |
| 32 | `Order Item Discount` | float64 | 180,519 | 0 | 0.00% | 1,017 | 0.0 | 500.0 |
| 33 | `Order Item Discount Rate` | float64 | 180,519 | 0 | 0.00% | 18 | 0.0 | 0.25 |
| 34 | `Order Item Id` | int64 | 180,519 | 0 | 0.00% | 180,519 | 1 | 180519 |
| 35 | `Order Item Product Price` | float64 | 180,519 | 0 | 0.00% | 75 | 9.989999771 | 1999.98999 |
| 36 | `Order Item Profit Ratio` | float64 | 180,519 | 0 | 0.00% | 162 | -2.75 | 0.5 |
| 37 | `Order Item Quantity` | int64 | 180,519 | 0 | 0.00% | 5 | 1 | 5 |
| 38 | `Sales` | float64 | 180,519 | 0 | 0.00% | 193 | 9.989999771 | 1999.98999 |
| 39 | `Order Item Total` | float64 | 180,519 | 0 | 0.00% | 2,927 | 7.489999771 | 1939.98999 |
| 40 | `Order Profit Per Order` | float64 | 180,519 | 0 | 0.00% | 21,998 | -4274.97998 | 911.7999878 |
| 41 | `Order Region` | str | 180,519 | 0 | 0.00% | 23 | Canada | Western Europe |
| 42 | `Order State` | str | 180,519 | 0 | 0.00% | 1,089 | Abia | Ústí nad Labem |
| 43 | `Order Status` | str | 180,519 | 0 | 0.00% | 9 | CANCELED | SUSPECTED_FRAUD |
| 44 | `Order Zipcode` | float64 | 24,840 | 155,679 | 86.24% | 609 | 1040.0 | 99301.0 |
| 45 | `Product Card Id` | int64 | 180,519 | 0 | 0.00% | 118 | 19 | 1363 |
| 46 | `Product Category Id` | int64 | 180,519 | 0 | 0.00% | 51 | 2 | 76 |
| 47 | `Product Description` | float64 | 0 | 180,519 | 100.00% | 0 | - | - |
| 48 | `Product Image` | str | 180,519 | 0 | 0.00% | 118 | http://images.acmesports.sports/Adult+dog+supplies | http://images.acmesports.sports/insta-bed+Neverflat+Air+Mattress |
| 49 | `Product Name` | str | 180,519 | 0 | 0.00% | 118 | Adult dog supplies | insta-bed Neverflat Air Mattress |
| 50 | `Product Price` | float64 | 180,519 | 0 | 0.00% | 75 | 9.989999771 | 1999.98999 |
| 51 | `Product Status` | int64 | 180,519 | 0 | 0.00% | 1 | 0 | 0 |
| 52 | `shipping date (DateOrders)` | str | 180,519 | 0 | 0.00% | 63,701 | 1/1/2016 0:22 | 9/9/2017 9:08 |
| 53 | `Shipping Mode` | str | 180,519 | 0 | 0.00% | 4 | First Class | Standard Class |

## Columns with missing values (top 10 by null %)

- `Product Description`: 180,519 nulls (100.00%)
- `Order Zipcode`: 155,679 nulls (86.24%)
- `Customer Lname`: 8 nulls (0.00%)
- `Customer Zipcode`: 3 nulls (0.00%)
