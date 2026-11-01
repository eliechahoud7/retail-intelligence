# PostgreSQL Warehouse Schema Design

**Project:** Retail Intelligence · **Task:** 5.2.2 (design only, no DDL executed) · **Status:** Approved (decisions Q1–Q5 in section 9)
**Target:** PostgreSQL 18.6, database `retail_intelligence`, owner of all objects: application role `retail_app`
**Source:** the eight Gold Parquet outputs in `data/processed/analytical/` (analytical run `baea48c9b8ac`, commit `6d2ca67`), whose contract is `src/analytical/schema.py` (`TABLES`)

The warehouse is a **faithful relational copy of the validated Gold star schema**: same tables, same grain, same column names, same values. It adds what Parquet cannot enforce on its own: declared types with exact money precision, primary and foreign keys, and indexes for analytical joins. No column, relationship or business rule is added beyond those listed in section 7.

---

## 1. Schema overview

| Schema | Purpose | Contents | Created when |
|---|---|---|---|
| `warehouse` | Dimensional model, loaded from Gold | 4 fact tables, 4 dimension tables | Task 5.2.3 (implementation) |
| `analytics` | Analytical views built on `warehouse` | Empty until views are designed | Task 5.2.3, left empty |

**No `raw` schema.** The raw, staging and clean layers already exist as versioned, checksummed Parquet with full lineage (D-02, D-14). Loading them into PostgreSQL would duplicate data and validation with no analytical benefit. PostgreSQL receives only the Gold output.

Both schemas are owned by `retail_app`. It already holds `CREATE` on the database (Task 5.2.1), so no superuser access is needed. The `public` schema is not used.

**Naming:** table and column names are identical to the Gold Parquet names. All of them are lower-case snake_case, valid unquoted PostgreSQL identifiers; `date`, `year`, `month` and `quarter` are non-reserved and need no quoting.

---

## 2. Star schema and relationships

```
                         dim_dates (date)
                              ▲  purchase_date (all 4 facts)
                              │
dim_customers ◄── customer_unique_id ── fact_orders (order_id) ◄── order_id ── fact_payments
(customer_unique_id)          │              ▲        ▲
                              │              │        └──── order_id ── fact_reviews
                              │          order_id
                              │              │
                              └──────── fact_order_lines ──► product_id ──► dim_products
                                                         └─► seller_id  ──► dim_sellers
```

All 13 foreign keys reference a **primary key**, so every fact-to-dimension join is many-to-one and **cannot fan out**. They are exactly the 13 relationships declared in `src/analytical/schema.py` and validated in Task 4.4 (0 orphans, 0 null foreign keys).

| # | Child table.column | References | Gold cardinality |
|---:|---|---|---|
| 1 | `fact_order_lines.order_id` | `fact_orders.order_id` | many-to-one (98,666 orders have lines) |
| 2 | `fact_order_lines.product_id` | `dim_products.product_id` | many-to-one |
| 3 | `fact_order_lines.seller_id` | `dim_sellers.seller_id` | many-to-one |
| 4 | `fact_order_lines.customer_unique_id` | `dim_customers.customer_unique_id` | many-to-one |
| 5 | `fact_order_lines.purchase_date` | `dim_dates.date` | many-to-one |
| 6 | `fact_orders.customer_unique_id` | `dim_customers.customer_unique_id` | many-to-one |
| 7 | `fact_orders.purchase_date` | `dim_dates.date` | many-to-one |
| 8 | `fact_payments.order_id` | `fact_orders.order_id` | many-to-one (99,440 orders have payments) |
| 9 | `fact_payments.customer_unique_id` | `dim_customers.customer_unique_id` | many-to-one |
| 10 | `fact_payments.purchase_date` | `dim_dates.date` | many-to-one |
| 11 | `fact_reviews.order_id` | `fact_orders.order_id` | many-to-one (98,673 orders have reviews) |
| 12 | `fact_reviews.customer_unique_id` | `dim_customers.customer_unique_id` | many-to-one |
| 13 | `fact_reviews.purchase_date` | `dim_dates.date` | many-to-one |

**Load order** (it follows from the foreign keys): `dim_dates`, `dim_customers`, `dim_products`, `dim_sellers` → `fact_orders` → `fact_order_lines`, `fact_payments`, `fact_reviews`.

