# Analytical Layer Report

- **Run ID:** `baea48c9b8ac`
- **Executed (UTC):** 2026-11-01T20:47:25+00:00 → 2026-11-01T20:47:43+00:00
- **Git commit:** `6d2ca67039044cb6d69d26d6325a13405aafd4fc`
- **Code SHA-256 (src/):** `c75764756e24bfb5d442f34018daaeb23d115e94fc42e2980e9ae28a51cceb84`
- **Inputs:** clean `data/processed/clean` (clean run `cdc88ffb32ce`); raw `data/raw` (checksums only)
- **Output directory:** `data/processed/analytical` · **Manifest:** `data/processed/_manifest/baea48c9b8ac.json`
- **Baseline checksums verified:** True
- **Outputs written:** True · **Success:** True

## Validation summary

| Class | Total | Passed | Failed |
|---|---:|---:|---:|
| blocking | 101 | 101 | 0 |
| regression | 19 | 19 | 0 |
| warning | 4 | 0 | 0 |

**Failed checks:** none

**Warnings (logged values):**

- `W01` (order_id, product_id, seller_id, price) groups with >1 freight_value or shipping_limit_date (rejected coarser grain, D-15): **0**
- `W04` orders / sellers that receive a centroid via zip_lookup: **{'orders_with_customer_centroid': 99162, 'orders_share_pct': 99.719, 'sellers_with_centroid': 3088, 'sellers_share_pct': 99.774}**
- `W05` fact rows outside the candidate window 2017-01..2018-08 (flagged, not removed; Q-11): **{'fact_order_lines': 333, 'fact_orders': 349, 'fact_payments': 366, 'fact_reviews': 345}**
- `W.lines_with_quantity_gt_1` order lines with quantity > 1: **7088**

## Decisions applied

- **Q-11:** candidate window 2017-01..2018-08 applied as a flag only; W05 reports rows outside it
- **Q-04:** fact_orders carries review_count only; no order-level score
- **eligibility:** dim_customers measures in two labelled variants: all orders, and status = delivered
- **location:** customer location kept per order in fact_orders; dim_customers has no chosen address
- **Q-03:** item-based and payment-based totals are separate, labelled columns; never combined into one revenue
- **Q-16:** orders without items / payments are kept; their counts are 0 and their totals missing (not 0)
- **D-16:** max_installments ignores dq_installments_zero rows; payment totals include them

## Tables

| Table | Kind | Grain | Primary key | Rows | Columns |
|---|---|---|---|---:|---:|
| `fact_order_lines` | fact | one order line = order_items rows identical in (order_id, product_id, seller_id, price, freight_value, shipping_limit_date) | `order_line_id` | 102,425 | 18 |
| `fact_orders` | fact | one order | `order_id` | 99,441 | 53 |
| `fact_payments` | fact | one payment transaction | `order_id, payment_sequential` | 103,886 | 12 |
| `fact_reviews` | fact | one review-order link | `review_id, order_id` | 99,224 | 19 |
| `dim_customers` | dimension | one person (customer_unique_id) | `customer_unique_id` | 96,096 | 19 |
| `dim_products` | dimension | one product | `product_id` | 32,951 | 26 |
| `dim_sellers` | dimension | one seller | `seller_id` | 3,095 | 9 |
| `dim_dates` | dimension | one calendar day from the first to the last event date | `date` | 1,314 | 11 |

## Joins (all validated)

| Join | validate | Left rows | Right rows | Result rows |
|---|---|---:|---:|---:|
| `orders_x_customers.context` | one_to_one | 99,441 | 99,441 | 99,441 |
| `order_lines_x_order_context` | many_to_one | 102,425 | 99,441 | 102,425 |
| `orders_x_customers` | one_to_one | 99,441 | 99,441 | 99,441 |
| `orders_x_item_summary` | one_to_one | 99,441 | 98,666 | 99,441 |
| `orders_x_payment_summary` | one_to_one | 99,441 | 99,440 | 99,441 |
| `orders_x_review_summary` | one_to_one | 99,441 | 98,673 | 99,441 |
| `payments_x_order_context` | many_to_one | 103,886 | 99,441 | 103,886 |
| `reviews_x_order_context` | many_to_one | 99,224 | 99,441 | 99,224 |
| `reviews_x_delivery_date` | many_to_one | 99,224 | 99,441 | 99,224 |

