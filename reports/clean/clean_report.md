# Clean Layer Report

- **Run ID:** `cdc88ffb32ce`
- **Executed (UTC):** 2026-11-01T20:22:23+00:00 → 2026-11-01T20:22:32+00:00
- **Git commit:** `fd7b7559eda66e50524f4f561e27aab8f69b60d3`
- **Code SHA-256 (src/):** `4b04ef08832ee5cb2594c41a8e57d8d3a0f52bcfa8d68092c1b0f3c9810bf75b`
- **Inputs:** staging `data/staging` (staging run `b387742326b9`); raw `data/raw` (checksums only)
- **Output directory:** `data/processed/clean` · **Manifest:** `data/processed/_manifest/cdc88ffb32ce.json`
- **Baseline checksums verified:** True
- **Outputs written:** True · **Success:** True

## Validation summary

| Class | Total | Passed | Failed |
|---|---:|---:|---:|
| blocking | 104 | 104 | 0 |
| regression | 76 | 76 | 0 |
| warning | 4 | 0 | 0 |

**Failed checks:** none

**Warnings (logged values):**

- `W03` zips with dq_zip_no_valid_point (centroid missing; not measured in NB01-03): **5**
- `W04` customers / sellers that receive a centroid via zip_lookup: **{'customers': {'with_centroid': 99162, 'dq_no_geolocation': 279, 'share_pct': 99.719}, 'sellers': {'with_centroid': 3088, 'dq_no_geolocation': 7, 'share_pct': 99.774}}**
- `W.review_before_delivery` reviews flagged dq_review_before_delivery (informational; NB03 5.5 measured 5,127): **5127**
- `W.category_overrides_not_applied` category_map overrides with no matching source row: **[]**

## Outputs

| Output | Kind | Grain | Rows in | Rows out | Columns | Added columns |
|---|---|---|---:|---:|---:|---:|
| `customers` | source | one customer record per order (customer_id) | 99,441 | 99,441 | 10 | 5 |
| `geolocation` | source | one raw coordinate row (no key; 1:1 with staging rows) | 1,000,163 | 1,000,163 | 8 | 3 |
| `orders` | source | one order (order_id) | 99,441 | 99,441 | 17 | 9 |
| `order_items` | source | one unit line within an order (order_id, order_item_id) | 112,650 | 112,650 | 8 | 1 |
| `payments` | source | one payment transaction (order_id, payment_sequential) | 103,886 | 103,886 | 7 | 2 |
| `reviews` | source | one review-order link (review_id, order_id) | 99,224 | 99,224 | 13 | 6 |
| `products` | source | one product listing (product_id) | 32,951 | 32,951 | 26 | 17 |
| `sellers` | source | one seller (seller_id) | 3,095 | 3,095 | 10 | 6 |
| `translation` | source | one category name mapping (product_category_name) | 71 | 71 | 2 | 0 |
| `category_map` | reference | one Portuguese category (product_category_name) | 71 | 73 | 5 | 0 |
| `zip_lookup` | reference | one zip prefix (geolocation_zip_code_prefix) | 720,154 | 19,015 | 9 | 0 |

## Flag counts

| Output | Flag | Rows flagged |
|---|---|---:|
| `customers` | `dq_no_geolocation` | 279 |
| `orders` | `dq_delivered_missing_approval` | 14 |
| `orders` | `dq_delivered_missing_carrier_date` | 2 |
| `orders` | `dq_delivered_missing_delivery_date` | 8 |
| `orders` | `dq_carrier_before_purchase` | 166 |
| `orders` | `dq_delivered_before_carrier` | 23 |
| `orders` | `dq_carrier_before_approval` | 1,359 |
| `orders` | `dq_delivered_before_approval` | 61 |
| `orders` | `dq_status_canceled_with_date` | 6 |
| `orders` | `dq_same_person_same_second` | 582 |
| `order_items` | `dq_shipping_limit_out_of_range` | 4 |
| `payments` | `dq_payment_zero` | 9 |
| `payments` | `dq_installments_zero` | 2 |
| `reviews` | `has_comment_title` | 11,568 |
| `reviews` | `has_comment_message` | 40,977 |
| `reviews` | `dq_review_id_shared` | 1,603 |
| `reviews` | `dq_review_content_duplicate` | 86 |
| `reviews` | `dq_review_before_purchase` | 64 |
| `reviews` | `dq_review_before_delivery` | 5,127 |
| `products` | `dq_missing_category` | 610 |
| `products` | `dq_missing_name_length` | 610 |
| `products` | `dq_missing_description_length` | 610 |
| `products` | `dq_missing_photos_qty` | 610 |
| `products` | `dq_missing_weight_g` | 2 |
| `products` | `dq_missing_length_cm` | 2 |
| `products` | `dq_missing_height_cm` | 2 |
| `products` | `dq_missing_width_cm` | 2 |
| `products` | `dq_listing_metadata_missing` | 610 |
| `products` | `dq_physical_attributes_missing` | 2 |
| `products` | `dq_all_attributes_missing` | 1 |
| `products` | `dq_weight_zero` | 4 |
| `products` | `dq_density_suspicious` | 159 |
| `products` | `dq_dims_16x2x11` | 588 |
| `sellers` | `dq_city_malformed` | 24 |
| `sellers` | `dq_no_geolocation` | 7 |
| `zip_lookup` | `dq_zip_multi_state` | 8 |
| `zip_lookup` | `dq_zip_no_valid_point` | 5 |