Foreign keys use the default `NO ACTION`; there is no `ON DELETE CASCADE`, so a dimension row can never silently remove facts.

---

## 3. Type mapping rules

| Gold dtype (pandas / Arrow) | PostgreSQL type | Rule |
|---|---|---|
| `string` (32-char hex IDs, codes, free text) | `text` | PostgreSQL's recommended string type. No length limits are invented; key formats are already enforced upstream (NB02, V06) |
| `category` (`order_status`, `payment_type`, `*_state_std`) | `text` | The vocabularies are observed, not business rules (strategy 11.3.2), so no `CHECK`/`ENUM` |
| `float64` **monetary** | `numeric(12,2)` | Exact cents. Every value has at most 2 decimals. The largest row value is 13,664.08 and the largest column sum ~16.0 M, both well within `numeric(12,2)` (10 integer digits). Loading must **round to 2 dp** to remove float noise (e.g. `1794.9600000000003`) |
| `float64` coordinates, durations | `double precision` | Continuous measures; Gold's float64 is kept exactly |
| `int64` / `Int64` counts and ordinals | `integer` | All observed ranges are far below 2³¹ (largest: 40,425 g, 3,992 characters, 2,020) |
| `bool` | `boolean NOT NULL` | Flags never contain missing values (Gold check F01) |
| `boolean` (nullable) | `boolean` (nullable) | Only `fact_orders.is_late` |
| `datetime64[us]` event timestamps | `timestamp(0) without time zone` | The source timezone is unknown (Q-14), so `timestamptz` would invent one. Every value is a whole second, so precision 0 loses nothing |
| `datetime64[us]` that is always midnight and used as a date key (`purchase_date`, `dim_dates.date`) | `date` | Calendar-day keys. The conversion is lossless (100% midnight values) |
| `list<int64>` (`order_item_ids`) | `integer[]` | Preserves the D-15 traceability list as stored in Gold (values 1–21, 1–20 elements) |

**Nullability rule:** a column is `NOT NULL` exactly when the Gold contract never produces a missing value in it, which is also what the data shows. Every nullable column below has a documented reason, so the loader preserves Gold missing values and never imputes.

---

## 4. Table definitions

The column order equals the Gold column order. **N** = nullable; blank = `NOT NULL`. Null counts are from analytical run `baea48c9b8ac`.

### 4.1 `warehouse.fact_order_lines`, Parquet `fact_order_lines.parquet` (102,425 rows)

**Grain:** one order line = the order-item rows identical in `(order_id, product_id, seller_id, price, freight_value, shipping_limit_date)` (D-15). **PK:** `order_line_id`.

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `order_line_id` | `text` | | PK; `order_id:first_order_item_id` |
| `order_id` | `text` | | FK → `fact_orders` |
| `first_order_item_id` | `integer` | | |
| `product_id` | `text` | | FK → `dim_products` |
| `seller_id` | `text` | | FK → `dim_sellers` |
| `shipping_limit_date` | `timestamp(0)` | | |
| `quantity` | `integer` | | 1–20 |
| `unit_price` | `numeric(12,2)` | | |
| `freight_value_per_row` | `numeric(12,2)` | | |
| `line_price_total` | `numeric(12,2)` | | = `unit_price × quantity` |
| `freight_total` | `numeric(12,2)` | | |
| `order_item_ids` | `integer[]` | | element count = `quantity` |
| `dq_shipping_limit_out_of_range` | `boolean` | | |
| `customer_unique_id` | `text` | | FK → `dim_customers` |
| `order_status` | `text` | | |
| `order_purchase_timestamp` | `timestamp(0)` | | |
| `purchase_date` | `date` | | FK → `dim_dates` |
| `in_candidate_window` | `boolean` | | |

**Unique constraint (grain guard):** `UNIQUE (order_id, product_id, seller_id, unit_price, freight_value_per_row, shipping_limit_date)`, the D-15 six-attribute grain. A duplicated or mis-aggregated load fails instead of inflating units or sales.

### 4.2 `warehouse.fact_orders`, Parquet `fact_orders.parquet` (99,441 rows)