### `fact_order_lines`

**Purpose:** Product, category, seller and demand analysis: units sold and item-based sales per line (D-15)

**Grain:** one order line = order_items rows identical in (order_id, product_id, seller_id, price, freight_value, shipping_limit_date) · **PK:** `order_line_id` · **FKs:** `order_id -> fact_orders.order_id`, `product_id -> dim_products.product_id`, `seller_id -> dim_sellers.seller_id`, `customer_unique_id -> dim_customers.customer_unique_id`, `purchase_date -> dim_dates.date` · **Sources:** clean.order_items, clean.orders, clean.customers · **Path:** `data/processed/analytical/fact_order_lines.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `order_line_id` | string | 0 |
| `order_id` | string | 0 |
| `first_order_item_id` | Int64 | 0 |
| `product_id` | string | 0 |
| `seller_id` | string | 0 |
| `shipping_limit_date` | datetime64[us] | 0 |
| `quantity` | int64 | 0 |
| `unit_price` | float64 | 0 |
| `freight_value_per_row` | float64 | 0 |
| `line_price_total` | float64 | 0 |
| `freight_total` | float64 | 0 |
| `order_item_ids` | object | 0 |
| `dq_shipping_limit_out_of_range` | bool | 0 |
| `customer_unique_id` | string | 0 |
| `order_status` | category | 0 |
| `order_purchase_timestamp` | datetime64[us] | 0 |
| `purchase_date` | datetime64[us] | 0 |
| `in_candidate_window` | bool | 0 |

### `fact_orders`

**Purpose:** Order-level sales, payment reconciliation, delivery/logistics, cohort and retention analysis (D-13, D-08)

**Grain:** one order · **PK:** `order_id` · **FKs:** `customer_unique_id -> dim_customers.customer_unique_id`, `purchase_date -> dim_dates.date` · **Sources:** clean.orders, clean.customers, clean.order_items, clean.payments, clean.reviews · **Path:** `data/processed/analytical/fact_orders.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `customer_id` | string | 0 |
| `customer_unique_id` | string | 0 |
| `order_status` | category | 0 |
| `order_purchase_timestamp` | datetime64[us] | 0 |
| `order_approved_at` | datetime64[us] | 160 |
| `order_delivered_carrier_date` | datetime64[us] | 1,783 |
| `order_delivered_customer_date` | datetime64[us] | 2,965 |
| `order_estimated_delivery_date` | datetime64[us] | 0 |
| `purchase_date` | datetime64[us] | 0 |
| `purchase_year_month` | string | 0 |
| `in_candidate_window` | bool | 0 |
| `customer_order_seq` | int64 | 0 |
| `is_first_order` | bool | 0 |
| `customer_zip_code_prefix` | string | 0 |
| `customer_city_key` | string | 0 |
| `customer_state_std` | category | 0 |
| `customer_lat_centroid` | float64 | 279 |
| `customer_lng_centroid` | float64 | 279 |
| `dq_customer_no_geolocation` | bool | 0 |
| `dq_delivered_missing_approval` | bool | 0 |
| `dq_delivered_missing_carrier_date` | bool | 0 |
| `dq_delivered_missing_delivery_date` | bool | 0 |
| `dq_carrier_before_purchase` | bool | 0 |
| `dq_delivered_before_carrier` | bool | 0 |
| `dq_carrier_before_approval` | bool | 0 |
| `dq_delivered_before_approval` | bool | 0 |
| `dq_status_canceled_with_date` | bool | 0 |
| `dq_same_person_same_second` | bool | 0 |
| `has_items` | bool | 0 |
| `item_count` | int64 | 0 |
| `order_line_count` | int64 | 0 |
| `distinct_products` | int64 | 0 |
| `distinct_sellers` | int64 | 0 |
| `items_price_total` | float64 | 775 |
| `items_freight_total` | float64 | 775 |
| `has_payment` | bool | 0 |
| `payment_rows` | int64 | 0 |
| `payment_total` | float64 | 1 |
| `payment_types` | string | 1 |
| `has_voucher` | bool | 0 |
| `max_installments` | Int64 | 3 |
| `payment_minus_items` | float64 | 776 |
| `dq_payment_items_mismatch` | bool | 0 |
| `review_count` | int64 | 0 |
| `approval_hours` | float64 | 160 |
| `purchase_to_carrier_days` | float64 | 1,949 |
| `approval_to_carrier_days` | float64 | 3,156 |
| `carrier_to_customer_days` | float64 | 2,989 |
| `approval_to_delivery_days` | float64 | 3,040 |
| `purchase_to_delivery_days` | float64 | 2,965 |
| `delivery_vs_estimate_days` | float64 | 2,965 |
| `is_late` | boolean | 2,965 |

