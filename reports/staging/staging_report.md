# Staging Report

- **Run ID:** `2274f9e7fdff`
- **Executed (UTC):** 2026-11-01T15:09:34+00:00
- **Git commit:** `6e88bd113a3e0560e9fdf17fb3ec2c752b8bee58` (working tree has uncommitted changes)
- **Raw directory:** `data/raw` · **Output directory:** `data/staging`
- **Baseline checksums verified:** True
- **Outputs written:** True · **Success:** True

## Validation summary

| Class | Total | Passed | Failed |
|---|---:|---:|---:|
| blocking | 76 | 76 | 0 |
| regression | 41 | 41 | 0 |
| warning | 3 | 0 | 0 |

**Failed checks:** none

**Warnings (logged values):**

- `W02` distinct (zip, lat, lng) points flagged outside Brazil (not measured in NB01-03): **33**
- `W.geo_points_multi_city` points carried by more than one city spelling (explains part of the 18,178 gap): **18133**
- `W.geo_points_multi_state` points carried by more than one state: **2**

## Outputs

| Output | Kind | Source | Rows in | Rows out | Columns | Missing cells |
|---|---|---|---:|---:|---:|---:|
| `customers` | staged | customers | 99,441 | 99,441 | 5 | 0 |
| `geolocation` | staged | geolocation | 1,000,163 | 1,000,163 | 5 | 0 |
| `orders` | staged | orders | 99,441 | 99,441 | 8 | 4,908 |
| `order_items` | staged | order_items | 112,650 | 112,650 | 7 | 0 |
| `payments` | staged | payments | 103,886 | 103,886 | 5 | 0 |
| `reviews` | staged | reviews | 99,224 | 99,224 | 7 | 145,903 |
| `products` | staged | products | 32,951 | 32,951 | 9 | 2,448 |
| `sellers` | staged | sellers | 3,095 | 3,095 | 4 | 0 |
| `translation` | staged | translation | 71 | 71 | 2 | 0 |
| `geolocation_rows` | derived | geolocation | 1,000,163 | 738,332 | 6 | 0 |
| `geolocation_points` | derived | geolocation | 738,332 | 720,154 | 8 | 0 |
| `products_metadata_flags` | derived | products | 32,951 | 32,951 | 12 | 0 |

### `customers`

Source file(s): `olist_customers_dataset.csv` · Path: `data/staging/customers.parquet`

Transformations:

- customer_state: string -> category (27 observed values)

| Column | dtype | Missing |
|---|---|---:|
| `customer_id` | string | 0 |
| `customer_unique_id` | string | 0 |
| `customer_zip_code_prefix` | string | 0 |
| `customer_city` | string | 0 |
| `customer_state` | category | 0 |

### `geolocation`

Source file(s): `olist_geolocation_dataset.csv` · Path: `data/staging/geolocation.parquet`

Transformations:

- geolocation_state: string -> category (27 observed values)

| Column | dtype | Missing |
|---|---|---:|
| `geolocation_zip_code_prefix` | string | 0 |
| `geolocation_lat` | float64 | 0 |
| `geolocation_lng` | float64 | 0 |
| `geolocation_city` | string | 0 |
| `geolocation_state` | category | 0 |

### `orders`

Source file(s): `olist_orders_dataset.csv` · Path: `data/staging/orders.parquet`

Transformations:

- order_purchase_timestamp: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)
- order_approved_at: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)
- order_delivered_carrier_date: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)
- order_delivered_customer_date: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)
- order_estimated_delivery_date: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)
- order_status: string -> category (8 observed values)

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `customer_id` | string | 0 |
| `order_status` | category | 0 |
| `order_purchase_timestamp` | datetime64[us] | 0 |
| `order_approved_at` | datetime64[us] | 160 |
| `order_delivered_carrier_date` | datetime64[us] | 1,783 |
| `order_delivered_customer_date` | datetime64[us] | 2,965 |
| `order_estimated_delivery_date` | datetime64[us] | 0 |