**Grain:** one order. **PK:** `order_id`. **Unique:** `customer_id`, because customers ↔ orders is strictly one-to-one (D-01; 99,441 distinct values).

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `order_id` | `text` | | PK |
| `customer_id` | `text` | | UNIQUE (per-order customer key) |
| `customer_unique_id` | `text` | | FK → `dim_customers` |
| `order_status` | `text` | | |
| `order_purchase_timestamp` | `timestamp(0)` | | |
| `order_approved_at` | `timestamp(0)` | N | 160 never approved |
| `order_delivered_carrier_date` | `timestamp(0)` | N | 1,783 |
| `order_delivered_customer_date` | `timestamp(0)` | N | 2,965 |
| `order_estimated_delivery_date` | `timestamp(0)` | | always midnight (see Q2) |
| `purchase_date` | `date` | | FK → `dim_dates` |
| `purchase_year_month` | `text` | | `YYYY-MM` |
| `in_candidate_window` | `boolean` | | |
| `customer_order_seq` | `integer` | | |
| `is_first_order` | `boolean` | | |
| `customer_zip_code_prefix` | `text` | | 5-character code; leading zeros preserved |
| `customer_city_key` | `text` | | |
| `customer_state_std` | `text` | | |
| `customer_lat_centroid`, `customer_lng_centroid` | `double precision` | N | 279 without geolocation |
| `dq_customer_no_geolocation` | `boolean` | | |
| 9 order flags `dq_delivered_missing_approval` … `dq_same_person_same_second` | `boolean` | | in Gold order |
| `has_items` | `boolean` | | |
| `item_count`, `order_line_count`, `distinct_products`, `distinct_sellers` | `integer` | | 0 when the order has no items |
| `items_price_total`, `items_freight_total` | `numeric(12,2)` | N | 775 orders without items (Q-16) |
| `has_payment` | `boolean` | | |
| `payment_rows` | `integer` | | |
| `payment_total` | `numeric(12,2)` | N | 1 order without payment |
| `payment_types` | `text` | N | e.g. `credit_card\|voucher` |
| `has_voucher` | `boolean` | | |
| `max_installments` | `integer` | N | 3 (no payment, or only zero-instalment rows; D-16) |
| `payment_minus_items` | `numeric(12,2)` | N | 776; signed, −51.62 … 182.81 |
| `dq_payment_items_mismatch` | `boolean` | | 303 TRUE |
| `review_count` | `integer` | | |
| `approval_hours`, `purchase_to_carrier_days`, `approval_to_carrier_days`, `carrier_to_customer_days`, `approval_to_delivery_days`, `purchase_to_delivery_days`, `delivery_vs_estimate_days` | `double precision` | N | D-08 guarded durations |
| `is_late` | `boolean` | N | 2,965 without a delivery date |

### 4.3 `warehouse.fact_payments`, Parquet `fact_payments.parquet` (103,886 rows)

**Grain:** one payment transaction. **PK:** `(order_id, payment_sequential)`.

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `order_id` | `text` | | PK part; FK → `fact_orders` |
| `payment_sequential` | `integer` | | PK part |
| `payment_type` | `text` | | |
| `payment_installments` | `integer` | | recorded 0 kept (D-16) |
| `payment_value` | `numeric(12,2)` | | |
| `dq_payment_zero`, `dq_installments_zero` | `boolean` | | |
| `customer_unique_id` | `text` | | FK → `dim_customers` |
| `order_status` | `text` | | |
| `order_purchase_timestamp` | `timestamp(0)` | | |
| `purchase_date` | `date` | | FK → `dim_dates` |
| `in_candidate_window` | `boolean` | | |

### 4.4 `warehouse.fact_reviews`, Parquet `fact_reviews.parquet` (99,224 rows)