### `fact_payments`

**Purpose:** Payment-method, instalment and cash-collected analysis at transaction level

**Grain:** one payment transaction · **PK:** `order_id, payment_sequential` · **FKs:** `order_id -> fact_orders.order_id`, `customer_unique_id -> dim_customers.customer_unique_id`, `purchase_date -> dim_dates.date` · **Sources:** clean.payments, clean.orders, clean.customers · **Path:** `data/processed/analytical/fact_payments.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `order_id` | string | 0 |
| `payment_sequential` | Int64 | 0 |
| `payment_type` | category | 0 |
| `payment_installments` | Int64 | 0 |
| `payment_value` | float64 | 0 |
| `dq_payment_zero` | bool | 0 |
| `dq_installments_zero` | bool | 0 |
| `customer_unique_id` | string | 0 |
| `order_status` | category | 0 |
| `order_purchase_timestamp` | datetime64[us] | 0 |
| `purchase_date` | datetime64[us] | 0 |
| `in_candidate_window` | bool | 0 |

### `fact_reviews`

**Purpose:** Customer satisfaction analysis; review-level counts use distinct review_id (D-07, Q-04)

**Grain:** one review-order link · **PK:** `review_id, order_id` · **FKs:** `order_id -> fact_orders.order_id`, `customer_unique_id -> dim_customers.customer_unique_id`, `purchase_date -> dim_dates.date` · **Sources:** clean.reviews, clean.orders, clean.customers · **Path:** `data/processed/analytical/fact_reviews.parquet`

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
| `customer_unique_id` | string | 0 |
| `order_status` | category | 0 |
| `order_purchase_timestamp` | datetime64[us] | 0 |
| `purchase_date` | datetime64[us] | 0 |
| `in_candidate_window` | bool | 0 |
| `order_delivered_customer_date` | datetime64[us] | 2,865 |

### `dim_customers`

**Purpose:** Person-level customer analytics: RFM inputs, cohorts, retention, historical value (D-01). Measures span the full history: do not use them as prediction features; derive time-dependent features from the fact tables with an explicit cut-off date to avoid target leakage

**Grain:** one person (customer_unique_id) · **PK:** `customer_unique_id` · **FKs:** none · **Sources:** clean.customers, clean.orders, clean.order_items, clean.payments · **Path:** `data/processed/analytical/dim_customers.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `customer_unique_id` | string | 0 |
| `n_customer_ids` | int64 | 0 |
| `n_distinct_zips` | int64 | 0 |
| `n_distinct_states` | int64 | 0 |
| `first_purchase_ts` | datetime64[us] | 0 |
| `last_purchase_ts` | datetime64[us] | 0 |
| `cohort_month` | string | 0 |
| `n_orders` | int64 | 0 |
| `n_items` | int64 | 0 |
| `items_price_total` | float64 | 676 |
| `items_freight_total` | float64 | 676 |
| `payment_total` | float64 | 1 |
| `n_orders_delivered` | int64 | 0 |
| `n_items_delivered` | int64 | 0 |
| `items_price_total_delivered` | float64 | 2,738 |
| `items_freight_total_delivered` | float64 | 2,738 |
| `payment_total_delivered` | float64 | 2,739 |
| `first_delivered_purchase_ts` | datetime64[us] | 2,738 |
| `last_delivered_purchase_ts` | datetime64[us] | 2,738 |

### `dim_products`

**Purpose:** Product attributes, category labels (category_map) and quality flags