### `customers`

Sources: `customers`, `geolocation_points`, `geolocation_rows` · Path: `data/processed/clean/customers.parquet`

Transformations:

- + city_key [string] <- customer_city: accent-free, lower case, trimmed (D-11)
- + customer_state_std [category] <- customer_state: upper case, trimmed; validated against the 27 UFs (8.1)
- + lat_centroid [float64] <- zip_lookup.lat_centroid: zip_lookup centroid via many_to_one join on zip prefix (D-10.4)
- + lng_centroid [float64] <- zip_lookup.lng_centroid: zip_lookup centroid via many_to_one join on zip prefix (D-10.4)
- + dq_no_geolocation [bool] <- zip_lookup: zip absent from zip_lookup, or zip has no valid point (centroid missing) (D-10.4)

| Column | dtype | Missing |
|---|---|---:|
| `customer_id` | string | 0 |
| `customer_unique_id` | string | 0 |
| `customer_zip_code_prefix` | string | 0 |
| `customer_city` | string | 0 |
| `customer_state` | category | 0 |
| `city_key` | string | 0 |
| `customer_state_std` | category | 0 |
| `lat_centroid` | float64 | 279 |
| `lng_centroid` | float64 | 279 |
| `dq_no_geolocation` | bool | 0 |

### `geolocation`

Sources: `geolocation` · Path: `data/processed/clean/geolocation.parquet`

Transformations:

- + geolocation_city_std [string] <- geolocation_city: '%26apos%3b' decoded to an apostrophe; otherwise unchanged (8.1)
- + city_key [string] <- geolocation_city: accent-free, lower case, trimmed (raw text, no decoding) (D-11)
- + geolocation_state_std [category] <- geolocation_state: upper case, trimmed; validated against the 27 UFs (8.1)

| Column | dtype | Missing |
|---|---|---:|
| `geolocation_zip_code_prefix` | string | 0 |
| `geolocation_lat` | float64 | 0 |
| `geolocation_lng` | float64 | 0 |
| `geolocation_city` | string | 0 |
| `geolocation_state` | category | 0 |
| `geolocation_city_std` | string | 0 |
| `city_key` | string | 0 |
| `geolocation_state_std` | category | 0 |

### `orders`

Sources: `orders`, `customers` · Path: `data/processed/clean/orders.parquet`

Transformations:

- + dq_delivered_missing_approval [bool] <- order_status, order_approved_at: status = delivered and approval missing (D-06)
- + dq_delivered_missing_carrier_date [bool] <- order_status, order_delivered_carrier_date: status = delivered and carrier date missing (D-06)
- + dq_delivered_missing_delivery_date [bool] <- order_status, order_delivered_customer_date: status = delivered and delivery date missing (D-06)
- + dq_carrier_before_purchase [bool] <- order_delivered_carrier_date, order_purchase_timestamp: carrier date < purchase (both present) (D-08)
- + dq_delivered_before_carrier [bool] <- order_delivered_customer_date, order_delivered_carrier_date: delivery date < carrier date (both present) (D-08)
- + dq_carrier_before_approval [bool] <- order_delivered_carrier_date, order_approved_at: carrier date < approval (both present) (D-08)
- + dq_delivered_before_approval [bool] <- order_delivered_customer_date, order_approved_at: delivery date < approval (both present) (D-08)
- + dq_status_canceled_with_date [bool] <- order_status, order_delivered_customer_date: status = canceled and delivery date present (D-08 / Q-01)
- + dq_same_person_same_second [bool] <- customers.customer_unique_id, order_purchase_timestamp: another order by the same customer_unique_id has the same purchase timestamp (all rows of the group) (D-07 / Q-09)

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
| `dq_delivered_missing_approval` | bool | 0 |
| `dq_delivered_missing_carrier_date` | bool | 0 |
| `dq_delivered_missing_delivery_date` | bool | 0 |
| `dq_carrier_before_purchase` | bool | 0 |
| `dq_delivered_before_carrier` | bool | 0 |
| `dq_carrier_before_approval` | bool | 0 |
| `dq_delivered_before_approval` | bool | 0 |
| `dq_status_canceled_with_date` | bool | 0 |
| `dq_same_person_same_second` | bool | 0 |