### `order_items`

Source file(s): `olist_order_items_dataset.csv` · Path: `data/staging/order_items.parquet`

Transformations:

- shipping_limit_date: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `order_item_id` | Int64 | 0 |
| `product_id` | string | 0 |
| `seller_id` | string | 0 |
| `shipping_limit_date` | datetime64[us] | 0 |
| `price` | float64 | 0 |
| `freight_value` | float64 | 0 |

### `payments`

Source file(s): `olist_order_payments_dataset.csv` · Path: `data/staging/payments.parquet`

Transformations:

- payment_type: string -> category (5 observed values)

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `payment_sequential` | Int64 | 0 |
| `payment_type` | category | 0 |
| `payment_installments` | Int64 | 0 |
| `payment_value` | float64 | 0 |

### `reviews`

Source file(s): `olist_order_reviews_dataset.csv` · Path: `data/staging/reviews.parquet`

Transformations:

- review_creation_date: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)
- review_answer_timestamp: text -> datetime64[us] (format '%Y-%m-%d %H:%M:%S', timezone-naive)

| Column | dtype | Missing |
|---|---|---:|
| `review_id` | string | 0 |
| `order_id` | string | 0 |
| `review_score` | Int64 | 0 |
| `review_comment_title` | string | 87,656 |
| `review_comment_message` | string | 58,247 |
| `review_creation_date` | datetime64[us] | 0 |
| `review_answer_timestamp` | datetime64[us] | 0 |

### `products`

Source file(s): `olist_products_dataset.csv` · Path: `data/staging/products.parquet`

Transformations:

- renamed product_name_lenght -> product_name_length (strategy D-05)
- renamed product_description_lenght -> product_description_length (strategy D-05)

| Column | dtype | Missing |
|---|---|---:|
| `product_id` | string | 0 |
| `product_category_name` | string | 610 |
| `product_name_length` | Int64 | 610 |
| `product_description_length` | Int64 | 610 |
| `product_photos_qty` | Int64 | 610 |
| `product_weight_g` | Int64 | 2 |
| `product_length_cm` | Int64 | 2 |
| `product_height_cm` | Int64 | 2 |
| `product_width_cm` | Int64 | 2 |

### `sellers`

Source file(s): `olist_sellers_dataset.csv` · Path: `data/staging/sellers.parquet`

Transformations:

- seller_state: string -> category (23 observed values)

| Column | dtype | Missing |
|---|---|---:|
| `seller_id` | string | 0 |
| `seller_zip_code_prefix` | string | 0 |
| `seller_city` | string | 0 |
| `seller_state` | category | 0 |

### `translation`

Source file(s): `product_category_name_translation.csv` · Path: `data/staging/translation.parquet`

Transformations:

- no structural change (types already final from ingestion)

| Column | dtype | Missing |
|---|---|---:|
| `product_category_name` | string | 0 |
| `product_category_name_english` | string | 0 |

### `geolocation_rows`

Source file(s): `olist_geolocation_dataset.csv` · Path: `data/staging/geolocation_rows.parquet`

Transformations:

- one row per distinct full row (zip, lat, lng, city, state)
- occurrence_count = number of identical raw rows (lossless; strategy D-10)

| Column | dtype | Missing |
|---|---|---:|
| `geolocation_zip_code_prefix` | string | 0 |
| `geolocation_lat` | float64 | 0 |
| `geolocation_lng` | float64 | 0 |
| `geolocation_city` | string | 0 |
| `geolocation_state` | category | 0 |
| `occurrence_count` | int64 | 0 |

### `geolocation_points`

Source file(s): `olist_geolocation_dataset.csv` · Path: `data/staging/geolocation_points.parquet`

Transformations:

- one row per distinct (zip, lat, lng), built from geolocation_rows
- raw_row_count = sum of occurrence_count; n_full_rows, n_city_spellings, n_states
- dq_outside_brazil: lat outside [-33.75, 5.27] or lng outside [-73.99, -34.79]
- no ZIP centroid computed (deferred)

| Column | dtype | Missing |
|---|---|---:|
| `geolocation_zip_code_prefix` | string | 0 |
| `geolocation_lat` | float64 | 0 |
| `geolocation_lng` | float64 | 0 |
| `raw_row_count` | int64 | 0 |
| `n_full_rows` | int64 | 0 |
| `n_city_spellings` | int64 | 0 |
| `n_states` | int64 | 0 |
| `dq_outside_brazil` | bool | 0 |

### `products_metadata_flags`

Source file(s): `olist_products_dataset.csv` · Path: `data/staging/products_metadata_flags.parquet`

Transformations:

- 8 field-level flags (value missing) and 3 row-level flags (all fields of the group missing), as defined in strategy section 4.3

| Column | dtype | Missing |
|---|---|---:|
| `product_id` | string | 0 |
| `dq_missing_category` | bool | 0 |
| `dq_missing_name_length` | bool | 0 |
| `dq_missing_description_length` | bool | 0 |
| `dq_missing_photos_qty` | bool | 0 |
| `dq_missing_weight_g` | bool | 0 |
| `dq_missing_length_cm` | bool | 0 |
| `dq_missing_height_cm` | bool | 0 |
| `dq_missing_width_cm` | bool | 0 |
| `dq_listing_metadata_missing` | bool | 0 |
| `dq_physical_attributes_missing` | bool | 0 |
| `dq_all_attributes_missing` | bool | 0 |

## All checks