**Grain:** one product · **PK:** `product_id` · **FKs:** none · **Sources:** clean.products · **Path:** `data/processed/analytical/dim_products.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `product_id` | string | 0 |
| `product_category_name` | string | 610 |
| `category_en_clean` | string | 610 |
| `category_reporting` | string | 0 |
| `product_name_length` | Int64 | 610 |
| `product_description_length` | Int64 | 610 |
| `product_photos_qty` | Int64 | 610 |
| `product_weight_g` | Int64 | 2 |
| `weight_g_valid` | Int64 | 6 |
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
| `dq_density_suspicious` | bool | 0 |
| `dq_dims_16x2x11` | bool | 0 |

### `dim_sellers`

**Purpose:** Seller location and quality flags

**Grain:** one seller · **PK:** `seller_id` · **FKs:** none · **Sources:** clean.sellers · **Path:** `data/processed/analytical/dim_sellers.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `seller_id` | string | 0 |
| `seller_zip_code_prefix` | string | 0 |
| `seller_city` | string | 0 |
| `city_key` | string | 0 |
| `seller_state_std` | category | 0 |
| `dq_city_malformed` | bool | 0 |
| `lat_centroid` | float64 | 7 |
| `lng_centroid` | float64 | 7 |
| `dq_no_geolocation` | bool | 0 |

### `dim_dates`

**Purpose:** Calendar for time series, seasonality and forecasting

**Grain:** one calendar day from the first to the last event date · **PK:** `date` · **FKs:** none · **Sources:** clean.orders, clean.order_items, clean.reviews · **Path:** `data/processed/analytical/dim_dates.parquet`

| Column | dtype | Missing |
|---|---|---:|
| `date` | datetime64[us] | 0 |
| `year` | int64 | 0 |
| `quarter` | int64 | 0 |
| `month` | int64 | 0 |
| `year_month` | string | 0 |
| `iso_year` | int64 | 0 |
| `iso_week` | int64 | 0 |
| `day_of_week` | int64 | 0 |
| `day_name` | string | 0 |
| `is_weekend` | bool | 0 |
| `in_candidate_window` | bool | 0 |

## All checks