### `order_items`

Sources: `order_items`, `orders` · Path: `data/processed/clean/order_items.parquet`

Transformations:

- + dq_shipping_limit_out_of_range [bool] <- shipping_limit_date, orders.order_purchase_timestamp: shipping_limit_date > max(purchase) + 1 year (D-08 / Q-15)

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `order_item_id` | Int64 | 0 |
| `product_id` | string | 0 |
| `seller_id` | string | 0 |
| `shipping_limit_date` | datetime64[us] | 0 |
| `price` | float64 | 0 |
| `freight_value` | float64 | 0 |
| `dq_shipping_limit_out_of_range` | bool | 0 |

### `payments`

Sources: `payments` · Path: `data/processed/clean/payments.parquet`

Transformations:

- + dq_payment_zero [bool] <- payment_value: payment_value = 0 (D-09)
- + dq_installments_zero [bool] <- payment_installments: payment_installments = 0 (value kept) (D-16 / Q-07)

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `payment_sequential` | Int64 | 0 |
| `payment_type` | category | 0 |
| `payment_installments` | Int64 | 0 |
| `payment_value` | float64 | 0 |
| `dq_payment_zero` | bool | 0 |
| `dq_installments_zero` | bool | 0 |

### `reviews`

Sources: `reviews`, `orders` · Path: `data/processed/clean/reviews.parquet`

Transformations:

- + has_comment_title [bool] <- review_comment_title: title present (4.2)
- + has_comment_message [bool] <- review_comment_message: message present (4.2)
- + dq_review_id_shared [bool] <- review_id: review_id appears on more than one row (all rows) (D-07 / Q-04)
- + dq_review_content_duplicate [bool] <- order_id, review_score, review_comment_title, review_comment_message, review_creation_date: repeats an earlier row's order, score, title, message and creation date (first occurrence not flagged) (D-07)
- + dq_review_before_purchase [bool] <- review_creation_date, orders.order_purchase_timestamp: creation date < purchase date (day level) (D-08 / Q-15)
- + dq_review_before_delivery [bool] <- review_creation_date, orders.order_delivered_customer_date: creation date < delivery date (day level); informational (D-08)

| Column | dtype | Missing |
|---|---|---:|
| `review_id` | string | 0 |
| `order_id` | string | 0 |
| `review_score` | Int64 | 0 |
| `review_comment_title` | string | 87,656 |
| `review_comment_message` | string | 58,247 |
| `review_creation_date` | datetime64[us] | 0 |
| `review_answer_timestamp` | datetime64[us] | 0 |
| `has_comment_title` | bool | 0 |
| `has_comment_message` | bool | 0 |
| `dq_review_id_shared` | bool | 0 |
| `dq_review_content_duplicate` | bool | 0 |
| `dq_review_before_purchase` | bool | 0 |
| `dq_review_before_delivery` | bool | 0 |

### `products`

Sources: `products`, `products_metadata_flags`, `translation` · Path: `data/processed/clean/products.parquet`

Transformations:

- + dq_missing_category [bool] <- products_metadata_flags.dq_missing_category: reused from staging products_metadata_flags (product_category_name missing) (4.3)
- + dq_missing_name_length [bool] <- products_metadata_flags.dq_missing_name_length: reused from staging products_metadata_flags (product_name_length missing) (4.3)
- + dq_missing_description_length [bool] <- products_metadata_flags.dq_missing_description_length: reused from staging products_metadata_flags (product_description_length missing) (4.3)
- + dq_missing_photos_qty [bool] <- products_metadata_flags.dq_missing_photos_qty: reused from staging products_metadata_flags (product_photos_qty missing) (4.3)
- + dq_missing_weight_g [bool] <- products_metadata_flags.dq_missing_weight_g: reused from staging products_metadata_flags (product_weight_g missing) (4.3)
- + dq_missing_length_cm [bool] <- products_metadata_flags.dq_missing_length_cm: reused from staging products_metadata_flags (product_length_cm missing) (4.3)
- + dq_missing_height_cm [bool] <- products_metadata_flags.dq_missing_height_cm: reused from staging products_metadata_flags (product_height_cm missing) (4.3)
- + dq_missing_width_cm [bool] <- products_metadata_flags.dq_missing_width_cm: reused from staging products_metadata_flags (product_width_cm missing) (4.3)
- + dq_listing_metadata_missing [bool] <- products_metadata_flags.dq_listing_metadata_missing: reused from staging products_metadata_flags (all fields of the group missing) (4.3)
- + dq_physical_attributes_missing [bool] <- products_metadata_flags.dq_physical_attributes_missing: reused from staging products_metadata_flags (all fields of the group missing) (4.3)
- + dq_all_attributes_missing [bool] <- products_metadata_flags.dq_all_attributes_missing: reused from staging products_metadata_flags (all fields of the group missing) (4.3)
- + dq_weight_zero [bool] <- product_weight_g: product_weight_g = 0 (D-09)
- + weight_g_valid [Int64] <- product_weight_g: product_weight_g, set to missing where dq_weight_zero (analysis column) (D-09)
- + dq_density_suspicious [bool] <- product_weight_g, product_length_cm, product_height_cm, product_width_cm: weight / (length x height x width) > 2.0 g/cm3; missing inputs -> False (D-09 / Q-06)
- + dq_dims_16x2x11 [bool] <- product_length_cm, product_height_cm, product_width_cm: dimensions exactly 16 x 2 x 11 cm (D-09 / Q-06)
- + category_en_clean [string] <- product_category_name, category_map.category_en_clean: category_map label via many_to_one join; missing where the category is missing (D-12)
- + category_reporting [string] <- product_category_name, category_en_clean: category_en_clean; explicit unknown label where the raw category is missing (D-12 / Q-05)

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
| `dq_weight_zero` | bool | 0 |
| `weight_g_valid` | Int64 | 6 |
| `dq_density_suspicious` | bool | 0 |
| `dq_dims_16x2x11` | bool | 0 |
| `category_en_clean` | string | 610 |
| `category_reporting` | string | 0 |