**Grain:** one review–order link. **PK:** `(review_id, order_id)`. `review_id` alone is **not** unique (98,410 distinct; D-07), so there is no unique constraint on it.

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `review_id` | `text` | | PK part |
| `order_id` | `text` | | PK part; FK → `fact_orders` |
| `review_score` | `integer` | | |
| `review_comment_title` | `text` | N | 87,656 (optional field) |
| `review_comment_message` | `text` | N | 58,247 (optional field) |
| `review_creation_date` | `timestamp(0)` | | 85 non-midnight values, so it stays a timestamp |
| `review_answer_timestamp` | `timestamp(0)` | | |
| 6 flags `has_comment_title` … `dq_review_before_delivery` | `boolean` | | |
| `customer_unique_id` | `text` | | FK → `dim_customers` |
| `order_status` | `text` | | |
| `order_purchase_timestamp` | `timestamp(0)` | | |
| `purchase_date` | `date` | | FK → `dim_dates` |
| `in_candidate_window` | `boolean` | | |
| `order_delivered_customer_date` | `timestamp(0)` | N | 2,865 |

### 4.5 `warehouse.dim_customers`, Parquet `dim_customers.parquet` (96,096 rows)

**Grain:** one person. **PK:** `customer_unique_id`. Measures cover the full history; they are not leakage-safe model features (Task 4.4).

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `customer_unique_id` | `text` | | PK |
| `n_customer_ids`, `n_distinct_zips`, `n_distinct_states` | `integer` | | |
| `first_purchase_ts`, `last_purchase_ts` | `timestamp(0)` | | |
| `cohort_month` | `text` | | `YYYY-MM` |
| `n_orders`, `n_items` | `integer` | | |
| `items_price_total`, `items_freight_total` | `numeric(12,2)` | N | 676 people with no items |
| `payment_total` | `numeric(12,2)` | N | 1 |
| `n_orders_delivered`, `n_items_delivered` | `integer` | | |
| `items_price_total_delivered`, `items_freight_total_delivered` | `numeric(12,2)` | N | 2,738 |
| `payment_total_delivered` | `numeric(12,2)` | N | 2,739 |
| `first_delivered_purchase_ts`, `last_delivered_purchase_ts` | `timestamp(0)` | N | 2,738 with no delivered order |

### 4.6 `warehouse.dim_products`, Parquet `dim_products.parquet` (32,951 rows)

**Grain:** one product. **PK:** `product_id`.

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `product_id` | `text` | | PK |
| `product_category_name`, `category_en_clean` | `text` | N | 610 without a category (raw value kept missing) |
| `category_reporting` | `text` | | includes the provisional label `uncategorized` (Q-05) |
| `product_name_length`, `product_description_length`, `product_photos_qty` | `integer` | N | 610 |
| `product_weight_g` | `integer` | N | 2 |
| `weight_g_valid` | `integer` | N | 6 (2 missing + 4 zero weight) |
| `product_length_cm`, `product_height_cm`, `product_width_cm` | `integer` | N | 2 |
| 14 flags `dq_missing_category` … `dq_dims_16x2x11` | `boolean` | | in Gold order |

### 4.7 `warehouse.dim_sellers`, Parquet `dim_sellers.parquet` (3,095 rows)

**Grain:** one seller. **PK:** `seller_id`.

| Column | PostgreSQL type | N | Notes |
|---|---|:-:|---|
| `seller_id` | `text` | | PK |
| `seller_zip_code_prefix` | `text` | | leading zeros preserved |
| `seller_city`, `city_key`, `seller_state_std` | `text` | | raw city kept; 24 flagged malformed |
| `dq_city_malformed` | `boolean` | | |
| `lat_centroid`, `lng_centroid` | `double precision` | N | 7 |
| `dq_no_geolocation` | `boolean` | | |

### 4.8 `warehouse.dim_dates`, Parquet `dim_dates.parquet` (1,314 rows)

**Grain:** one calendar day, 2016-09-04 … 2020-04-09 (contiguous). **PK:** `date`.

| Column | PostgreSQL type | N |
|---|---|:-:|
| `date` | `date` | |
| `year`, `quarter`, `month`, `iso_year`, `iso_week`, `day_of_week` | `integer` | |
| `year_month`, `day_name` | `text` | |
| `is_weekend`, `in_candidate_window` | `boolean` | |

---

## 5. Index strategy

PostgreSQL indexes primary keys and unique constraints automatically, but **not foreign-key columns**. Indexes are added only where analytical joins or filters use them, plus the indexes needed to keep foreign-key checks on dimension deletes or updates fast.