| ID | Class | Description | Expected | Observed | Result |
|---|---|---|---|---|---|
| `V01` | blocking | raw files unchanged from run start to validation (SHA-256); baseline verified | `True` | `True` | PASS |
| `C01.customers` | blocking | clean customers: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C01.orders` | blocking | clean orders: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C01.order_items` | blocking | clean order_items: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C01.payments` | blocking | clean payments: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C01.reviews` | blocking | clean reviews: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C01.products` | blocking | clean products: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C01.sellers` | blocking | clean sellers: lineage present and its raw SHA-256 equals the current raw file | `[]` | `[]` | PASS |
| `C02` | blocking | all clean inputs come from one clean run | `1` | `1` | PASS |
| `V03.fact_order_lines` | blocking | fact_order_lines: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.fact_order_lines` | blocking | fact_order_lines: grain key ('order_line_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `FK.fact_order_lines.order_id` | blocking | fact_order_lines.order_id -> fact_orders.order_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_order_lines.product_id` | blocking | fact_order_lines.product_id -> dim_products.product_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_order_lines.seller_id` | blocking | fact_order_lines.seller_id -> dim_sellers.seller_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_order_lines.customer_unique_id` | blocking | fact_order_lines.customer_unique_id -> dim_customers.customer_unique_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_order_lines.purchase_date` | blocking | fact_order_lines.purchase_date -> dim_dates.date: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `F01.fact_order_lines` | blocking | fact_order_lines: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.fact_orders` | blocking | fact_orders: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.fact_orders` | blocking | fact_orders: grain key ('order_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `FK.fact_orders.customer_unique_id` | blocking | fact_orders.customer_unique_id -> dim_customers.customer_unique_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_orders.purchase_date` | blocking | fact_orders.purchase_date -> dim_dates.date: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `F01.fact_orders` | blocking | fact_orders: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.fact_payments` | blocking | fact_payments: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.fact_payments` | blocking | fact_payments: grain key ('order_id', 'payment_sequential') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `FK.fact_payments.order_id` | blocking | fact_payments.order_id -> fact_orders.order_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_payments.customer_unique_id` | blocking | fact_payments.customer_unique_id -> dim_customers.customer_unique_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_payments.purchase_date` | blocking | fact_payments.purchase_date -> dim_dates.date: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `F01.fact_payments` | blocking | fact_payments: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.fact_reviews` | blocking | fact_reviews: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.fact_reviews` | blocking | fact_reviews: grain key ('review_id', 'order_id') unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `FK.fact_reviews.order_id` | blocking | fact_reviews.order_id -> fact_orders.order_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_reviews.customer_unique_id` | blocking | fact_reviews.customer_unique_id -> dim_customers.customer_unique_id: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `FK.fact_reviews.purchase_date` | blocking | fact_reviews.purchase_date -> dim_dates.date: no orphans (nulls count as orphans) | `0` | `0` | PASS |
| `F01.fact_reviews` | blocking | fact_reviews: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.dim_customers` | blocking | dim_customers: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.dim_customers` | blocking | dim_customers: grain key ('customer_unique_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `F01.dim_customers` | blocking | dim_customers: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.dim_products` | blocking | dim_products: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.dim_products` | blocking | dim_products: grain key ('product_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `F01.dim_products` | blocking | dim_products: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.dim_sellers` | blocking | dim_sellers: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.dim_sellers` | blocking | dim_sellers: grain key ('seller_id',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `F01.dim_sellers` | blocking | dim_sellers: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V03.dim_dates` | blocking | dim_dates: columns and dtypes match the declared schema | `[]` | `[]` | PASS |
| `V10.dim_dates` | blocking | dim_dates: grain key ('date',) unique and non-null | `[0, 0]` | `[0, 0]` | PASS |
| `F01.dim_dates` | blocking | dim_dates: boolean flag columns have no missing values | `[]` | `[]` | PASS |
| `V11.orders_x_customers.context` | blocking | join orders_x_customers.context (validate=one_to_one, how=left): row count preserved | `99441` | `99441` | PASS |
| `V11.order_lines_x_order_context` | blocking | join order_lines_x_order_context (validate=many_to_one, how=left): row count preserved | `102425` | `102425` | PASS |
| `V11.orders_x_customers` | blocking | join orders_x_customers (validate=one_to_one, how=left): row count preserved | `99441` | `99441` | PASS |
| `V11.orders_x_item_summary` | blocking | join orders_x_item_summary (validate=one_to_one, how=left): row count preserved | `99441` | `99441` | PASS |
| `V11.orders_x_payment_summary` | blocking | join orders_x_payment_summary (validate=one_to_one, how=left): row count preserved | `99441` | `99441` | PASS |
| `V11.orders_x_review_summary` | blocking | join orders_x_review_summary (validate=one_to_one, how=left): row count preserved | `99441` | `99441` | PASS |
| `V11.payments_x_order_context` | blocking | join payments_x_order_context (validate=many_to_one, how=left): row count preserved | `103886` | `103886` | PASS |
| `V11.reviews_x_order_context` | blocking | join reviews_x_order_context (validate=many_to_one, how=left): row count preserved | `99224` | `99224` | PASS |
| `V11.reviews_x_delivery_date` | blocking | join reviews_x_delivery_date (validate=many_to_one, how=left): row count preserved | `99224` | `99224` | PASS |
| `ROWS.fact_orders` | blocking | fact_orders: rows = clean orders rows | `99441` | `99441` | PASS |
| `A14.fact_orders` | blocking | fact_orders: every column carried from clean orders is identical (values, order) | `[]` | `[]` | PASS |
| `ROWS.fact_payments` | blocking | fact_payments: rows = clean payments rows | `103886` | `103886` | PASS |
| `A14.fact_payments` | blocking | fact_payments: every column carried from clean payments is identical (values, order) | `[]` | `[]` | PASS |
| `ROWS.fact_reviews` | blocking | fact_reviews: rows = clean reviews rows | `99224` | `99224` | PASS |
| `A14.fact_reviews` | blocking | fact_reviews: every column carried from clean reviews is identical (values, order) | `[]` | `[]` | PASS |
| `ROWS.dim_products` | blocking | dim_products: rows = clean products rows | `32951` | `32951` | PASS |
| `A14.dim_products` | blocking | dim_products: every column carried from clean products is identical (values, order) | `[]` | `[]` | PASS |
| `ROWS.dim_sellers` | blocking | dim_sellers: rows = clean sellers rows | `3095` | `3095` | PASS |
| `A14.dim_sellers` | blocking | dim_sellers: every column carried from clean sellers is identical (values, order) | `[]` | `[]` | PASS |
| `ROWS.dim_customers` | blocking | dim_customers: one row per distinct customer_unique_id | `96096` | `96096` | PASS |
| `ROWS.dim_dates` | blocking | dim_dates: contiguous days, no gaps | `1314` | `1314` | PASS |
| `V13.quantity_sum` | blocking | sum(quantity) = order_items rows | `112650` | `112650` | PASS |
| `V13.each_item_once` | blocking | every (order_id, order_item_id) is in exactly one line (duplicates, unmatched either side) | `[0, 0]` | `[0, 0]` | PASS |
| `V13.lossless_attributes` | blocking | every item's six attributes equal those of its line (nothing with different freight/deadline merged) | `0` | `0` | PASS |
| `V13.line_totals` | blocking | per line: line_price_total and freight_total = sums of their items (cents) | `[0, 0]` | `[0, 0]` | PASS |
| `V13.ids_match_quantity` | blocking | len(order_item_ids) = quantity on every line | `0` | `0` | PASS |
| `V09.fact_order_lines.line_price_total` | blocking | fact_order_lines.line_price_total: total = clean total (same run, cents) | `13591643.7` | `13591643.7` | PASS |
| `V09.fact_order_lines.freight_total` | blocking | fact_order_lines.freight_total: total = clean total (same run, cents) | `2251909.54` | `2251909.54` | PASS |
| `V09.fact_orders.items_price_total` | blocking | fact_orders.items_price_total: total = clean total (same run, cents) | `13591643.7` | `13591643.7` | PASS |
| `V09.fact_orders.items_freight_total` | blocking | fact_orders.items_freight_total: total = clean total (same run, cents) | `2251909.54` | `2251909.54` | PASS |
| `V09.fact_orders.payment_total` | blocking | fact_orders.payment_total: total = clean total (same run, cents) | `16008872.12` | `16008872.12` | PASS |
| `V09.fact_payments.payment_value` | blocking | fact_payments.payment_value: total = clean total (same run, cents) | `16008872.12` | `16008872.12` | PASS |
| `V09.dim_customers.items_price_total` | blocking | dim_customers.items_price_total: total = clean total (same run, cents) | `13591643.7` | `13591643.7` | PASS |
| `V09.dim_customers.items_freight_total` | blocking | dim_customers.items_freight_total: total = clean total (same run, cents) | `2251909.54` | `2251909.54` | PASS |
| `V09.dim_customers.payment_total` | blocking | dim_customers.payment_total: total = clean total (same run, cents) | `16008872.12` | `16008872.12` | PASS |
| `V09.dim_customers.delivered_variant` | blocking | dim_customers delivered-variant totals = fact_orders totals over delivered orders | `[13221498.11, 2198275.64, 15422461.77]` | `[13221498.11, 2198275.64, 15422461.77]` | PASS |
| `S.item_count` | blocking | fact_orders.item_count / order_line_count = quantity sum / line count per order | `[0, 0]` | `[0, 0]` | PASS |
| `S.row_counts` | blocking | sums of item_count / payment_rows / review_count = clean rows | `[112650, 103886, 99224]` | `[112650, 103886, 99224]` | PASS |
| `S.payment_minus_items_defined` | blocking | payment_minus_items present exactly for orders with items and payments | `True` | `True` | PASS |
| `S.mismatch_flag` | blocking | dq_payment_items_mismatch = |payment_minus_items| > tolerance | `True` | `True` | PASS |
| `S.duration_flags` | blocking | each guarded duration is missing wherever its chronology flag is True | `0` | `0` | PASS |
| `S.duration_non_negative` | blocking | guarded durations are never negative where defined | `0` | `0` | PASS |
| `S.is_late_defined` | blocking | is_late is missing exactly where the delivery date is missing | `True` | `True` | PASS |
| `S.customer_order_seq` | blocking | one first order per person; max sequence = n_orders | `[96096, True]` | `[96096, True]` | PASS |
| `S.dim_customers_counts` | blocking | dim_customers: sum(n_orders) = orders, sum(n_items) = order_items rows | `[99441, 112650]` | `[99441, 112650]` | PASS |
| `R.order_lines` | regression | order_lines (strategy 11.3 / NB02-NB03) | `102425` | `102425` | PASS |
| `R.orders` | regression | orders (strategy 11.3 / NB02-NB03) | `99441` | `99441` | PASS |
| `R.persons` | regression | persons (strategy 11.3 / NB02-NB03) | `96096` | `96096` | PASS |
| `R.persons_with_several_orders` | regression | persons_with_several_orders (strategy 11.3 / NB02-NB03) | `2997` | `2997` | PASS |
| `R.dq_payment_items_mismatch` | regression | dq_payment_items_mismatch (strategy 11.3 / NB02-NB03) | `303` | `303` | PASS |
| `R.orders_reconciled` | regression | orders_reconciled (strategy 11.3 / NB02-NB03) | `98665` | `98665` | PASS |
| `R.mismatch_overpaid` | regression | mismatch_overpaid (strategy 11.3 / NB02-NB03) | `264` | `264` | PASS |
| `R.mismatch_underpaid` | regression | mismatch_underpaid (strategy 11.3 / NB02-NB03) | `39` | `39` | PASS |
| `R.mismatch_gt_1` | regression | mismatch_gt_1 (strategy 11.3 / NB02-NB03) | `249` | `249` | PASS |
| `R.orders_without_items` | regression | orders_without_items (strategy 11.3 / NB02-NB03) | `775` | `775` | PASS |
| `R.orders_without_payment` | regression | orders_without_payment (strategy 11.3 / NB02-NB03) | `1` | `1` | PASS |
| `R.orders_with_several_reviews` | regression | orders_with_several_reviews (strategy 11.3 / NB02-NB03) | `547` | `547` | PASS |
| `R.late_deliveries` | regression | late_deliveries (strategy 11.3 / NB02-NB03) | `6535` | `6535` | PASS |
| `R.delivery_over_60_days` | regression | delivery_over_60_days (strategy 11.3 / NB02-NB03) | `306` | `306` | PASS |
| `R.max_quantity` | regression | max quantity per line <= 20 (D-15) | `True` | `True` | PASS |
| `R.mismatch_range` | regression | min / max payment_minus_items among mismatched orders (NB03 6.2) | `[-51.62, 182.81]` | `[-51.62, 182.81]` | PASS |
| `R.orders_without_items_by_status` | regression | status of orders without items (Q-16) | `{'unavailable': 603, 'canceled': 164, 'created': 5, 'invoiced': 2, 'shipped': 1}` | `{'unavailable': 603, 'canceled': 164, 'created': 5, 'invoiced': 2, 'shipped': 1}` | PASS |
| `R.negative_unguarded_durations` | regression | approval before purchase / delivery before purchase (NB03 5.2: 0 / 0) | `[0, 0]` | `[0, 0]` | PASS |
| `R.distinct_review_id` | regression | distinct review_id in fact_reviews (NB02 5.1) | `98410` | `98410` | PASS |
| `W01` | warning | (order_id, product_id, seller_id, price) groups with >1 freight_value or shipping_limit_date (rejected coarser grain, D-15) | `None` | `0` | logged |
| `W04` | warning | orders / sellers that receive a centroid via zip_lookup | `None` | `{'orders_with_customer_centroid': 99162, 'orders_share_pct': 99.719, 'sellers_with_centroid': 3088, 'sellers_share_pct': 99.774}` | logged |
| `W05` | warning | fact rows outside the candidate window 2017-01..2018-08 (flagged, not removed; Q-11) | `None` | `{'fact_order_lines': 333, 'fact_orders': 349, 'fact_payments': 366, 'fact_reviews': 345}` | logged |
| `W.lines_with_quantity_gt_1` | warning | order lines with quantity > 1 | `None` | `7088` | logged |
| `P01.fact_order_lines` | blocking | fact_order_lines: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.fact_orders` | blocking | fact_orders: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.fact_payments` | blocking | fact_payments: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.fact_reviews` | blocking | fact_reviews: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.dim_customers` | blocking | dim_customers: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.dim_products` | blocking | dim_products: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.dim_sellers` | blocking | dim_sellers: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `P01.dim_dates` | blocking | dim_dates: Parquet read-back identical (values and dtypes) | `None` | `None` | PASS |
| `C03` | blocking | clean input files unchanged during the run (SHA-256) | `True` | `True` | PASS |
| `V01.end` | blocking | raw files unchanged at the end of the run (SHA-256) | `True` | `True` | PASS |