### `sellers`

Sources: `sellers`, `geolocation_points`, `geolocation_rows` · Path: `data/processed/clean/sellers.parquet`

Transformations:

- + city_key [string] <- seller_city: accent-free, lower case, trimmed (malformed values not repaired) (D-11)
- + seller_state_std [category] <- seller_state: upper case, trimmed; validated against the 27 UFs (8.1)
- + dq_city_malformed [bool] <- seller_city: seller_city matches '[\\d/\\\\@,]|\\s-\\s' (D-11 / Q-12)
- + lat_centroid [float64] <- zip_lookup.lat_centroid: zip_lookup centroid via many_to_one join on zip prefix (D-10.4)
- + lng_centroid [float64] <- zip_lookup.lng_centroid: zip_lookup centroid via many_to_one join on zip prefix (D-10.4)
- + dq_no_geolocation [bool] <- zip_lookup: zip absent from zip_lookup, or zip has no valid point (centroid missing) (D-10.4)

| Column | dtype | Missing |
|---|---|---:|
| `seller_id` | string | 0 |
| `seller_zip_code_prefix` | string | 0 |
| `seller_city` | string | 0 |
| `seller_state` | category | 0 |
| `city_key` | string | 0 |
| `seller_state_std` | category | 0 |
| `dq_city_malformed` | bool | 0 |
| `lat_centroid` | float64 | 7 |
| `lng_centroid` | float64 | 7 |
| `dq_no_geolocation` | bool | 0 |

### `translation`

Sources: `translation` · Path: `data/processed/clean/translation.parquet`

Transformations:

- no change (label corrections live in category_map, D-12)

| Column | dtype | Missing |
|---|---|---:|
| `product_category_name` | string | 0 |
| `product_category_name_english` | string | 0 |

### `category_map`

Sources: `translation`, `products` · Path: `data/processed/clean/category_map.parquet`

Transformations:

- one row per Portuguese category; category_map version 1.0.0
- category_en_source = source translation (unchanged, missing when untranslated)
- category_en_clean = source translation with the 4 typo corrections; 2 project-added translations (provisional, Q-05)
- '_2' twins kept separate (Q-05)

| Column | dtype | Missing |
|---|---|---:|
| `product_category_name` | string | 0 |
| `category_en_source` | string | 2 |
| `category_en_clean` | string | 0 |
| `is_added_by_project` | bool | 0 |
| `note` | string | 65 |

### `zip_lookup`

Sources: `geolocation_points`, `geolocation_rows` · Path: `data/processed/clean/zip_lookup.parquet`

Transformations:

- one row per zip prefix of geolocation_points (D-10.3)
- lat/lng_centroid = component-wise median over distinct points with dq_outside_brazil False, unweighted
- n_points_used, n_points_excluded_outside_brazil, raw_rows_represented (sum of raw_row_count over used points)
- n_distinct_states over the zip's geolocation_rows; dq_zip_multi_state = n_distinct_states > 1
- dq_zip_no_valid_point = no valid point (centroids missing)

| Column | dtype | Missing |
|---|---|---:|
| `geolocation_zip_code_prefix` | string | 0 |
| `lat_centroid` | float64 | 5 |
| `lng_centroid` | float64 | 5 |
| `n_points_used` | int64 | 0 |
| `n_points_excluded_outside_brazil` | int64 | 0 |
| `raw_rows_represented` | int64 | 0 |
| `n_distinct_states` | int64 | 0 |
| `dq_zip_multi_state` | bool | 0 |
| `dq_zip_no_valid_point` | bool | 0 |

