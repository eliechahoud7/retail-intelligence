# Data Preparation Strategy

**Project:** Retail Intelligence — Customer, Sales & Demand Analytics
**Phase:** Task 4 — Data Cleaning & Preparation (strategy, before implementation)
**Status:** Draft for approval
**Evidence base:**

| Ref | Notebook | What it established |
|---|---|---|
| NB01 | `notebooks/01_dataset_inventory.ipynb` | Structure, row/column counts, types as inferred, first quality signals |
| NB02 | `notebooks/02_relationship_analysis.ipynb` | Primary/foreign keys, referential integrity, cardinality, join behaviour, ERD |
| NB03 | `notebooks/03_data_quality_assessment.ipynb` | 52-rule data-quality register (4 High · 13 Medium · 22 Low · 13 Info) |

Every number in this document was measured in one of these notebooks. A reference such as *(NB03 §5.2)* points to the section where it was measured. Where the data cannot settle a question, the document says so and the question is listed in Section 14. **No business rule is invented here.**

---

## Contents

1. [Data granularity, identifiers and keys](#1-data-granularity-identifiers-and-keys)
2. [Data preservation and layer architecture](#2-data-preservation-and-layer-architecture)
3. [Data types](#3-data-types)
4. [Missing values](#4-missing-values)
5. [Duplicates](#5-duplicates)
6. [Chronological inconsistencies](#6-chronological-inconsistencies)
7. [Numerical anomalies](#7-numerical-anomalies)
8. [Categorical inconsistencies](#8-categorical-inconsistencies)
9. [Payment reconciliation and double-counting](#9-payment-reconciliation-and-double-counting)
10. [Geolocation](#10-geolocation)
11. [Data validation](#11-data-validation)
12. [Data lineage](#12-data-lineage)
13. [Processing order](#13-processing-order)
14. [Decisions requiring investigation](#14-decisions-requiring-investigation)
15. [Summary of major decisions and unresolved questions](#15-summary-of-major-decisions-and-unresolved-questions)

---

## Guiding principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **Raw is immutable.** | Files in `data/raw/` are never edited, renamed, moved or overwritten. Their SHA-256 hashes are checked at the start and end of every pipeline run. |
| P2 | **Flag, don't delete.** | Invalid or suspicious values are *flagged* in dedicated columns. Rows are not removed from the clean layer. Excluding them is a per-analysis decision made explicit in code. |
| P3 | **Never invent values.** | No imputed dates, no fabricated categories, no forced reconciliation. Where a value is unusable, it may be set to missing in a *derived* column, and the original is always kept. |
| P4 | **Preserve originals for traceability.** | Every standardised column keeps its raw counterpart (`*_raw`) or is reproducible from a versioned mapping table. |
| P5 | **Declare grain before joining.** | Every analytical dataset states its grain. One-to-many tables are aggregated to that grain *before* joining, and every merge declares `validate=`. |
| P6 | **Reproducible and measured.** | Each transformation is deterministic, logged with row counts, and verified by automated checks (Section 11). |
| P7 | **Business decisions belong to the business.** | Where the data allows several valid treatments, the options are documented and the choice is escalated (Section 14). |

**Decision template.** Each major decision below follows the same structure: **Observed issue → Proposed treatment → Technical justification → Business implication → Validation method.** Decisions carry an ID (`D-xx`) and open questions an ID (`Q-xx`).

---

## 1. Data granularity, identifiers and keys

### 1.1 Grain of each dataset

The grain is what **one row represents**. It was established from data, not from column names (NB02 §2, §4).

| Dataset | Rows | One row represents | Primary key (validated) | Notes |
|---|---:|---|---|---|
| `orders` | 99,441 | One order | `order_id` | `customer_id` is also unique here because it is per order (see 1.2) |
| `order_items` | 112,650 | One **unit** line within an order | `(order_id, order_item_id)` | Quantity is stored as repeated rows: 7,088 (order, product) pairs repeat, up to 20 rows (NB02 §5.4) |
| `payments` | 103,886 | One payment transaction within an order | `(order_id, payment_sequential)` | 80 orders have sequences not starting at 1 (NB02 §5.4) |
| `reviews` | 99,224 | One review–order link | `(review_id, order_id)` | `review_id` alone is not unique: 789 IDs on 1,603 rows (NB02 §5.1) |
| `customers` | 99,441 | One customer **record per order** | `customer_id` | The person is `customer_unique_id` (96,096 distinct) |
| `products` | 32,951 | One product listing | `product_id` | |
| `sellers` | 3,095 | One seller | `seller_id` | |
| `translation` | 71 | One category name mapping (PT → EN) | `product_category_name` | The English name is also unique, but it is not the referenced key |
| `geolocation` | 1,000,163 | One **coordinate point** associated with a zip prefix | **None possible** | 261,831 rows are exact duplicates, so no column combination can identify a row (NB02 §2.3) |

### 1.2 Identifier definitions

| Identifier | Definition (as measured) | Format |
|---|---|---|
| `order_id` | Unique order identifier | 32-char lowercase hex (NB02 §1.4) |
| `customer_id` | **Per-order** customer key. customers ↔ orders is strictly **one-to-one** (NB02 §4) | 32-char hex |
| `customer_unique_id` | **Person-level** key. 2,997 persons have 2–17 `customer_id`s, one per order (NB02 §5.2) | 32-char hex |
| `product_id`, `seller_id` | Unique product / seller identifiers | 32-char hex |
| `review_id` | Review identifier. **Can link one review to several orders** of the same person (NB02 §5.1) | 32-char hex |
| `order_item_id` | Line counter 1…n within an order. Gap-free in every order (NB02 §5.4) | integer |
| `payment_sequential` | Payment counter within an order. **Not** gap-free (80 orders) | integer |
| `*_zip_code_prefix` | 5-digit postal prefix. **Stored as 5-character text in the raw files** (NB03 §4.3) | text `\d{5}` |

### 1.3 Foreign keys and cardinality

All values below were measured in NB02 §3–4.

| Relationship | Child → parent | Integrity | Cardinality | Parent rows with no children |
|---|---|---:|---|---:|
| customers ↔ orders | `orders.customer_id → customers.customer_id` | 100% | **one-to-one** | 0 |
| orders ↔ order_items | `order_items.order_id → orders.order_id` | 100% | one-to-many (max 21) | 775 orders |
| orders ↔ payments | `payments.order_id → orders.order_id` | 100% | one-to-many (max 29) | 1 order |
| orders ↔ reviews | `reviews.order_id → orders.order_id` | 100% | one-to-many (max 3) | 768 orders |
| order_items ↔ products | `order_items.product_id → products.product_id` | 100% | many-to-one | 0 |
| order_items ↔ sellers | `order_items.seller_id → sellers.seller_id` | 100% | many-to-one | 0 |
| products ↔ translation | `products.product_category_name → translation.product_category_name` | 99.96% (13 orphans; 610 missing) | many-to-one | 0 |
| customers/sellers ↔ geolocation | zip prefix | 99.72% / 99.77% | **many-to-many** (no key on geolocation) | — |
| order_items ↔ payments | via `order_id` only | — | **many-to-many** | — |

> **D-01 — Adopt the measured keys as the schema contract.**
> - **Observed:** the keys and cardinalities above, including the composite keys and the absence of any key on `geolocation`.
> - **Treatment:** declare these keys in the pipeline schema. Enforce PK uniqueness and FK integrity as hard validation checks (Section 11). Treat `customer_unique_id` as the customer entity.
> - **Justification:** the keys were tested on all rows, not inferred from names.
> - **Business implication:** any customer-level metric (retention, RFM, CLV) uses `customer_unique_id`. With `customer_id`, every customer would appear to be a one-time buyer.
> - **Validation:** PK uniqueness = 0 duplicates; FK orphan counts equal the NB02 baseline (0 for core tables; 13 untranslated products).

---

## 2. Data preservation and layer architecture

### 2.1 Layers

The agreed project structure (`data/raw/`, `data/processed/`) is kept. The staging layer has its own directory, `data/staging/`, as implemented in Task 4.2. The clean and analytical layers are subdivisions of the processed area:

```
data/
├── raw/                      # Layer 0 — immutable source (9 CSVs, never modified)
├── staging/                  # Layer 1 — typed copy of raw, same rows, no business logic
│   ├── <9 staged datasets>   #   customers, geolocation, orders, order_items, payments,
│   │                         #   reviews, products, sellers, translation (.parquet)
│   └── <3 derived outputs>   #   geolocation_rows, geolocation_points,
│                             #   products_metadata_flags (.parquet)
└── processed/
    ├── clean/                # Layer 2 — standardised + flagged, still at source grain
    └── analytical/           # Layer 3 — integrated datasets at declared grains
```

| Layer | Contents | Row rule | Allowed operations | Not allowed |
|---|---|---|---|---|
| **0 Raw** | The 9 original CSVs | — | Read only | Any write |
| **1 Staging** | One file per source table (the 9 **staged datasets**), plus 3 **derived structural outputs**: `geolocation_rows`, `geolocation_points` (D-10) and `products_metadata_flags` (4.3) | Staged datasets: **row count = raw row count**, exactly. Derived outputs: their own declared grain (distinct full row, distinct point, one row per product), reconstructable from the staged data | Staged datasets: explicit typing (Section 3), column-name normalisation (with mapping), load metadata. Derived outputs: lossless collapse of geolocation duplicates with occurrence counts; product missingness flags; the `dq_outside_brazil` point flag | Filtering, de-duplication *of the staged datasets*, value changes; ZIP centroids; `city_key`; quantity aggregation; other `dq_*` flags |
| **2 Clean** | One file per source table, plus reference tables (zip lookup, category mapping) | Same grain as source. Rows are kept; issues are flagged | Standardised *copies* of columns (incl. `city_key`), the remaining `dq_*` flag columns, derived reference tables (`zip_lookup` built from the staging `geolocation_points`; `category_map`) | Overwriting raw values; imputation; deleting flagged rows |
| **3 Analytical** | Integrated datasets (e.g. order-level, order-item-level, customer-level) | Grain declared per dataset and enforced | Pre-aggregation of one-to-many tables, validated joins, documented filters | Undeclared grain; joins without `validate=` |

**The two kinds of staging output.** The nine **staged datasets** remain typed copies of the raw datasets: same rows, same values, with only the approved type conversions and the two column renames. The three **derived structural outputs** are separate files produced during staging from those typed copies. They never alter the staged datasets: each one either reconstructs its source losslessly (geolocation occurrence counts) or adds information alongside it (product flags keyed by `product_id`).

**Storage format: Parquet** (`pyarrow` is installed). Parquet preserves the declared types: zip codes stay as strings, timestamps stay as datetimes, nullable integers stay integers. Re-reading a CSV would re-trigger the inference problems found in NB03 §4.

> **D-02 — Raw immutability and layered processing.**
> - **Observed:** the analyses so far relied on raw files whose SHA-256 hashes have been verified unchanged after every notebook (NB01–NB03).
> - **Treatment:** pipelines only ever **read** `data/raw/`. Outputs go to `data/staging/` (staging layer) or `data/processed/<layer>/` (clean and analytical layers). A run fails if any raw hash changes.
> - **Justification:** keeps the source of truth for re-processing and audit. Separating layers isolates technical typing and structural derivation (staging) from quality judgements (clean) and from business integration (analytical). The only flags produced in staging are part of its derived outputs: the product missingness flags (factual: a value is missing or not) and `dq_outside_brazil` on geolocation points (a fixed bounding-box rule). All other `dq_*` flags belong to the clean layer.
> - **Business implication:** any figure can be traced back to unmodified source data, and any treatment can be revised without re-acquiring data.
> - **Validation:** compare SHA-256 against the baseline recorded in NB03 §11 at the start and end of every run. Staging row counts must equal raw row counts.

---

## 3. Data types

### 3.1 Dates and timestamps

> **D-03 — Parse all 8 date columns explicitly at staging.**
> - **Observed:** all 8 date/time columns load as text with default inference. 100% of their non-missing values match `YYYY-MM-DD HH:MM:SS`, and **0 values are unparseable** (NB03 §4.1). `order_estimated_delivery_date` always has time 00:00:00. `review_creation_date` has 85 non-midnight values out of 99,224.
> - **Treatment:**
>   - Parse with `pd.to_datetime(..., format="%Y-%m-%d %H:%M:%S", errors="coerce")` into `datetime64`.
>   - Keep values **timezone-naive**. The source states no timezone, and assigning one would be an invented assumption (see Q-14).
>   - Treat `order_estimated_delivery_date` and `review_creation_date` as **date-level** values in comparisons (Section 6).
> - **Justification:** an explicit format is deterministic and rejects malformed dates instead of guessing. `errors="coerce"` turns any future malformed value into a countable missing value instead of a crash.
> - **Business implication:** enables durations (delivery time, approval time) and time-series analysis.
> - **Validation:** count of coerced values must be 0, matching NB03. Non-missing counts per column must equal the raw counts. Min/max per column must equal the NB03 §4.1 table.

Columns: `orders.order_purchase_timestamp`, `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date`, `order_estimated_delivery_date`; `order_items.shipping_limit_date`; `reviews.review_creation_date`, `review_answer_timestamp`.

### 3.2 ZIP-code preservation

> **D-04 — Read zip prefixes as text, everywhere.**
> - **Observed:** the raw files store every zip prefix as **5-character text** (100% of values have length 5). Default inference converts them to `int64`, which drops the leading zero on 270,755 rows: customers 23,995, sellers 1,027, geolocation 245,733. Converting the integers back with 5-digit zero padding reproduces the raw text exactly (NB03 §4.3).
> - **Treatment:** read `customer_zip_code_prefix`, `seller_zip_code_prefix` and `geolocation_zip_code_prefix` with `dtype=str` at staging, in **all three tables**. Never cast them to numbers.
> - **Justification:** a zip prefix is a code, not a quantity. Reading as text preserves the source exactly. Using the same type in all tables keeps joins working; NB02 §3.1 showed both sides are currently `int64`, and mixing types would silently produce zero matches.
> - **Business implication:** correct postal codes in reports and correct joins to any external geographic data.
> - **Validation:** every value matches `^\d{5}$`. Values equal the raw text. Join match rates to geolocation remain 99.72% / 99.77% (NB02 §3.2).

### 3.3 Identifiers, numerics and categoricals

| Column group | Target type | Rationale (evidence) |
|---|---|---|
| All `*_id` columns (32-char hex) | `string` | Confirmed text and 32-char hex on all rows (NB02 §1.4). Never cast. |
| `order_item_id`, `payment_sequential`, `payment_installments`, `review_score` | `Int64` (nullable integer) | Whole-number counters or ordinals |
| `product_name_lenght`, `product_description_lenght`, `product_photos_qty` | `Int64` | Whole numbers stored as `float64` only because of 610 missing values; 0 fractional values (NB03 §4.4) |
| `product_weight_g`, `product_length_cm`, `product_height_cm`, `product_width_cm` | `Int64` | 0 fractional values. Physical measures, but recorded as integers (NB03 §4.4) |
| `price`, `freight_value`, `payment_value` | `float64` (monetary, 2 dp) | Decimal currency amounts. Use **rounding only for comparisons**, never to alter stored values |
| `geolocation_lat`, `geolocation_lng` | `float64` | Continuous coordinates |
| `order_status`, `payment_type`, `*_state`, `review_score` (for grouping) | `category` | Closed vocabularies, all values valid (NB03 §7.1) |
| City names, category names | `string` (+ standardised copies, Section 8) | Free text / open vocabulary |
| Review text | `string` | Free text; keep as provided |

> **D-05 — Correct source column-name misspellings via a mapping, not by editing the source.**
> - **Observed:** the source names `product_name_lenght` and `product_description_lenght` are misspelled (NB01, NB03 §4.4).
> - **Treatment:** rename them to `product_name_length` and `product_description_length` at staging. Keep a versioned `column_name_map` (raw → staged) in the lineage metadata.
> - **Justification:** prevents propagating typos into all downstream code. The mapping keeps traceability to the source.
> - **Business implication:** none on values.
> - **Validation:** the schema check confirms the expected column names. The mapping file is present.

---

## 4. Missing values

### 4.1 Principle

Missing values are classified as either:

- **legitimate (structural):** the event has not happened or the field is optional; or
- **data-quality gaps:** the value should exist given other fields.

Neither type is imputed. Analyses filter to eligible records explicitly instead.

### 4.2 Column-level treatment

| Dataset.column | Missing | Classification (evidence) | Treatment |
|---|---:|---|---|
| `orders.order_approved_at` | 160 | 141 canceled + 5 created = **legitimate** (approval never happened). **14 delivered** = **gap** (NB03 §2.3) | Keep NaT. Flag `dq_delivered_missing_approval` (14) |
| `orders.order_delivered_carrier_date` | 1,783 | Follows status: canceled 550, unavailable 609, invoiced 314, processing 301, created 5, approved 2 = **legitimate**. **2 delivered** = **gap** | Keep NaT. Flag `dq_delivered_missing_carrier_date` (2) |
| `orders.order_delivered_customer_date` | 2,965 | shipped 1,107, canceled 619, unavailable 609, invoiced 314, processing 301, created 5, approved 2 = **legitimate**. **8 delivered** = **gap** | Keep NaT. Flag `dq_delivered_missing_delivery_date` (8) |
| `reviews.review_comment_title` | 87,656 (88.34%) | **Legitimate**: optional field | Keep missing. Derive `has_comment_title` |
| `reviews.review_comment_message` | 58,247 (58.70%) | **Legitimate**: optional. Missingness rises with score (23.5% at score 1 → 64.1% at score 5; ρ = 0.196) (NB03 §2.3) | Keep missing. Derive `has_comment_message`. **Never impute text** |
| `products.product_category_name` (+ name length, description length, photos) | 610 (same rows) | **Gap** with a single cause: the 4 fields are missing together (609 rows; plus 1 row missing all 8 attributes). These products appear in 1,603 order items (NB03 §2.2–2.3) | Keep missing in the raw-value column. Flag with the field-level and row-level flags in 4.3. In a *reporting* category column, label as an explicit unknown group (see D-12). **Do not drop products** |
| `products.product_weight_g` + 3 dimensions | 2 | **Gap**: 1 row missing only physical attributes, 1 row missing all attributes | Keep missing. Flag per 4.3. Exclude from weight/volume analyses only |

> **D-06 — No deletion, no imputation of missing values.**
> - **Observed:** missing values exist in only 3 of 9 tables (NB01, NB03 §2.1). Their pattern is mostly explained by `order_status` or by optional fields.
> - **Treatment:** keep all missing values. Add `dq_*` flags only for the **gaps**: delivered orders missing timestamps (clean layer), and products missing metadata (flags defined in 4.3, produced in staging as `products_metadata_flags`). Analyses define eligibility explicitly; for example, delivery-time metrics use delivered orders that have both purchase and delivery timestamps.
> - **Justification:** imputing a timestamp or a category would fabricate facts (P3). Deleting rows would remove sales (e.g. the 1,603 items of uncategorised products) and bias revenue and volume.
> - **Business implication:** metrics state their eligible population. Revenue totals stay complete.
> - **Validation:** missing counts per column in clean = raw counts (no new NaN, none removed). Order flag counts equal the NB03 baselines (14 / 2 / 8). Product flag counts equal the baselines in 4.3. All are listed in 11.3.

### 4.3 Product metadata flags

**Layer:** produced in **staging** as the derived output `products_metadata_flags.parquet`, with one row per product: `product_id` plus the 11 flags below. The staged `products` dataset itself is not modified.

**Evidence** (NB03 §2.1–2.2): the missing-value pattern in `products` has exactly four combinations.

| Pattern | Products |
|---|---:|
| Complete | 32,340 |
| 4 listing fields missing (category, name length, description length, photos); physical attributes present | 609 |
| 4 physical fields missing (weight, length, height, width); listing fields present | 1 |
| All 8 attributes missing | 1 |

So, within each group, the fields are always missing **together**. No product is missing only some of its listing fields, or only some of its physical fields.

**Field-level flags.** One per source column, true when that individual value is missing. They are needed because analyses select on specific columns.

| Flag | Source column (staged name) | Baseline |
|---|---|---:|
| `dq_missing_category` | `product_category_name` | 610 |
| `dq_missing_name_length` | `product_name_length` | 610 |
| `dq_missing_description_length` | `product_description_length` | 610 |
| `dq_missing_photos_qty` | `product_photos_qty` | 610 |
| `dq_missing_weight_g` | `product_weight_g` | 2 |
| `dq_missing_length_cm` | `product_length_cm` | 2 |
| `dq_missing_height_cm` | `product_height_cm` | 2 |
| `dq_missing_width_cm` | `product_width_cm` | 2 |

**Row-level flags.** One per *group* of fields, true when **all** fields in the group are missing. They describe the product record as a whole.

| Flag | Definition | Baseline |
|---|---|---:|
| `dq_listing_metadata_missing` | all 4 listing fields missing | 610 (609 + 1) |
| `dq_physical_attributes_missing` | all 4 physical fields missing | 2 (1 + 1) |
| `dq_all_attributes_missing` | all 8 attributes missing | 1 |

**Why both levels exist.** In the current data, "any field in the group missing" equals "all fields in the group missing", so the row-level flags carry the same information as their field-level flags. The pipeline checks this equivalence: for each group, the count of products with *any* field missing must equal the count with *all* fields missing. A difference would mean a new partial pattern has appeared, and the regression check (11.3) then fails.

**Downstream reach:**
- Products with `dq_missing_category` appear in **1,603 order items** (NB03 §2.3).
- Order items whose product lacks weight: **18**. This is derived from NB03 §8.3, where 112,632 of 112,650 items had a non-missing weight; freight has no missing values.

These flags never cause a product or order item to be excluded from sales totals.

---

## 5. Duplicates

### 5.1 Types of duplication observed

| Type | Where | Count | Interpretation (evidence) |
|---|---|---:|---|
| Exact duplicate rows | `geolocation` | 261,831 (26.18%) | Repeated identical points. No information beyond frequency (NB03 §3.1) |
| Exact duplicate rows | the other 8 tables | 0 | — |
| Repeated identifier | `reviews.review_id` | 1,603 rows / 789 IDs | Identical content; all orders belong to the same person; 92.52% placed within 60 s (max spread 127 days) (NB02 §5.1) |
| Repeated identifier | `customers.customer_unique_id` | 6,342 rows / 2,997 persons | By design: one `customer_id` per order (NB02 §5.2) |
| Business-level repeat | `order_items` (same order, product, seller, price, freight, deadline) | 10,225 | **Legitimate**: quantity is represented by repeated rows |
| Business-level repeat | `payments` (same order, type, instalments, value) | 614 | Not determinable from the data (e.g. several equal vouchers would look like this) |
| Business-level repeat | `reviews` (same order, score, title, message, creation date; different `review_id`) | 86 | Likely duplicate submissions (NB03 §3.3) |
| Business-level repeat | `orders` (same person, same purchase second) | 582 rows / 290 groups | 59% of groups span several sellers; 41% unexplained (NB03 §3.3) |
| Business-level repeat | `products` (identical attributes except ID) | 695 | Coarse attributes can coincide; not evidence of duplication |
| Business-level repeat | `sellers` (same zip/city/state) | 799 | Plausible co-location |
| Composite key violations | items, payments, reviews | 0 | — |

### 5.2 Treatment per dataset

> **D-07 — Treat duplicates by type, never by blanket `drop_duplicates`.**
>
> | Dataset | Treatment |
> |---|---|
> | `geolocation` | See D-10. The staged `geolocation` dataset keeps all 1,000,163 rows. Separately, staging produces `geolocation_rows`, which collapses exact duplicate **full rows** losslessly (one row per distinct full row, with `occurrence_count`), so the original row count can be reconstructed. Distinct *coordinate points* are a separate staging output, `geolocation_points` (D-10). |
> | `order_items` | **Keep all rows** in clean at grain `(order_id, order_item_id)`. Quantity is derived only in the analytical layer, at the grain defined in D-15. |
> | `payments` | **Keep all rows.** The 614 repeats are not proven duplicates (see Q-10). |
> | `reviews` | **Keep all rows** at grain `(review_id, order_id)`. Flag `dq_review_id_shared` (1,603 rows) and `dq_review_content_duplicate` (86 rows). For review-level counts, count distinct `review_id`. The order-level review rule is a business decision (Q-04). |
> | `customers` | **Keep all rows.** Build a person-level dimension keyed on `customer_unique_id` in the analytical layer. |
> | `orders` | **Keep all rows.** Flag `dq_same_person_same_second` (582). Do not merge orders (Q-09). |
> | `products`, `sellers` | **No action.** Attribute coincidences are not duplicates. |
>
> - **Justification:** the duplicates have different causes (NB03 §3). Removing the `order_items` repeats would delete real units sold. Removing `review_id` repeats would detach reviews from orders. Only exact geolocation repeats carry no information, and even those are collapsed with a count rather than discarded.
> - **Business implication:** sales volumes, revenue and review links stay correct. Review and customer counts use the right distinct keys.
> - **Validation:**
>   - `order_items` row count and price sum are unchanged (112,650; 13,591,643.70).
>   - In the staging output `geolocation_rows`, `sum(occurrence_count)` = 1,000,163.
>   - Distinct `review_id` = 98,410 and distinct `customer_unique_id` = 96,096, unchanged.

### 5.3 Order-item quantity grain

**What the notebooks measured:**
- NB03 §3.3: rows repeating an earlier row on **all six** non-sequence attributes `(order_id, product_id, seller_id, shipping_limit_date, price, freight_value)` = **10,225**. Distinct six-attribute groups are therefore 112,650 − 10,225 = **102,425** (derived).
- NB02 §5.4: (order_id, product_id) pairs appearing on more than one row = **7,088**, with up to 20 rows per pair.

**What was not measured.** No notebook tested whether rows sharing `(order_id, product_id, seller_id, price)` can differ in `freight_value` or `shipping_limit_date`. That grouping is therefore **not verified as safe**: it could merge rows with different freight or deadlines, and keeping one freight or deadline per group would then lose information.

> **D-15 — Derive quantity only at the full-attribute grain.**
> - **Observed:** quantity is represented by repeated rows. Whether freight or deadline can vary within a coarser grouping is unverified (above).
> - **Treatment:**
>   1. The clean layer keeps `order_items` at its source grain `(order_id, order_item_id)`. Nothing is aggregated there.
>   2. In the analytical layer, an **order-line** dataset groups rows by all six attributes: `(order_id, product_id, seller_id, price, freight_value, shipping_limit_date)`. Rows are merged only when they are identical in everything except `order_item_id`, so differing freight values or deadlines can never be combined.
>   3. Each line carries these columns:
>      - `quantity` = number of rows;
>      - `unit_price` = `price` (constant within the group by construction);
>      - `freight_value_per_row` = `freight_value` (constant within the group);
>      - `freight_total` = sum of `freight_value` over the group's rows;
>      - `line_price_total` = `price` × `quantity`;
>      - `shipping_limit_date` (constant within the group);
>      - the `order_item_id`s involved, for traceability.
>   4. The coarser grouping `(order_id, product_id, seller_id, price)` is **not used**. Its effect is reported as a diagnostic (warning W01): the number of such groups that contain more than one distinct freight value or deadline.
> - **Justification:** grouping on every non-sequence attribute is lossless by construction. Summing `freight_value` over rows reproduces the raw freight total whatever freight represents (see Q-17). Deadlines are kept exactly because they are part of the grouping key.
> - **Business implication:** unit counts per product become available without changing revenue or freight totals. The same product bought with different freight or deadline stays on separate lines.
> - **Validation:**
>   - Line count = 102,425 (regression, 11.3).
>   - `sum(quantity)` = 112,650, and every `(order_id, order_item_id)` belongs to exactly one line (blocking V13).
>   - `sum(line_price_total)` = 13,591,643.70 and `sum(freight_total)` = 2,251,909.54 (V09 / V13).
>   - Maximum `quantity` ≤ 20, because the six-attribute grain is at least as fine as the (order, product) pairs measured in NB02.

---

## 6. Chronological inconsistencies

### 6.1 Measured violations (NB03 §5)

| Rule | Kind | Violations | Evaluated (both present) |
|---|---|---:|---:|
| `order_approved_at` < purchase | impossible | 0 | 99,281 |
| `order_delivered_carrier_date` < purchase | impossible | **166** | 97,658 |
| `order_delivered_customer_date` < purchase | impossible | 0 | 96,476 |
| `order_delivered_customer_date` < carrier date | impossible | **23** | 96,475 |
| `order_delivered_carrier_date` < approval | suspicious | **1,359** | 97,644 |
| `order_delivered_customer_date` < approval | suspicious | **61** | 96,462 |
| estimated delivery < purchase (day) | impossible | 0 | 99,441 |
| status = delivered, delivery date missing | status conflict | **8** | — |
| status = canceled, delivery date present | status conflict | **6** | — |
| `shipping_limit_date` > 1 year after last purchase (2020) | out of range | **4** rows (3 orders) | — |
| `review_creation_date` < purchase date | impossible | **64** | — |
| `review_creation_date` < delivery date | plausible | 5,127 (mean score 1.99 vs 4.20) | — |
| delivered after estimated date (day level) | business outcome | 6,535 | — |

Magnitudes: carrier-before-purchase is a median of 0.6 h early, but up to 4,109 h. Delivered-before-carrier is a median of 1.7 days early, up to 16.1 days. Carrier-before-approval is a median of 17.2 h early (NB03 §5.2).

> **D-08 — Flag invalid sequences; never replace dates.**
> - **Observed:** the violations above. In each impossible sequence, at least one of the two timestamps is wrong, and the data cannot show which one.
> - **Treatment:**
>   1. Keep all timestamps exactly as recorded.
>   2. Add one boolean flag per rule in clean `orders` / `reviews` / `order_items`:
>      - `dq_carrier_before_purchase`
>      - `dq_delivered_before_carrier`
>      - `dq_carrier_before_approval`
>      - `dq_delivered_before_approval`
>      - `dq_status_canceled_with_date`
>      - `dq_shipping_limit_out_of_range`
>      - `dq_review_before_purchase`
>      - `dq_review_before_delivery` (informational)
>
>      The "delivered but no delivery date" conflict (8 orders) is **not** given a second flag here. It is already covered by `dq_delivered_missing_delivery_date` from D-06, which belongs with the related approval and carrier-date gap flags.
>   3. Each **derived duration** is computed only when its own inputs are valid. For example, `carrier_to_customer_days` is set to missing when `dq_delivered_before_carrier`, while `purchase_to_delivery_days` stays valid because purchase < delivery holds for all rows.
>   4. Comparisons involving date-only fields (estimated delivery, review creation) are made at **day** level.
> - **Justification:** replacement dates would be fabricated (P3). Per-rule flags let each metric exclude exactly the records that make *that* metric unreliable, rather than dropping whole orders.
> - **Business implication:**
>   - Delivery KPIs exclude at most 166 + 23 orders from the affected intervals.
>   - Customer-facing delivery time (purchase → delivery) is unaffected.
>   - Status conflicts (8 + 6) need a business rule before they count in revenue or delivery reporting (Q-01, Q-02).
> - **Validation:** each flag count equals the NB03 baseline above. No timestamp value differs between staging and clean. Derived durations are never negative where their flag is False.

---

## 7. Numerical anomalies

### 7.1 Classification framework

Every unusual value is placed in one of three classes (NB03 §8.1):

| Class | Definition | Treatment |
|---|---|---|
| **Invalid** | Violates a definitional rule (impossible) | Flag. Set to missing in a *derived* analysis column. Keep the original |
| **Suspicious** | Possible but implausible; caught by a heuristic screen | Flag for investigation. **No automatic exclusion or correction** |
| **Plausible extreme** | Statistically unusual but corroborated by independent evidence | **Keep and do not flag as an error.** Handle with robust statistics in analysis |

### 7.2 Findings and treatments

| Finding (NB03) | Count | Class | Treatment |
|---|---:|---|---|
| `price` ≤ 0 (negative **or zero**) | 0 (minimum price 0.85; NB03 §6, §6.1) | — | Regression check with expected count 0 (11.3). This records the observed fact that no zero or negative prices exist. It is **not** a business rule declaring zero prices invalid (Q-18) |
| `freight_value` < 0 | 0 | — | Regression check, expected 0. Zero freight (383) is treated separately below |
| `payment_value` < 0 | 0 | — | Regression check, expected 0. Zero payments (9) are treated separately below |
| `review_score` outside 1–5 | 0 | — | Regression check, expected 0 |
| `product_weight_g` = 0 | 4 products (all `cama_mesa_banho`, 30×25×30 cm) | Invalid | Flag `dq_weight_zero`. Analysis column `weight_g_valid` = missing. Original kept |
| Coordinates outside Brazil's bounding box | 42 rows / 21 zip prefixes | Invalid | Flag `dq_outside_brazil`, produced in staging on `geolocation_points`. Excluded from the zip-centroid computation (clean layer, D-10) |
| `payment_installments` = 0 (credit card) | 2 | Invalid as stated | See D-16 |
| `shipping_limit_date` in 2020 | 4 rows | Invalid (out of range) | Flag (D-08) |
| Density > 2 g/cm³ (heuristic screen) | 159 products | **Suspicious** | Flag `dq_density_suspicious`. No exclusion, no correction. 58 of 159 (36.5%) have dimensions 16×2×11 cm (Q-06) |
| Dimensions exactly 16×2×11 cm | 588 products | **Suspicious** (possible convention, unverified) | Flag `dq_dims_16x2x11` (Q-06) |
| `payment_value` = 0 | 9 (6 voucher, 3 `not_defined` on canceled orders) | Plausible edge case | Keep. Flag `dq_payment_zero`; exclude from average-ticket statistics |
| `payment_type` = `not_defined` | 3 (all canceled orders) | Plausible edge case | Keep the value |
| `freight_value` = 0 | 383 items (9 sellers; top seller 41.3%) | Plausible (free shipping) | Keep. No error flag |
| `freight_value` > `price` | 4,124 items (median price 13.90) | Plausible (cheap items) | Keep |
| IQR outliers: price / freight / payment | 8,427 / 12,134 / 7,981 | **Plausible extreme** | Keep. Evidence: top prices are in high-value categories; 99.9% of top-1% payments reconcile with item totals; freight–weight Spearman ρ = 0.447 (NB03 §8.3) |
| Delivery time > 60 days | 306 orders (max 209.6 days) | Plausible extreme | Keep. No flag as error |

> **D-09 — Preserve statistical outliers; flag only rule violations and heuristic suspects.**
> - **Observed:** monetary and physical variables are strongly right-skewed (skew 1.7–9.3). Raw-scale IQR flags 7–14% of values, and the log-scale IQR far fewer (NB03 §8.1). The extreme values are corroborated by independent tables.
> - **Treatment:** no outlier removal, capping or winsorisation in the clean layer. Analyses use medians, log scales or robust models as appropriate. Capping, if ever needed, is a model-specific choice documented in that model. Invalid values are flagged, with a missing value in derived analysis columns only. Suspicious values are flagged only.
> - **Justification:** the high tail is real business (expensive products, large orders, heavy freight). Removing it would understate revenue and bias averages.
> - **Business implication:** revenue and freight totals stay complete. Suspicious records are available for review by the data owner.
> - **Validation:**
>   - Sums of `price` (13,591,643.70), `freight_value` (2,251,909.54) and `payment_value` (16,008,872.12) are unchanged in clean.
>   - Flag counts equal the baselines (4, 42, 2, 159, 588, 9).

### 7.3 Credit-card payments with zero instalments

> **D-16 — Keep the recorded zero; flag; never substitute a value.**
> - **Observed (NB03 §6.2):** exactly 2 payment rows have `payment_installments = 0`, both `credit_card`:
>
>   | order_id | payment_sequential | payment_value |
>   |---|---:|---:|
>   | `744bade1fcf9ff3f31d860ace076d422` | 2 | 58.69 |
>   | `1a57108394169c0b47d8f876acc9ba2d` | 2 | 129.94 |
>
>   All other payment rows have instalments ≥ 1. No non-credit-card payment has more than 1 instalment (0 rows).
> - **Treatment:**
>   1. `payment_installments` keeps the value **0** exactly as recorded, in every layer.
>   2. Flag `dq_installments_zero` = True on these 2 rows.
>   3. Instalment-based statistics (e.g. distribution or mean of instalments) exclude flagged rows explicitly, or report them as a separate "recorded as 0" group. **No replacement value** (1, a mode, or anything else) is written anywhere.
>   4. `payment_value` on these rows is **not** in question, so it stays in payment totals.
> - **Justification:** the data does not show what the true instalment count was. Any substitute would be an invented rule (P3). Excluding the rows only where the instalment value matters keeps payment totals complete.
> - **Business implication:** negligible for totals (2 of 103,886 rows). The meaning of 0 must be confirmed by Finance (Q-07).
> - **Validation:** flag count = 2 (regression). Values in the source column are identical between staging and clean (blocking V14). Payment totals are unchanged (V09).

---

## 8. Categorical inconsistencies

### 8.1 Standardisation rules

All standardised values are **added as new columns**. The raw value is always kept, either as `<column>_raw` or as the untouched source column alongside a `<column>_std` copy.

| Column | Observed issue (NB03 §7) | Standardisation rule | Kept for traceability |
|---|---|---|---|
| `*_state` | All values valid (27 UFs; sellers use 23) | Upper-case 2-letter code; validate against the 27 UFs | Raw value |
| `order_status`, `payment_type` | Closed vocabularies; all values documented | No change. Validate against the observed vocabulary | — |
| `customer_city` | 4,119 values; 0 accent variants; 1 value with a digit; 8 with separators | `city_key` = accent-free, lower case, trimmed | `customer_city` |
| `seller_city` | 611 → 609 after normalisation; 24 malformed rows (state suffixes such as `"sao paulo - sp"`, a value `"04482255"`, an e-mail address) | `city_key` as above. Malformed values are **not auto-repaired**: flag `dq_city_malformed` and resolve via a reviewed manual mapping (Q-12). Prefer zip/state for geography | `seller_city` |
| `geolocation_city` | 8,011 → 5,966 after normalisation (2,045 accent variants); 2 URL-encoded values (`%26apos%3b`) | `city_key` as above. Decode the URL-encoded apostrophe in the `_std` column only | `geolocation_city` |
| `product_category_name` | 2 `_2` twins (`casa_conforto_2`, `eletrodomesticos_2`; 95 products); 610 missing; 2 untranslated (13 products) | Raw PT name unchanged. Reporting category from a **project-owned mapping table** (D-12) | `product_category_name` |
| `product_category_name_english` | 4 typos: `costruction_tools_garden`, `costruction_tools_tools`, `fashio_female_clothing`, `home_confort` | Corrected label in the project mapping table only | Original English name |

> **D-11 — Standardise into new columns; never overwrite source values.**
> - **Observed:** inconsistent spellings and free-text errors in city names. Valid controlled vocabularies elsewhere.
> - **Treatment:** add deterministic standardised keys (`*_std` / `city_key`). Flag values that cannot be standardised deterministically. Never overwrite source columns.
> - **Justification:** normalising accents and case is lossless and reversible in meaning. Repairing malformed text needs human judgement.
> - **Business implication:** city-level grouping becomes consistent. Unresolved values stay visible rather than silently misassigned.
> - **Validation:** every source value is still present unchanged. Row count is unchanged. `city_key` distinct counts equal the NB03 baselines (4,119 / 609 / 5,966).

> **D-12 — Category mapping table owned by the project.**
> - **Observed:** 13 products have untranslated categories (`pc_gamer` 3, `portateis_cozinha_e_preparadores_de_alimentos` 10). There are 4 English typos, 2 `_2` twins, and 610 products with no category.
> - **Treatment:** create a versioned reference table `category_map` in the clean layer with these columns: `product_category_name` (PT, source), `category_en_source`, `category_en_clean`, `is_added_by_project`, `note`.
>   - Add the 2 missing translations, marked `is_added_by_project = True`. The English wording needs sign-off (Q-05).
>   - Correct the 4 typos in `category_en_clean`.
>   - Keep the `_2` twins as **separate** categories until the business decides (Q-05).
>   - Show products without a category under an explicit reporting label, with the label wording to be agreed. The raw value stays missing.
> - **Justification:** the raw translation file stays immutable. Every label decision is reviewable in one table.
> - **Business implication:** category reporting covers 100% of sales lines with transparent labels.
> - **Validation:** every non-missing PT category in products maps to exactly one row (73 categories). Mapping keys are unique. Untranslated count after mapping = 0. Missing-category product count is still 610.

---

## 9. Payment reconciliation and double-counting

### 9.1 Evidence

- `order_items` ↔ `payments` is **many-to-many** via `order_id`. 275 orders have more than one item **and** more than one payment (NB02 §5.4).
- A naive join adds **4,954 duplicated rows** and inflates **payment value by 26.86%**, **price by 4.54%** and **freight by 4.69%** (NB02 §6.2; NB03 §9).
- Chaining orders + items + payments + reviews turns 99,441 orders into 119,143 rows (NB02 §6.1).
- Per order, the sum of payments differs from the sum of price + freight in **303 of 98,665 orders**:
  - 264 overpaid, 39 underpaid;
  - 249 differ by more than 1.00;
  - differences range from −51.62 to +182.81.

  The cause cannot be verified from the data (NB03 §6.2).
- Payment rows include zero-value vouchers (6) and `not_defined` rows (3). The `payment_sequential` numbering has gaps in 80 orders.

> **D-13 — Separate item-based and payment-based measures; aggregate before joining.**
> - **Observed:** as above.
> - **Treatment:**
>   1. **Never join `order_items` to `payments` at row level.**
>   2. Build an **order-level payment summary** (one row per `order_id`) in the analytical layer: `payment_total`, `payment_rows` (count of rows, **not** `max(payment_sequential)`), `payment_types` (set), `has_voucher`, `max_installments`.
>   3. Build an **order-level item summary**: `item_count`, `distinct_products`, `distinct_sellers`, `items_price_total`, `items_freight_total`.
>   4. Join both summaries to `orders` with `validate="one_to_one"`.
>   5. Store `payment_minus_items` = `payment_total − (items_price_total + items_freight_total)` and a flag `dq_payment_items_mismatch` (|diff| > 0.01). **Do not adjust either side to force equality.**
>   6. Metrics declare their source: **item-based** (product sales, GMV-like measures by product, category or seller) versus **payment-based** (cash collected, payment-method analysis).
> - **Justification:** aggregating to the shared grain (order) before joining removes the many-to-many multiplication. Forcing equality would fabricate values. The two tables may legitimately measure different things, which the data alone cannot settle.
> - **Business implication:** revenue figures no longer double-count. Reports state which revenue definition they use. The definition itself is a business decision (Q-03).
> - **Validation:**
>   - `sum(payment_total)` = 16,008,872.12.
>   - `sum(items_price_total)` = 13,591,643.70.
>   - Order-level dataset has exactly one row per `order_id` (99,441).
>   - Mismatch flag count = 303.
>   - Every analytical merge declares `validate=`, and a cardinality breach fails the run.

---

## 10. Geolocation

### 10.1 Evidence (NB01; NB02 §2.2, §5.3, §6.3; NB03 §3, §6.5, §7.3)

| Measure | Value | Source |
|---|---:|---|
| Raw rows | 1,000,163 | NB01 |
| **Exact duplicate full rows**: all 5 columns (zip, lat, lng, city, state) identical to an earlier row | 261,831 | NB01; NB03 §3.1 |
| **Distinct full rows** | 738,332 | NB02 §2.2 (all-column key: unique values) |
| **Distinct (zip, lat, lng) points** | 720,154 | NB02 §5.3 |
| Distinct full rows minus distinct points | 18,178 | Derived: 738,332 − 720,154 |
| Zip prefixes | 19,015 | NB02 §5.3 |
| Zip prefixes with more than one state / more than one city spelling | 8 / 8,556 | NB02 §5.3 |
| Raw rows outside Brazil's bounding box (lat −33.75…5.27, lng −73.99…−34.79) | 42 rows in 21 zips | NB03 §6.5 |
| Customers / sellers whose zip has no geolocation row | 278 (157 zips) / 7 | NB02 §3.2 |
| Fan-out of a naive customers ⋈ geolocation join | ~15.1 million rows (×151.7) | NB02 §6.3 |

### 10.2 Two different kinds of "duplicate"

- An **exact duplicate full row** repeats the same coordinates *and* the same city and state text. Collapsing these gives 738,332 distinct full rows.
- A **distinct point** is a unique `(zip, lat, lng)` combination, whatever the city or state text. There are 720,154.
- The 18,178 difference means that some coordinates appear in several distinct full rows that differ only in city or state text. The notebooks show such text variants exist: accent variants of the same city (NB02 §5.3 example; NB03 §7.3), and zips recorded under two states. They did **not** measure whether these variants account for all 18,178 cases.

**Consequence:** collapsing exact duplicate full rows does **not** give one row per coordinate. Any calculation that must count each coordinate once has to work on distinct `(zip, lat, lng)` points, not on distinct full rows.

> **D-10 — Two lossless staging structures, then an unweighted zip-level lookup (clean layer, deferred).**
> - **Observed:** as above.
> - **Treatment:**
>   1. **`geolocation_rows`** (staging, derived output): one row per distinct full row, expected 738,332. Columns: the 5 source columns (zip as text) and `occurrence_count` (number of identical raw rows). Every raw value, including spelling variants, is preserved. The raw table can be reconstructed exactly by repeating each row `occurrence_count` times. `city_key` (D-11) is **not** added in staging; it is a clean-layer standardisation.
>   2. **`geolocation_points`** (staging, derived output): one row per distinct `(zip, lat, lng)`, expected 720,154. Columns:
>      - `raw_row_count` = sum of `occurrence_count` over the full rows that share this point;
>      - `n_full_rows`, `n_city_spellings`, `n_states`;
>      - `dq_outside_brazil`, using the same bounding box as NB03 §6.5.
>   3. **`zip_lookup`** (clean reference; **deferred**, not computed in staging): built from the staging output `geolocation_points`, with one row for **each** of the 19,015 zip prefixes. For zip *z*, let *P(z)* be the set of rows in `geolocation_points` with that zip and `dq_outside_brazil` = False. Then:
>      - `lat_centroid` = median of `geolocation_lat` over *P(z)*;
>      - `lng_centroid` = median of `geolocation_lng` over *P(z)*.
>
>      **Each distinct point counts exactly once**, regardless of `raw_row_count` or `n_full_rows`, so repeated coordinates are **not weighted**. The two medians are computed separately (component-wise). With an even number of points, the median is the mean of the two middle values, so the centroid need not coincide with an observed point. Other columns:
>      - `n_points_used` (size of *P(z)*);
>      - `n_points_excluded_outside_brazil`;
>      - `raw_rows_represented` (sum of `raw_row_count` over *P(z)*);
>      - `n_distinct_states` and `dq_zip_multi_state`;
>      - `dq_zip_no_valid_point`: true when *P(z)* is empty, in which case both centroids are missing.
>
>      No "dominant state" is derived. Customers and sellers keep their own state column.
>   4. Coordinates reach customers and sellers **only through `zip_lookup`** (`validate="many_to_one"`). Records whose zip is absent from `zip_lookup` (278 customers, 7 sellers), or whose zip has no valid point, get missing coordinates and a flag `dq_no_geolocation`.
>   5. **Occurrence information is preserved** in `occurrence_count`, `raw_row_count` and `raw_rows_represented`. A frequency-weighted centroid could therefore be computed later without re-reading the raw file, but it is **not** the default (Q-08).
> - **Justification:**
>   - Using distinct points stops heavily repeated coordinates from pulling the centroid towards themselves just because they were recorded many times.
>   - The median resists stray points.
>   - Excluding the out-of-country points removes coordinates that cannot belong to a Brazilian zip.
>   - A one-row-per-zip lookup turns a many-to-many relationship into many-to-one, so joins cannot fan out.
> - **Business implication:** maps and distance features without row explosion. Centroids are approximate zip-area locations, not addresses.
> - **Validation:**
>   - **Blocking (V12), staging:**
>     - `sum(occurrence_count)` over `geolocation_rows` = staged geolocation row count;
>     - `sum(raw_row_count)` over `geolocation_points` = the same count;
>     - `geolocation_rows` has no exact duplicates;
>     - `geolocation_points` has no duplicate `(zip, lat, lng)`.
>   - **Blocking (V12), clean (when `zip_lookup` is built):**
>     - `zip_lookup` has a unique zip key;
>     - joins keep customer and seller row counts (99,441 / 3,095).
>   - **Regression (11.3):**
>     - staging: `geolocation_rows` = 738,332 rows, `geolocation_points` = 720,154 rows;
>     - clean: `zip_lookup` = 19,015 rows;
>     - raw rows flagged outside Brazil = 42 (staging, `geolocation_points.dq_outside_brazil`);
>     - `dq_zip_multi_state` = 8;
>     - customers / sellers with no zip in geolocation = 278 / 7.
>   - **Consistency:** for every zip, `n_points_used` + `n_points_excluded_outside_brazil` = number of its distinct points. Summed over all zips, this equals 720,154.
>   - **Plausibility:** every non-missing centroid lies inside the bounding box, and between the minimum and maximum of its zip's used points on each axis.
>   - **Warnings (11.4):** the number of distinct points flagged outside Brazil (staging, W02), and the number of zips with `dq_zip_no_valid_point` (clean, W03). Neither was measured in NB01–NB03.

---

## 11. Data validation

### 11.1 Three classes of check

| Class | IDs | What it compares | Result on failure |
|---|---|---|---|
| **Blocking checks** | V01–V14 | **Pipeline invariants.** Outputs are compared with the inputs *of the same run*, and with the declared schema and key contracts. They must hold for any input data. | Run fails. Nothing is written to the next layer. |
| **Regression baseline checks** | Section 11.3 | **Data content against values measured in NB01–NB03**: flag counts, domain-rule counts and structural counts. Valid only while the raw SHA-256 hashes equal the baseline (V01). | Run fails and outputs are not promoted. With identical inputs, a mismatch can only come from changed logic. If the raw data legitimately changes, the baselines are re-measured and re-approved; they are never edited to make a run pass. |
| **Warning checks** | W01–W05 | **Diagnostics with no measured baseline**, or values that inform a pending decision. | Logged with their observed value. The run continues. Reviewed before results are released. |

Regression baselines are **not** warnings. A deviation stops the run, as with a blocking check. They are kept as a separate class because they test *expected data content* (tied to these specific raw files), whereas blocking checks test *pipeline correctness* (true for any input).

### 11.2 Blocking checks (V)

| # | Check | Pass condition | Applies to |
|---|---|---|---|
| V01 | Raw SHA-256 unchanged (start and end of run) | Equal to the NB03 §11 baseline | Raw |
| V02 | Staging row count | Equal to raw row count, per table, counted in the same run | Staging |
| V03 | Schema | Column names and dtypes match the declared schema | All layers |
| V04 | Primary keys | Unique and non-null (8 tables; geolocation has no PK) | Staging, clean |
| V05 | Core foreign keys | 0 orphans for orders→customers, items→orders, payments→orders, reviews→orders, items→products, items→sellers | Clean |
| V06 | Zip format | 100% of values match `^\d{5}$` | All zip columns |
| V07 | Date parsing | 0 coerced values; non-missing count per column = non-missing raw text count | 8 date columns |
| V08 | No new missing values | Missing count per source column in clean = staging | Clean |
| V09 | Monetary totals preserved | Sums of price, freight and payment in clean and analytical outputs = sums computed from staging in the same run | Clean, analytical |
| V10 | Declared grain | Grain key unique in every analytical dataset | Analytical |
| V11 | Merge cardinality | Every merge declares `validate=`; no `MergeError` | Analytical |
| V12 | Lossless geolocation structures | Conditions listed in D-10: occurrence sums = staged geolocation rows and no duplicate full rows or points (staging); unique zip key and join row counts (clean) | Staging (`geolocation_rows`, `geolocation_points`); clean (`zip_lookup`) |
| V13 | Lossless order-line aggregation (D-15) | `sum(quantity)` = order_items rows; each `(order_id, order_item_id)` in exactly one line; line price and freight totals = item totals | Analytical |
| V14 | Source values unchanged | Every source column in clean is identical to staging (standardisation and flags only add columns) | Clean |

**Domain rules are deliberately not blocking checks.** NB03 §6 shows these rules describe the **observed** data: zero prices do not occur, and the minimum price is 0.85. The data cannot show that a zero or negative value would be invalid in business terms (Q-18). The domain rules are therefore regression baselines with their measured expected counts (11.3.2).

### 11.3 Regression baseline checks (R)

#### 11.3.1 Flag counts

| Flag | Baseline | Flag | Baseline |
|---|---:|---|---:|
| `dq_carrier_before_purchase` | 166 | `dq_weight_zero` | 4 |
| `dq_delivered_before_carrier` | 23 | `dq_density_suspicious` | 159 |
| `dq_carrier_before_approval` | 1,359 | `dq_dims_16x2x11` | 588 |
| `dq_delivered_before_approval` | 61 | `dq_outside_brazil` (raw rows represented) | 42 |
| `dq_delivered_missing_delivery_date` | 8 | `dq_installments_zero` | 2 |
| `dq_status_canceled_with_date` | 6 | `dq_payment_zero` | 9 |
| `dq_shipping_limit_out_of_range` | 4 | `dq_payment_items_mismatch` | 303 |
| `dq_review_before_purchase` | 64 | `dq_review_id_shared` | 1,603 |
| `dq_delivered_missing_approval` | 14 | `dq_review_content_duplicate` | 86 |
| `dq_same_person_same_second` | 582 | `dq_city_malformed` (sellers) | 24 |
| `dq_missing_category` | 610 | `dq_missing_weight_g` | 2 |
| `dq_missing_name_length` | 610 | `dq_missing_length_cm` | 2 |
| `dq_missing_description_length` | 610 | `dq_missing_height_cm` | 2 |
| `dq_missing_photos_qty` | 610 | `dq_missing_width_cm` | 2 |
| `dq_listing_metadata_missing` | 610 | `dq_physical_attributes_missing` | 2 |
| `dq_all_attributes_missing` | 1 | `dq_zip_multi_state` (zips) | 8 |
| `dq_delivered_missing_carrier_date` | 2 | | |

Plus the product-group equivalence from 4.3: for each field group, the count of products with *any* field missing equals the count with *all* fields missing.

#### 11.3.2 Domain-rule counts (NB03 §6, §7.1)

These restate measured facts as expected counts. They are not business validity rules.

| Rule | Expected count | Note |
|---|---:|---|
| `price` ≤ 0 | 0 | Minimum observed price 0.85; no zero prices exist |
| `freight_value` < 0 | 0 | |
| `freight_value` = 0 | 383 | Allowed; plausible free shipping |
| `payment_value` < 0 | 0 | |
| `payment_value` = 0 | 9 | Same rows as `dq_payment_zero` |
| `payment_installments` < 1 | 2 | Same rows as `dq_installments_zero` (D-16); both credit card |
| `payment_installments` > 1 with non-credit-card type | 0 | |
| `review_score` ∉ {1,…,5} | 0 | |
| `product_weight_g` ≤ 0 | 4 | All = 0; same rows as `dq_weight_zero` |
| any product dimension ≤ 0 | 0 | |
| `product_photos_qty` < 1 | 0 | |
| state ∉ the 27 UFs (customers, sellers, geolocation) | 0 | |
| `order_status` / `payment_type` outside the observed vocabularies (8 / 5 values) | 0 | |

#### 11.3.3 Structural counts

| Measure | Baseline | Source |
|---|---:|---|
| Raw rows per table | as NB01 (e.g. orders 99,441; order_items 112,650; geolocation 1,000,163) | NB01 |
| Sum of price / freight / payment | 13,591,643.70 / 2,251,909.54 / 16,008,872.12 | NB02 §6.2; NB03 §6.2 |
| Distinct `review_id` / `customer_unique_id` | 98,410 / 96,096 | NB02 §4, §5.2 |
| Products with untranslated category, before / after `category_map` | 13 / 0 | NB02 §3.2 |
| `geolocation_rows` / `geolocation_points` rows (staging) | 738,332 / 720,154 | NB02 §2.2, §5.3 |
| `zip_lookup` rows (clean, deferred) | 19,015 | NB02 §5.3 |
| Customers / sellers with no zip in geolocation | 278 / 7 | NB02 §3.2 |
| Order lines at the six-attribute grain (D-15) | 102,425 | Derived from NB03 §3.3 |
| Order-level analytical dataset rows | 99,441 | NB01 |

### 11.4 Warning checks (W)

| # | Diagnostic | Why it is a warning, not a regression check |
|---|---|---|
| W01 | Number of `(order_id, product_id, seller_id, price)` groups with more than one distinct `freight_value` or `shipping_limit_date` | Not measured in NB01–NB03. Shows what the rejected coarser grain would have merged (D-15) |
| W02 | Number of distinct `(zip, lat, lng)` points flagged outside Brazil | Only the raw-row count (42) was measured |
| W03 | Number of zips with `dq_zip_no_valid_point` (centroid missing) | Not measured |
| W04 | Share of customers and sellers that receive a centroid via `zip_lookup` | Can be lower than the zip match rates (99.72% / 99.77%) if W03 > 0 |
| W05 | Rows outside the candidate analysis window, per analytical dataset | The window is undecided (Q-11) |

Once a warning value has been reviewed and approved, it may be promoted to a regression baseline.

### 11.5 Validation outputs

Each run writes a machine-readable validation report (check ID, class, expected, observed, pass/fail or logged value) to `reports/validation/`. A failed blocking or regression check stops the run before any analytical output is written. Warning values are recorded in every run.

---

## 12. Data lineage

> **D-14 — Every output is traceable to its inputs, code and parameters.**
> - **Treatment:**
>   - **Input registry:** file name, size, SHA-256 and row count of each raw file, recorded at every run (baseline in NB03 §11).
>   - **Run manifest** (`data/processed/_manifest/<run_id>.json`):
>     - run ID and timestamp;
>     - Python and library versions (from `.venv`);
>     - code version;
>     - parameters (e.g. analysis window, thresholds such as density 2 g/cm³ and payment tolerance 0.01);
>     - list of steps with input and output row counts.
>   - **Output registry:** path, layer, grain, row count, SHA-256 and schema of every output file.
>   - **Column lineage:** `column_name_map` (raw → staged names). Every `_std` column documents its source column and rule. Every `dq_*` flag documents its rule ID, linked to this document and the NB03 register.
>   - **Reference tables** (`category_map`, `zip_lookup`, manual city mapping) are versioned files with change notes.
>   - **Code:** transformations live in `src/` as functions, not ad-hoc notebook cells. Notebooks call `src/` and do not duplicate logic.
> - **Justification:** any number in a report can be recomputed and explained. Changes in source or code are detectable through hashes and row counts.
> - **Business implication:** auditability, and confidence when figures are challenged.
> - **Validation:** the manifest exists for every output. Recomputing an output from its manifest reproduces the same output hash.

**Open prerequisite:** the project directory is not yet a git repository, so "code version" cannot be recorded reliably (Q-13).

---

## 13. Processing order

The sequence is designed so that each step depends only on validated outputs of earlier steps.

| Step | Layer | Operation | Gate |
|---:|---|---|---|
| 0 | — | Verify raw SHA-256 against the baseline; start the run manifest | V01 |
| 1 | Staging | Load each CSV **once** with an explicit schema: IDs and zips as `string`; dates parsed with explicit format; integers as `Int64`; categoricals | V02, V03, V06, V07 |
| 2 | Staging | Rename misspelled columns via `column_name_map` (D-05); add load metadata (source file, source hash, run ID) | V03 |
| 3 | Staging | Validate keys and referential integrity against the NB02 baseline | V04, V05 |
| 3a | Staging | Derived structural outputs from the staged data: `geolocation_rows`, then `geolocation_points` with `dq_outside_brazil` (D-10); `products_metadata_flags` (4.3). The staged datasets are not modified | V12 (staging part); regression structural and product-flag counts (11.3.1, 11.3.3); warning W02 |
| 4 | Clean | Categorical standardisation into new `_std` / `city_key` columns (D-11) | V14 |
| 5 | Clean | Build reference tables: `category_map` (D-12); `zip_lookup` from the staging `geolocation_points` (D-10) | V12 (clean part); regression `zip_lookup` count (11.3.3); warning W03 |
| 6 | Clean | Missing-value gap flags for delivered orders (D-06). Product metadata flags already exist from step 3a | V08; regression flag counts (11.3.1) |
| 7 | Clean | Chronology and status flags (D-08) | Regression flag counts (11.3.1) |
| 8 | Clean | Numerical validity and suspicion flags (D-09, D-16) | Regression flag and domain-rule counts (11.3.1–11.3.2) |
| 9 | Clean | Duplicate-related flags (`review_id` shared, review content duplicates, same-person-same-second) (D-07) | Regression flag counts (11.3.1) |
| 10 | Clean | Full clean-layer validation; write clean Parquet files | V03–V09, V12, V14; all regression checks for clean |
| 11 | Analytical | Order-level summaries: payments per order, items per order, reviews per order (the last depends on Q-04); order lines at the six-attribute grain (D-15) | V10, V13; warning W01 |
| 12 | Analytical | Integrated datasets at declared grains (e.g. order level, order-item level, customer level on `customer_unique_id`); all merges with `validate=` | V09, V10, V11; warning W04 |
| 13 | Analytical | Apply the **analysis window** as a documented filter or flag (Q-11). Not applied in clean | Window recorded in manifest |
| 14 | — | Write the validation report; verify raw SHA-256 again; close the manifest | V01; all blocking and regression checks; warnings recorded |

Typing is done first (steps 1–3) because every later check depends on correct types: date comparisons need datetimes, and zip joins need consistent text. The staging derived outputs (step 3a) come straight after typing because they only restructure (geolocation) or describe (product flags) the typed data, without needing any clean-layer standardisation. Flags (steps 6–9) are computed on clean, typed data and before any aggregation, so they stay at source grain and can be traced to individual records. Aggregation and integration come last (steps 11–13), so double-counting cannot enter the clean layer.

---

## 14. Decisions requiring investigation

These issues **cannot be resolved safely from the data alone**. Until each is decided, the pipeline applies the conservative default shown: keep the record, flag it, and exclude it only where it would make a specific metric wrong.

| ID | Issue (evidence) | Why it cannot be automated | Conservative default until decided | Owner |
|---|---|---|---|---|
| Q-01 | **6 canceled orders with a delivery date** (NB03 §5.3) | Could be a return, a late cancellation or a status error. Affects whether revenue counts | Keep; flag; report separately | Business / Operations |
| Q-02 | **8 delivered orders without a delivery date**; 14 without approval; 2 without carrier date | Status or timestamp could be wrong | Keep in sales; exclude from delivery-time metrics | Operations |
| Q-03 | **Revenue definition:** item-based (price, ± freight) vs. payment-based; 303 orders differ (NB03 §6.2) | The two sources measure different things; the cause of differences is not visible | Report both, labelled; never mix | Finance |
| Q-04 | **Reviews per order:** 547 orders have 2–3 reviews; 789 reviews are shared across orders (NB02 §5.1) | Choice (latest, first, mean, worst) changes satisfaction metrics | Keep all at `(review_id, order_id)` grain; no order-level score until decided | Customer Experience |
| Q-05 | **Category labels:** wording for 2 added translations; whether to merge `_2` twins (95 products); label for 610 uncategorised products | Taxonomy is a business decision | Keep twins separate; added translations marked as project-added | Merchandising |
| Q-06 | **16×2×11 cm dimensions** (588 products) and **159 suspicious-density products** | Possible packaging/default convention, **not verified**; heuristic threshold | Flag only; no correction or exclusion | Data owner / Logistics |
| Q-07 | **2 credit-card payments with 0 instalments** (orders `744bade1…`, `1a571083…`; NB03 §6.2) | What 0 means (e.g. paid in full, or a recording error) is not determinable. Any substitute value would be an invented rule | D-16: keep 0; flag; exclude only from instalment statistics; keep in payment totals | Finance |
| Q-08 | **Zip centroid weighting:** unweighted median over distinct points vs. median weighted by `raw_row_count` | Both are defensible. The effect on coordinates was not measured | D-10: unweighted, component-wise median over distinct in-Brazil `(zip, lat, lng)` points. Occurrence counts are kept, so the weighted alternative stays possible | Analytics team |
| Q-09 | **582 same-person, same-second orders** (290 groups; 41% single-seller) | Could be split checkouts or double submissions | Keep as separate orders; flag | Operations |
| Q-10 | **614 identical payment rows within orders** | Could be legitimate equal payments (e.g. vouchers) | Keep all | Finance |
| Q-11 | **Analysis window:** Nov 2016 has 0 orders; 2016-09 (4), 2016-12 (1), 2018-09 (16), 2018-10 (4) are near-empty; 2016-10 (324) is partial (NB03 §5.4) | Window bounds depend on the analytical purpose | Keep all data in clean; apply the window in analytical datasets; candidate window 2017-01 to 2018-08, to be confirmed | Analytics lead |
| Q-12 | **24 malformed seller city values** (state suffixes, a numeric value, an e-mail address) | Manual interpretation needed | Flag; use zip/state for geography | Data owner |
| Q-13 | **Code versioning:** the project is not a git repository | Lineage needs a reliable code version | Record a hash of `src/` files in the manifest | Project owner |
| Q-14 | **Timezone of timestamps** not stated in the source | Assigning a zone would be an assumption | Keep timezone-naive; document as unknown | Data owner |
| Q-15 | **64 reviews created before purchase**; **4 shipping deadlines in 2020** | Wrong timestamp, but which value is unknown | Flag; exclude only from timing analyses | Data owner |
| Q-16 | **775 orders without items** (603 `unavailable`, 164 `canceled`, 5 `created`, 2 `invoiced`, 1 `shipped`) and **1 delivered order without payment** | Whether they belong in funnel or operational analyses is a scope decision | Keep in orders; naturally absent from item-based metrics | Analytics lead |
| Q-17 | **Meaning of `freight_value` on repeated item rows:** per-unit freight, or an allocated share of order freight | Not determinable from the data | D-15: sum `freight_value` over rows, which reproduces the raw total under either meaning. Do not compute "freight per order" or "per unit" semantics beyond that | Logistics / data owner |
| Q-18 | **Business validity of zero or negative monetary values.** None exist (minimum price 0.85; 0 negative freight or payments; NB03 §6) | Absence in the data does not prove such values would be invalid | Regression baselines expect 0 (11.3.2). If any appear, the run stops for review; they are not treated as errors automatically | Finance |

**Measurement gaps.** The following were not measured in NB01–NB03. They will be measured and logged as warnings during implementation, and nothing is assumed about them in the meantime:

- whether `(order_id, product_id, seller_id, price)` groups vary in freight or deadline (W01);
- how many distinct coordinate points lie outside Brazil (W02);
- whether any zip has no valid in-Brazil point (W03);
- what explains the 18,178 coordinates that appear under more than one distinct full row (10.2).

---

## 15. Summary of major decisions and unresolved questions

### 15.1 Major decisions

| ID | Decision | Key evidence |
|---|---|---|
| D-01 | Adopt the measured keys; `customer_unique_id` is the customer entity | customers ↔ orders is 1:1; 2,997 persons with several `customer_id`s |
| D-02 | Raw immutable; staging → clean → analytical layers in Parquet | SHA-256 verified unchanged after NB01–NB03 |
| D-03 | Explicit-format parsing of all 8 date columns; timezone-naive | 0 unparseable values; one uniform format |
| D-04 | Zip prefixes read as 5-character text in all tables | 270,755 rows lose leading zeros under default inference |
| D-05 | Fix misspelled column names via a mapping | `*_lenght` |
| D-06 | No deletion or imputation of missing values; flag only true gaps | Gaps follow status / optional fields; 14 + 2 + 8 delivered-order gaps; 610 products |
| D-07 | Duplicates treated by type; no blanket de-duplication | Item repeats are quantity; `review_id` repeats are shared reviews |
| D-08 | Flag invalid date sequences per rule; never replace dates | 166 / 23 impossible; 1,359 / 61 suspicious; 8 + 6 status conflicts |
| D-09 | Preserve outliers; flag invalid values and heuristic suspects only | Extremes corroborated (99.9% reconcile; ρ = 0.447) |
| D-10 | Geolocation: lossless full-row table (738,332) and distinct-point table (720,154), both staging derived outputs; then a zip lookup (clean, deferred) using the **unweighted** median over distinct in-Brazil points; join only via the lookup | 261,831 exact duplicate full rows; 720,154 distinct points; ×151.7 fan-out; 42 out-of-country rows |
| D-11 | Standardise categoricals into new columns; keep raw values | 2,045 geo city variants; 24 malformed seller cities |
| D-12 | Project-owned category mapping table | 2 untranslated, 4 typos, 2 twins, 610 missing |
| D-13 | Aggregate payments and items to order level before joining; never force reconciliation | +26.86% payment inflation; 303 mismatched orders |
| D-14 | Full lineage: input hashes, run manifest, output registry, versioned reference tables | Reproducibility requirement |
| D-15 | Quantity derived only at the six-attribute grain (order, product, seller, price, freight, deadline); clean keeps the item grain | 10,225 full-attribute repeats → 102,425 lines; the coarser grain is unverified |
| D-16 | Zero-instalment credit-card payments: keep 0, flag, never substitute | 2 rows (NB03 §6.2) |

### 15.2 Unresolved questions needing a decision before or during implementation

| Priority | Questions |
|---|---|
| **Needed before analytical datasets are built** | Q-03 revenue definition · Q-04 review-per-order rule · Q-11 analysis window · Q-16 scope for orders without items |
| **Needed before reporting by category or status** | Q-01 canceled-with-delivery · Q-02 delivered-without-date · Q-05 category labels and `_2` twins |
| **Can proceed with conservative defaults** | Q-06 dimensions/density · Q-07 zero instalments · Q-08 centroid weighting · Q-09 same-second orders · Q-10 identical payments · Q-12 malformed cities · Q-14 timezone · Q-15 impossible review/shipping dates · Q-17 freight meaning · Q-18 zero/negative monetary values |
| **Measurement gaps (warnings W01–W03)** | Coarser quantity grain safety · out-of-country distinct points · zips without a valid point |
| **Project infrastructure** | Q-13 initialise version control |

**Scope note:** this is a strategy document. No CSV was modified, no processed dataset was created, no cleaning code was written, and the notebooks were not changed.