| ID | Class | Description | Expected | Observed | Result |
|---|---|---|---|---|---|
| `V01` | blocking | raw files unchanged during ingestion (SHA-256 before = after); baseline verified | `True` | `True` | PASS |
| `V02.customers` | blocking | customers: staged rows = ingested rows | `99441` | `99441` | PASS |
| `V03.customers` | blocking | customers: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.customers` | blocking | customers: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.customers` | blocking | customers: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.customers` | blocking | customers: primary key ('customer_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.geolocation` | blocking | geolocation: staged rows = ingested rows | `1000163` | `1000163` | PASS |
| `V03.geolocation` | blocking | geolocation: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.geolocation` | blocking | geolocation: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.geolocation` | blocking | geolocation: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V02.orders` | blocking | orders: staged rows = ingested rows | `99441` | `99441` | PASS |
| `V03.orders` | blocking | orders: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.orders` | blocking | orders: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.orders` | blocking | orders: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.orders` | blocking | orders: primary key ('order_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.order_items` | blocking | order_items: staged rows = ingested rows | `112650` | `112650` | PASS |
| `V03.order_items` | blocking | order_items: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.order_items` | blocking | order_items: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.order_items` | blocking | order_items: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.order_items` | blocking | order_items: primary key ('order_id', 'order_item_id') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.payments` | blocking | payments: staged rows = ingested rows | `103886` | `103886` | PASS |
| `V03.payments` | blocking | payments: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.payments` | blocking | payments: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.payments` | blocking | payments: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.payments` | blocking | payments: primary key ('order_id', 'payment_sequential') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.reviews` | blocking | reviews: staged rows = ingested rows | `99224` | `99224` | PASS |
| `V03.reviews` | blocking | reviews: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.reviews` | blocking | reviews: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.reviews` | blocking | reviews: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.reviews` | blocking | reviews: primary key ('review_id', 'order_id') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.products` | blocking | products: staged rows = ingested rows | `32951` | `32951` | PASS |
| `V03.products` | blocking | products: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.products` | blocking | products: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.products` | blocking | products: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.products` | blocking | products: primary key ('product_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.sellers` | blocking | sellers: staged rows = ingested rows | `3095` | `3095` | PASS |
| `V03.sellers` | blocking | sellers: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.sellers` | blocking | sellers: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.sellers` | blocking | sellers: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.sellers` | blocking | sellers: primary key ('seller_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V02.translation` | blocking | translation: staged rows = ingested rows | `71` | `71` | PASS |
| `V03.translation` | blocking | translation: staged columns and dtypes match the staging schema | `[]` | `[]` | PASS |
| `V08.translation` | blocking | translation: missing count per column unchanged | `{}` | `{}` | PASS |
| `V14.translation` | blocking | translation: every value unchanged (dates re-formatted with the parse format equal the raw text) | `[]` | `[]` | PASS |
| `V04.translation` | blocking | translation: primary key ('product_category_name',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V07.orders` | blocking | orders: 0 date values failed to parse | `{'order_purchase_timestamp': 0, 'order_approved_at': 0, 'order_delivered_carrier_date': 0, 'order_delivered_customer_date': 0, 'order_estimated_delivery_date': 0}` | `{'order_purchase_timestamp': 0, 'order_approved_at': 0, 'order_delivered_carrier_date': 0, 'order_delivered_customer_date': 0, 'order_estimated_delivery_date': 0}` | PASS |
| `V07.order_items` | blocking | order_items: 0 date values failed to parse | `{'shipping_limit_date': 0}` | `{'shipping_limit_date': 0}` | PASS |
| `V07.reviews` | blocking | reviews: 0 date values failed to parse | `{'review_creation_date': 0, 'review_answer_timestamp': 0}` | `{'review_creation_date': 0, 'review_answer_timestamp': 0}` | PASS |
| `V06.customers` | blocking | customers.customer_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V06.sellers` | blocking | sellers.seller_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V06.geolocation` | blocking | geolocation.geolocation_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V05.orders.customer_id` | blocking | orders.customer_id -> customers.customer_id: no orphans | `0` | `0` | PASS |
| `V05.order_items.order_id` | blocking | order_items.order_id -> orders.order_id: no orphans | `0` | `0` | PASS |
| `V05.payments.order_id` | blocking | payments.order_id -> orders.order_id: no orphans | `0` | `0` | PASS |
| `V05.reviews.order_id` | blocking | reviews.order_id -> orders.order_id: no orphans | `0` | `0` | PASS |
| `V05.order_items.product_id` | blocking | order_items.product_id -> products.product_id: no orphans | `0` | `0` | PASS |
| `V05.order_items.seller_id` | blocking | order_items.seller_id -> sellers.seller_id: no orphans | `0` | `0` | PASS |
| `V12.rows_sum` | blocking | geolocation_rows: sum(occurrence_count) = geolocation rows | `1000163` | `1000163` | PASS |
| `V12.points_sum` | blocking | geolocation_points: sum(raw_row_count) = geolocation rows | `1000163` | `1000163` | PASS |
| `V12.rows_unique` | blocking | geolocation_rows: no duplicate full rows | `0` | `0` | PASS |
| `V12.points_unique` | blocking | geolocation_points: no duplicate (zip, lat, lng) | `0` | `0` | PASS |
| `V12.points_from_rows` | blocking | geolocation_points: sum(n_full_rows) = geolocation_rows rows | `738332` | `738332` | PASS |
| `V12.zip_format` | blocking | geolocation_rows / points: zip prefixes are 5-digit strings | `[0, 0]` | `[0, 0]` | PASS |
| `V02.products_metadata_flags` | blocking | products_metadata_flags: one row per product, same order | `True` | `True` | PASS |
| `R.rows.customers` | regression | customers: row count (NB01) | `99441` | `99441` | PASS |
| `R.rows.geolocation` | regression | geolocation: row count (NB01) | `1000163` | `1000163` | PASS |
| `R.rows.orders` | regression | orders: row count (NB01) | `99441` | `99441` | PASS |
| `R.rows.order_items` | regression | order_items: row count (NB01) | `112650` | `112650` | PASS |
| `R.rows.payments` | regression | payments: row count (NB01) | `103886` | `103886` | PASS |
| `R.rows.reviews` | regression | reviews: row count (NB01) | `99224` | `99224` | PASS |
| `R.rows.products` | regression | products: row count (NB01) | `32951` | `32951` | PASS |
| `R.rows.sellers` | regression | sellers: row count (NB01) | `3095` | `3095` | PASS |
| `R.rows.translation` | regression | translation: row count (NB01) | `71` | `71` | PASS |
| `R.dates.orders.order_purchase_timestamp` | regression | orders.order_purchase_timestamp: non-missing, min, max (NB03 4.1) | `[99441, '2016-09-04 21:15:19', '2018-10-17 17:30:18']` | `[99441, '2016-09-04 21:15:19', '2018-10-17 17:30:18']` | PASS |
| `R.dates.orders.order_approved_at` | regression | orders.order_approved_at: non-missing, min, max (NB03 4.1) | `[99281, '2016-09-15 12:16:38', '2018-09-03 17:40:06']` | `[99281, '2016-09-15 12:16:38', '2018-09-03 17:40:06']` | PASS |
| `R.dates.orders.order_delivered_carrier_date` | regression | orders.order_delivered_carrier_date: non-missing, min, max (NB03 4.1) | `[97658, '2016-10-08 10:34:01', '2018-09-11 19:48:28']` | `[97658, '2016-10-08 10:34:01', '2018-09-11 19:48:28']` | PASS |
| `R.dates.orders.order_delivered_customer_date` | regression | orders.order_delivered_customer_date: non-missing, min, max (NB03 4.1) | `[96476, '2016-10-11 13:46:32', '2018-10-17 13:22:46']` | `[96476, '2016-10-11 13:46:32', '2018-10-17 13:22:46']` | PASS |
| `R.dates.orders.order_estimated_delivery_date` | regression | orders.order_estimated_delivery_date: non-missing, min, max (NB03 4.1) | `[99441, '2016-09-30 00:00:00', '2018-11-12 00:00:00']` | `[99441, '2016-09-30 00:00:00', '2018-11-12 00:00:00']` | PASS |
| `R.dates.order_items.shipping_limit_date` | regression | order_items.shipping_limit_date: non-missing, min, max (NB03 4.1) | `[112650, '2016-09-19 00:15:34', '2020-04-09 22:35:08']` | `[112650, '2016-09-19 00:15:34', '2020-04-09 22:35:08']` | PASS |
| `R.dates.reviews.review_creation_date` | regression | reviews.review_creation_date: non-missing, min, max (NB03 4.1) | `[99224, '2016-10-02 00:00:00', '2018-08-31 00:00:00']` | `[99224, '2016-10-02 00:00:00', '2018-08-31 00:00:00']` | PASS |
| `R.dates.reviews.review_answer_timestamp` | regression | reviews.review_answer_timestamp: non-missing, min, max (NB03 4.1) | `[99224, '2016-10-07 18:32:28', '2018-10-29 12:27:35']` | `[99224, '2016-10-07 18:32:28', '2018-10-29 12:27:35']` | PASS |
| `R.zip_leading_zero.customers` | regression | customers: zip prefixes starting with '0' (NB03 4.3) | `23995` | `23995` | PASS |
| `R.zip_leading_zero.sellers` | regression | sellers: zip prefixes starting with '0' (NB03 4.3) | `1027` | `1027` | PASS |
| `R.zip_leading_zero.geolocation` | regression | geolocation: zip prefixes starting with '0' (NB03 4.3) | `245733` | `245733` | PASS |
| `R.total.order_items.price` | regression | order_items.price: sum (NB02/NB03) | `13591643.7` | `13591643.7` | PASS |
| `R.total.order_items.freight_value` | regression | order_items.freight_value: sum (NB02/NB03) | `2251909.54` | `2251909.54` | PASS |
| `R.total.payments.payment_value` | regression | payments.payment_value: sum (NB02/NB03) | `16008872.12` | `16008872.12` | PASS |
| `R.untranslated_products` | regression | products with a category absent from translation (NB02 3.2) | `13` | `13` | PASS |
| `R.geolocation_rows` | regression | geolocation_rows: distinct full rows (NB02 2.2) | `738332` | `738332` | PASS |
| `R.geolocation_points` | regression | geolocation_points: distinct (zip, lat, lng) (NB02 5.3) | `720154` | `720154` | PASS |
| `R.geolocation_zips` | regression | distinct zip prefixes in geolocation (NB02 5.3) | `19015` | `19015` | PASS |
| `R.geolocation_outside_rows` | regression | raw rows represented by points outside Brazil (NB03 6.5) | `42` | `42` | PASS |
| `R.flag.dq_missing_category` | regression | products_metadata_flags.dq_missing_category count (strategy 4.3) | `610` | `610` | PASS |
| `R.flag.dq_missing_name_length` | regression | products_metadata_flags.dq_missing_name_length count (strategy 4.3) | `610` | `610` | PASS |
| `R.flag.dq_missing_description_length` | regression | products_metadata_flags.dq_missing_description_length count (strategy 4.3) | `610` | `610` | PASS |
| `R.flag.dq_missing_photos_qty` | regression | products_metadata_flags.dq_missing_photos_qty count (strategy 4.3) | `610` | `610` | PASS |
| `R.flag.dq_missing_weight_g` | regression | products_metadata_flags.dq_missing_weight_g count (strategy 4.3) | `2` | `2` | PASS |
| `R.flag.dq_missing_length_cm` | regression | products_metadata_flags.dq_missing_length_cm count (strategy 4.3) | `2` | `2` | PASS |
| `R.flag.dq_missing_height_cm` | regression | products_metadata_flags.dq_missing_height_cm count (strategy 4.3) | `2` | `2` | PASS |
| `R.flag.dq_missing_width_cm` | regression | products_metadata_flags.dq_missing_width_cm count (strategy 4.3) | `2` | `2` | PASS |
| `R.flag.dq_listing_metadata_missing` | regression | products_metadata_flags.dq_listing_metadata_missing count (strategy 4.3) | `610` | `610` | PASS |
| `R.flag.dq_physical_attributes_missing` | regression | products_metadata_flags.dq_physical_attributes_missing count (strategy 4.3) | `2` | `2` | PASS |
| `R.flag.dq_all_attributes_missing` | regression | products_metadata_flags.dq_all_attributes_missing count (strategy 4.3) | `1` | `1` | PASS |
| `R.flag_equivalence.listing` | regression | listing fields: products with ANY missing = products with ALL missing (strategy 4.3) | `610` | `610` | PASS |
| `R.flag_equivalence.physical` | regression | physical fields: products with ANY missing = products with ALL missing (strategy 4.3) | `2` | `2` | PASS |
| `W02` | warning | distinct (zip, lat, lng) points flagged outside Brazil (not measured in NB01-03) | `None` | `33` | logged |
| `W.geo_points_multi_city` | warning | points carried by more than one city spelling (explains part of the 18,178 gap) | `None` | `18133` | logged |
| `W.geo_points_multi_state` | warning | points carried by more than one state | `None` | `2` | logged |
| `P01.customers` | blocking | customers: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.geolocation` | blocking | geolocation: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.orders` | blocking | orders: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.order_items` | blocking | order_items: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.payments` | blocking | payments: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.reviews` | blocking | reviews: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.products` | blocking | products: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.sellers` | blocking | sellers: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.translation` | blocking | translation: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.geolocation_rows` | blocking | geolocation_rows: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.geolocation_points` | blocking | geolocation_points: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.products_metadata_flags` | blocking | products_metadata_flags: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