## All checks

| ID | Class | Description | Expected | Observed | Result |
|---|---|---|---|---|---|
| `V01` | blocking | raw files unchanged from run start to validation (SHA-256); baseline verified | `True` | `True` | PASS |
| `S01.customers` | blocking | staging customers: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.geolocation` | blocking | staging geolocation: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.orders` | blocking | staging orders: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.order_items` | blocking | staging order_items: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.payments` | blocking | staging payments: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.reviews` | blocking | staging reviews: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.products` | blocking | staging products: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.sellers` | blocking | staging sellers: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.translation` | blocking | staging translation: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.geolocation_rows` | blocking | staging geolocation_rows: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.geolocation_points` | blocking | staging geolocation_points: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S01.products_metadata_flags` | blocking | staging products_metadata_flags: lineage present and its source SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `S02` | blocking | all staging inputs come from one staging run | `1` | `1` | PASS |
| `V14.customers.rows` | blocking | customers: clean rows = staged rows (none added or removed) | `99441` | `99441` | PASS |
| `V03.customers` | blocking | customers: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.customers` | blocking | customers: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.customers` | blocking | customers: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.customers` | blocking | customers: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.customers` | blocking | customers: primary key ('customer_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.geolocation.rows` | blocking | geolocation: clean rows = staged rows (none added or removed) | `1000163` | `1000163` | PASS |
| `V03.geolocation` | blocking | geolocation: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.geolocation` | blocking | geolocation: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.geolocation` | blocking | geolocation: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.geolocation` | blocking | geolocation: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V14.orders.rows` | blocking | orders: clean rows = staged rows (none added or removed) | `99441` | `99441` | PASS |
| `V03.orders` | blocking | orders: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.orders` | blocking | orders: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.orders` | blocking | orders: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.orders` | blocking | orders: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.orders` | blocking | orders: primary key ('order_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.order_items.rows` | blocking | order_items: clean rows = staged rows (none added or removed) | `112650` | `112650` | PASS |
| `V03.order_items` | blocking | order_items: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.order_items` | blocking | order_items: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.order_items` | blocking | order_items: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.order_items` | blocking | order_items: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.order_items` | blocking | order_items: primary key ('order_id', 'order_item_id') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.payments.rows` | blocking | payments: clean rows = staged rows (none added or removed) | `103886` | `103886` | PASS |
| `V03.payments` | blocking | payments: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.payments` | blocking | payments: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.payments` | blocking | payments: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.payments` | blocking | payments: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.payments` | blocking | payments: primary key ('order_id', 'payment_sequential') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.reviews.rows` | blocking | reviews: clean rows = staged rows (none added or removed) | `99224` | `99224` | PASS |
| `V03.reviews` | blocking | reviews: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.reviews` | blocking | reviews: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.reviews` | blocking | reviews: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.reviews` | blocking | reviews: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.reviews` | blocking | reviews: primary key ('review_id', 'order_id') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.products.rows` | blocking | products: clean rows = staged rows (none added or removed) | `32951` | `32951` | PASS |
| `V03.products` | blocking | products: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.products` | blocking | products: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.products` | blocking | products: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.products` | blocking | products: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.products` | blocking | products: primary key ('product_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.sellers.rows` | blocking | sellers: clean rows = staged rows (none added or removed) | `3095` | `3095` | PASS |
| `V03.sellers` | blocking | sellers: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.sellers` | blocking | sellers: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.sellers` | blocking | sellers: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.sellers` | blocking | sellers: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.sellers` | blocking | sellers: primary key ('seller_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V14.translation.rows` | blocking | translation: clean rows = staged rows (none added or removed) | `71` | `71` | PASS |
| `V03.translation` | blocking | translation: columns = staged columns + declared additions, with declared dtypes | `[]` | `[]` | PASS |
| `V14.translation` | blocking | translation: every source column identical to staging (values, order, dtype) | `[]` | `[]` | PASS |
| `V08.translation` | blocking | translation: missing count per source column = staging | `{}` | `{}` | PASS |
| `F01.translation` | blocking | translation: every flag column is boolean with no missing value | `[]` | `[]` | PASS |
| `V04.translation` | blocking | translation: primary key ('product_category_name',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V03.category_map` | blocking | category_map: columns and dtypes match the reference schema | `[]` | `[]` | PASS |
| `V03.zip_lookup` | blocking | zip_lookup: columns and dtypes match the reference schema | `[]` | `[]` | PASS |
| `V05.orders.customer_id` | blocking | orders.customer_id -> customers.customer_id: no orphans in clean | `0` | `0` | PASS |
| `V05.order_items.order_id` | blocking | order_items.order_id -> orders.order_id: no orphans in clean | `0` | `0` | PASS |
| `V05.payments.order_id` | blocking | payments.order_id -> orders.order_id: no orphans in clean | `0` | `0` | PASS |
| `V05.reviews.order_id` | blocking | reviews.order_id -> orders.order_id: no orphans in clean | `0` | `0` | PASS |
| `V05.order_items.product_id` | blocking | order_items.product_id -> products.product_id: no orphans in clean | `0` | `0` | PASS |
| `V05.order_items.seller_id` | blocking | order_items.seller_id -> sellers.seller_id: no orphans in clean | `0` | `0` | PASS |
| `V06.customers` | blocking | customers.customer_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V06.sellers` | blocking | sellers.seller_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V06.geolocation` | blocking | geolocation.geolocation_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V06.zip_lookup` | blocking | zip_lookup.geolocation_zip_code_prefix: all values are 5-digit strings | `0` | `0` | PASS |
| `V09.order_items.price` | blocking | order_items.price: clean sum = staged sum (same run) | `13591643.7` | `13591643.7` | PASS |
| `V09.order_items.freight_value` | blocking | order_items.freight_value: clean sum = staged sum (same run) | `2251909.54` | `2251909.54` | PASS |
| `V09.payments.payment_value` | blocking | payments.payment_value: clean sum = staged sum (same run) | `16008872.120000001` | `16008872.120000001` | PASS |
| `V12.zip_key_unique` | blocking | zip_lookup: zip key unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `V12.zip_set` | blocking | zip_lookup: exactly the zip prefixes of geolocation_points | `True` | `True` | PASS |
| `V12.customers_join_rows` | blocking | customers keep their row count through the zip_lookup join | `99441` | `99441` | PASS |
| `V12.sellers_join_rows` | blocking | sellers keep their row count through the zip_lookup join | `3095` | `3095` | PASS |
| `V12.points_accounted` | blocking | per zip, n_points_used + n_points_excluded_outside_brazil = its distinct points (total = points rows) | `[0, 720154]` | `[0, 720154]` | PASS |
| `V12.no_valid_point_consistency` | blocking | centroids are missing exactly where dq_zip_no_valid_point (both axes together) | `True` | `True` | PASS |
| `V12.centroid_plausible` | blocking | every centroid lies in the bounding box and within its zip's used points on each axis | `0` | `0` | PASS |
| `F02.products_metadata_flags` | blocking | clean products carry the staging products_metadata_flags unchanged | `True` | `True` | PASS |
| `V04.category_map` | blocking | category_map: key unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `M01.category_map_coverage` | blocking | every non-missing product category maps to exactly one category_map row | `0` | `0` | PASS |
| `R.rows.customers` | regression | customers: clean row count (NB01) | `99441` | `99441` | PASS |
| `R.rows.geolocation` | regression | geolocation: clean row count (NB01) | `1000163` | `1000163` | PASS |
| `R.rows.orders` | regression | orders: clean row count (NB01) | `99441` | `99441` | PASS |
| `R.rows.order_items` | regression | order_items: clean row count (NB01) | `112650` | `112650` | PASS |
| `R.rows.payments` | regression | payments: clean row count (NB01) | `103886` | `103886` | PASS |
| `R.rows.reviews` | regression | reviews: clean row count (NB01) | `99224` | `99224` | PASS |
| `R.rows.products` | regression | products: clean row count (NB01) | `32951` | `32951` | PASS |
| `R.rows.sellers` | regression | sellers: clean row count (NB01) | `3095` | `3095` | PASS |
| `R.rows.translation` | regression | translation: clean row count (NB01) | `71` | `71` | PASS |
| `R.flag.dq_carrier_before_purchase` | regression | orders.dq_carrier_before_purchase count (strategy 11.3.1) | `166` | `166` | PASS |
| `R.flag.dq_delivered_before_carrier` | regression | orders.dq_delivered_before_carrier count (strategy 11.3.1) | `23` | `23` | PASS |
| `R.flag.dq_carrier_before_approval` | regression | orders.dq_carrier_before_approval count (strategy 11.3.1) | `1359` | `1359` | PASS |
| `R.flag.dq_delivered_before_approval` | regression | orders.dq_delivered_before_approval count (strategy 11.3.1) | `61` | `61` | PASS |
| `R.flag.dq_delivered_missing_delivery_date` | regression | orders.dq_delivered_missing_delivery_date count (strategy 11.3.1) | `8` | `8` | PASS |
| `R.flag.dq_delivered_missing_approval` | regression | orders.dq_delivered_missing_approval count (strategy 11.3.1) | `14` | `14` | PASS |
| `R.flag.dq_delivered_missing_carrier_date` | regression | orders.dq_delivered_missing_carrier_date count (strategy 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_status_canceled_with_date` | regression | orders.dq_status_canceled_with_date count (strategy 11.3.1) | `6` | `6` | PASS |
| `R.flag.dq_same_person_same_second` | regression | orders.dq_same_person_same_second count (strategy 11.3.1) | `582` | `582` | PASS |
| `R.flag.dq_shipping_limit_out_of_range` | regression | order_items.dq_shipping_limit_out_of_range count (strategy 11.3.1) | `4` | `4` | PASS |
| `R.flag.dq_review_before_purchase` | regression | reviews.dq_review_before_purchase count (strategy 11.3.1) | `64` | `64` | PASS |
| `R.flag.dq_review_id_shared` | regression | reviews.dq_review_id_shared count (strategy 11.3.1) | `1603` | `1603` | PASS |
| `R.flag.dq_review_content_duplicate` | regression | reviews.dq_review_content_duplicate count (strategy 11.3.1) | `86` | `86` | PASS |
| `R.flag.dq_weight_zero` | regression | products.dq_weight_zero count (strategy 11.3.1) | `4` | `4` | PASS |
| `R.flag.dq_density_suspicious` | regression | products.dq_density_suspicious count (strategy 11.3.1) | `159` | `159` | PASS |
| `R.flag.dq_dims_16x2x11` | regression | products.dq_dims_16x2x11 count (strategy 11.3.1) | `588` | `588` | PASS |
| `R.flag.dq_installments_zero` | regression | payments.dq_installments_zero count (strategy 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_payment_zero` | regression | payments.dq_payment_zero count (strategy 11.3.1) | `9` | `9` | PASS |
| `R.flag.dq_city_malformed` | regression | sellers.dq_city_malformed count (strategy 11.3.1) | `24` | `24` | PASS |
| `R.flag.dq_zip_multi_state` | regression | zip_lookup.dq_zip_multi_state count (strategy 11.3.1) | `8` | `8` | PASS |
| `R.flag.dq_missing_category` | regression | products.dq_missing_category count (strategy 4.3 / 11.3.1) | `610` | `610` | PASS |
| `R.flag.dq_missing_name_length` | regression | products.dq_missing_name_length count (strategy 4.3 / 11.3.1) | `610` | `610` | PASS |
| `R.flag.dq_missing_description_length` | regression | products.dq_missing_description_length count (strategy 4.3 / 11.3.1) | `610` | `610` | PASS |
| `R.flag.dq_missing_photos_qty` | regression | products.dq_missing_photos_qty count (strategy 4.3 / 11.3.1) | `610` | `610` | PASS |
| `R.flag.dq_missing_weight_g` | regression | products.dq_missing_weight_g count (strategy 4.3 / 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_missing_length_cm` | regression | products.dq_missing_length_cm count (strategy 4.3 / 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_missing_height_cm` | regression | products.dq_missing_height_cm count (strategy 4.3 / 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_missing_width_cm` | regression | products.dq_missing_width_cm count (strategy 4.3 / 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_listing_metadata_missing` | regression | products.dq_listing_metadata_missing count (strategy 4.3 / 11.3.1) | `610` | `610` | PASS |
| `R.flag.dq_physical_attributes_missing` | regression | products.dq_physical_attributes_missing count (strategy 4.3 / 11.3.1) | `2` | `2` | PASS |
| `R.flag.dq_all_attributes_missing` | regression | products.dq_all_attributes_missing count (strategy 4.3 / 11.3.1) | `1` | `1` | PASS |
| `R.flag_equivalence.listing` | regression | listing fields: products with ANY missing = products with ALL missing (strategy 4.3) | `610` | `610` | PASS |
| `R.flag_equivalence.physical` | regression | physical fields: products with ANY missing = products with ALL missing (strategy 4.3) | `2` | `2` | PASS |
| `R.domain.price_le_0` | regression | domain rule price_le_0 (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.freight_lt_0` | regression | domain rule freight_lt_0 (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.freight_eq_0` | regression | domain rule freight_eq_0 (strategy 11.3.2) | `383` | `383` | PASS |
| `R.domain.payment_lt_0` | regression | domain rule payment_lt_0 (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.payment_eq_0` | regression | domain rule payment_eq_0 (strategy 11.3.2) | `9` | `9` | PASS |
| `R.domain.installments_lt_1` | regression | domain rule installments_lt_1 (strategy 11.3.2) | `2` | `2` | PASS |
| `R.domain.installments_gt_1_non_credit_card` | regression | domain rule installments_gt_1_non_credit_card (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.review_score_outside_1_5` | regression | domain rule review_score_outside_1_5 (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.weight_le_0` | regression | domain rule weight_le_0 (strategy 11.3.2) | `4` | `4` | PASS |
| `R.domain.any_dimension_le_0` | regression | domain rule any_dimension_le_0 (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.photos_lt_1` | regression | domain rule photos_lt_1 (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.customers_state_not_uf` | regression | domain rule customers_state_not_uf (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.sellers_state_not_uf` | regression | domain rule sellers_state_not_uf (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.geolocation_state_not_uf` | regression | domain rule geolocation_state_not_uf (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.order_status_outside_vocabulary` | regression | domain rule order_status_outside_vocabulary (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.payment_type_outside_vocabulary` | regression | domain rule payment_type_outside_vocabulary (strategy 11.3.2) | `0` | `0` | PASS |
| `R.domain.weight_le_0_rows` | regression | rows with weight <= 0 are exactly the dq_weight_zero rows | `True` | `True` | PASS |
| `R.domain.payment_eq_0_rows` | regression | rows with payment_value = 0 are exactly the dq_payment_zero rows | `True` | `True` | PASS |
| `R.domain.installments_lt_1_rows` | regression | rows with installments < 1 are exactly the dq_installments_zero rows, all credit_card | `True` | `True` | PASS |
| `R.total.order_items.price` | regression | order_items.price: sum (NB02/NB03) | `13591643.7` | `13591643.7` | PASS |
| `R.total.order_items.freight_value` | regression | order_items.freight_value: sum (NB02/NB03) | `2251909.54` | `2251909.54` | PASS |
| `R.total.payments.payment_value` | regression | payments.payment_value: sum (NB02/NB03) | `16008872.12` | `16008872.12` | PASS |
| `R.structure.distinct_review_id` | regression | distinct_review_id (strategy 11.3.3 / D-07 / D-11 / D-12) | `98410` | `98410` | PASS |
| `R.structure.distinct_customer_unique_id` | regression | distinct_customer_unique_id (strategy 11.3.3 / D-07 / D-11 / D-12) | `96096` | `96096` | PASS |
| `R.structure.untranslated_products_before_map` | regression | untranslated_products_before_map (strategy 11.3.3 / D-07 / D-11 / D-12) | `13` | `13` | PASS |
| `R.structure.untranslated_products_after_map` | regression | untranslated_products_after_map (strategy 11.3.3 / D-07 / D-11 / D-12) | `0` | `0` | PASS |
| `R.structure.category_map_rows` | regression | category_map_rows (strategy 11.3.3 / D-07 / D-11 / D-12) | `73` | `73` | PASS |
| `R.structure.missing_category_products` | regression | missing_category_products (strategy 11.3.3 / D-07 / D-11 / D-12) | `610` | `610` | PASS |
| `R.structure.zip_lookup_rows` | regression | zip_lookup_rows (strategy 11.3.3 / D-07 / D-11 / D-12) | `19015` | `19015` | PASS |
| `R.structure.customers_zip_absent` | regression | customers_zip_absent (strategy 11.3.3 / D-07 / D-11 / D-12) | `278` | `278` | PASS |
| `R.structure.sellers_zip_absent` | regression | sellers_zip_absent (strategy 11.3.3 / D-07 / D-11 / D-12) | `7` | `7` | PASS |
| `R.structure.city_key_distinct_customers` | regression | city_key_distinct_customers (strategy 11.3.3 / D-07 / D-11 / D-12) | `4119` | `4119` | PASS |
| `R.structure.city_key_distinct_sellers` | regression | city_key_distinct_sellers (strategy 11.3.3 / D-07 / D-11 / D-12) | `609` | `609` | PASS |
| `R.structure.city_key_distinct_geolocation` | regression | city_key_distinct_geolocation (strategy 11.3.3 / D-07 / D-11 / D-12) | `5966` | `5966` | PASS |
| `W03` | warning | zips with dq_zip_no_valid_point (centroid missing; not measured in NB01-03) | `None` | `5` | logged |
| `W04` | warning | customers / sellers that receive a centroid via zip_lookup | `None` | `{'customers': {'with_centroid': 99162, 'dq_no_geolocation': 279, 'share_pct': 99.719}, 'sellers': {'with_centroid': 3088, 'dq_no_geolocation': 7, 'share_pct': 99.774}}` | logged |
| `W.review_before_delivery` | warning | reviews flagged dq_review_before_delivery (informational; NB03 5.5 measured 5,127) | `None` | `5127` | logged |
| `W.category_overrides_not_applied` | warning | category_map overrides with no matching source row | `None` | `[]` | logged |
| `P01.customers` | blocking | customers: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.geolocation` | blocking | geolocation: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.orders` | blocking | orders: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.order_items` | blocking | order_items: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.payments` | blocking | payments: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.reviews` | blocking | reviews: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.products` | blocking | products: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.sellers` | blocking | sellers: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.translation` | blocking | translation: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.category_map` | blocking | category_map: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.zip_lookup` | blocking | zip_lookup: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `V01.end` | blocking | raw files unchanged at the end of the run (SHA-256) | `True` | `True` | PASS |