| Table | Index | Rationale |
|---|---|---|
| `fact_order_lines` | covered by the six-attribute UNIQUE index (leading `order_id`) | order → line joins |
| `fact_order_lines` | `(product_id)` | product and category analysis; FK |
| `fact_order_lines` | `(seller_id)` | seller performance; FK |
| `fact_order_lines` | `(customer_unique_id)` | customer basket analysis; FK |
| `fact_order_lines` | `(purchase_date)` | demand time series; FK |
| `fact_orders` | `(customer_unique_id, order_purchase_timestamp)` | cohort, retention and repeat-purchase scans per person in time order; serves the FK |
| `fact_orders` | `(purchase_date)` | sales over time; FK |
| `fact_payments` | covered by PK `(order_id, payment_sequential)` | order → payment joins |
| `fact_payments` | `(customer_unique_id)`, `(purchase_date)` | FKs; customer- and time-level payment analysis |
| `fact_reviews` | `(order_id)` | order → review joins (the PK leads with `review_id`) |
| `fact_reviews` | `(customer_unique_id)`, `(purchase_date)` | FKs; satisfaction by customer and over time |

**Not indexed, deliberately:** low-cardinality columns (`order_status` with 8 values, the boolean flags, `in_candidate_window`), and the dimensions beyond their primary keys (at most 96k rows). At about 100k fact rows, sequential scans are cheap for those filters. Indexes can be revisited from real query plans once the `analytics` views exist.

---

## 6. Parquet-to-PostgreSQL mapping

| Gold Parquet (`data/processed/analytical/`) | PostgreSQL table | Rows | Columns | PK |
|---|---|---:|---:|---|
| `fact_order_lines.parquet` | `warehouse.fact_order_lines` | 102,425 | 18 | `order_line_id` |
| `fact_orders.parquet` | `warehouse.fact_orders` | 99,441 | 53 | `order_id` |
| `fact_payments.parquet` | `warehouse.fact_payments` | 103,886 | 12 | `(order_id, payment_sequential)` |
| `fact_reviews.parquet` | `warehouse.fact_reviews` | 99,224 | 19 | `(review_id, order_id)` |
| `dim_customers.parquet` | `warehouse.dim_customers` | 96,096 | 19 | `customer_unique_id` |
| `dim_products.parquet` | `warehouse.dim_products` | 32,951 | 26 | `product_id` |
| `dim_sellers.parquet` | `warehouse.dim_sellers` | 3,095 | 9 | `seller_id` |
| `dim_dates.parquet` | `warehouse.dim_dates` | 1,314 | 11 | `date` |

Every Gold column maps to exactly one PostgreSQL column with the same name and position: no columns are dropped, renamed, derived or added. The only value-level transformations are type conversions:
- float money → `numeric(12,2)`, rounded to cents;
- midnight `purchase_date` / `date` → `date`;
- `list<int64>` → `integer[]`;
- `category` → `text`.

Each conversion is lossless for the current data and must be verified on load (section 8).

---

## 7. Design decisions

| # | Decision | Reason |
|---|---|---|
| W-01 | Two schemas, `warehouse` and `analytics`; no `raw` | Gold is the validated integration layer; earlier layers stay in Parquet with lineage |
| W-02 | Mirror Gold exactly (names, grain, columns) | One source of truth. The warehouse is a load target, not a second transformation layer, so the D-13/D-15 logic stays in `src/analytical` |
| W-03 | `numeric(12,2)` for every monetary column | Exact cent arithmetic. Sums reconcile to the strategy baselines exactly instead of within float tolerance |
| W-04 | `timestamp(0) without time zone` for event times | The timezone is unknown (Q-14); every value is a whole second |
| W-05 | `date` for `purchase_date` and `dim_dates.date` | Calendar-day keys; 100% midnight values; type-identical foreign key |
| W-06 | The 13 Gold foreign keys enforced as constraints | Referential integrity in the database. All target PKs, so joins cannot fan out |
| W-07 | Grain guards: PKs on every table; UNIQUE six-attribute key on `fact_order_lines`; UNIQUE `customer_id` on `fact_orders` | A duplicate load or wrong aggregation fails at insert time |
| W-08 | `CHECK` constraints limited to pipeline invariants: `quantity >= 1`, `cardinality(order_item_ids) = quantity` and `line_price_total = unit_price * quantity` on `fact_order_lines`; `customer_zip_code_prefix` / `seller_zip_code_prefix ~ '^[0-9]{5}$'` | These are the Task 4.4 V13 and V06 blocking invariants, true for any input. Observed vocabularies and domain counts stay out: they are regression facts, not business rules (11.3.2, Q-18) |
| W-09 | `text` for strings and categories; no `ENUM` | No invented limits or vocabularies; categories stay extensible |
| W-10 | `integer[]` for `order_item_ids` | Keeps the Gold traceability list as is (no bridge table). It can be unnested in a view if needed |
| W-11 | Denormalised order attributes on facts (`order_status`, `order_purchase_timestamp`, `purchase_date`, `in_candidate_window`, `customer_unique_id`) kept as in Gold | Part of the Gold design. Their consistency with `fact_orders` is guaranteed by the Gold build and can be verified after load (section 8) |
| W-12 | All objects owned by `retail_app`; the `postgres` login is not used for warehouse objects | Least privilege (Task 5.2.1) |
| W-13 | Full refresh per Gold run: in one transaction, truncate and reload all 8 tables | Gold is rebuilt in full each run, so an incremental load would add complexity without benefit. Atomicity keeps dimensions and facts consistent |

---

## 8. Validation of this design against the Gold outputs

| Check | Result |
|---|---|
| Every Gold column has a mapping with a compatible type | Yes: 167 columns across 8 tables, names and order identical |
| `NOT NULL` columns have 0 nulls in Gold; nullable columns are exactly those with nulls | Yes (null counts in section 4, run `baea48c9b8ac`) |
| PKs unique and non-null in Gold | Yes, all 8 (profiled; also Gold check V10) |
| FKs: no orphans, no null FKs | Yes, all 13 (Gold FK checks; nulls count as orphans since Task 4.4) |
| Grain preserved | Yes: same PKs; the D-15 six-attribute key is enforced as UNIQUE |
| No fan-out | Yes: every FK references a PK; the facts are never joined to each other except many-to-one into `fact_orders` |
| Money precision | Every monetary value has ≤ 2 decimals; max \|value\| 13,664.08; max column sum ~16.0 M; all fit `numeric(12,2)` |
| Dates and timestamps | Whole-second timestamps; `purchase_date` / `date` 100% midnight; `review_creation_date` not midnight-only, so it stays a timestamp |
| CHECK invariants hold in Gold | `quantity` 1–20; list length = quantity and line totals (V13, 0 violations); zip codes 5 digits (V06) |

**Post-load verification required in implementation (Task 5.2.3):**
- row counts per table = the Gold manifest;
- monetary sums in SQL = the Gold totals exactly (13,591,643.70 / 2,251,909.54 / 16,008,872.12);
- null counts per column = Gold;
- a per-table content comparison of PostgreSQL read back against Parquet, with types normalised;
- denormalised order attributes on the facts equal `fact_orders`;
- record the loaded Gold `run_id` and content hashes in `warehouse.load_run` (Q1).

---

## 9. Decisions on the open questions

All five recommendations were accepted at review.

| ID | Question | Decision |
|---|---|---|
| Q1 | Should load lineage be stored in PostgreSQL? | **Yes.** Add `warehouse.load_run` (load ID, Gold run ID, manifest path, row counts, content hashes, timestamps). It is lineage metadata, not a Gold table, so it is outside the Gold-to-warehouse mapping in section 6 |
| Q2 | `order_estimated_delivery_date` is always midnight. Keep `timestamp(0)` or use `date`? | **Keep `timestamp(0)`.** Only the date keys (`purchase_date`, `dim_dates.date`) become `date`; views can cast |
| Q3 | Read access for BI tools and analysts | **Deferred.** A separate read-only role (`USAGE` on `analytics`, `SELECT` on views) is added when dashboards start |
| Q4 | Include the W-08 `CHECK` constraints? | **Include** all four |
| Q5 | When should the `analytics` schema be created? | **Now:** created empty together with `warehouse` in Task 5.2.3, so permissions are set once |

---

*Design only: no schema, table, index or data was created in PostgreSQL. No Parquet file, pipeline code, `.env` or server configuration was modified.*
