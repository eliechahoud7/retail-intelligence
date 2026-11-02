{#
    Shared documentation for the dbt sources (Task 5.4, Milestone 2).
    Source of truth: reports/database/warehouse_schema_design.md (W-xx, Q1-Q5),
    reports/data_preparation_strategy.md (D-xx, Q-xx), src/analytical/schema.py (TABLES, DECISIONS)
    and the view SQL in src/database/sql/views/. Nothing here defines new business rules.
#}

{% docs __overview__ %}
# Retail Intelligence - dbt project

dbt documents and tests the existing PostgreSQL warehouse and builds its own models only in
`dbt_staging`, `dbt_intermediate` and `dbt_marts`. The `warehouse` and `analytics` schemas are
owned by the Python pipeline (Gold build, loader, views) and are **read-only sources** here.

- **Source `warehouse`**: the eight Gold star-schema tables, a faithful relational copy of the
  validated Gold Parquet outputs (W-02), plus the `load_run` lineage table (Q1).
- **Source `analytics`**: the seven analytical views defined in `src/database/sql/views/`.

Interpretation rules that apply everywhere (full text in the source and table descriptions):

- **Q-03** - item-based (`items_*`) and payment-based (`payment_*`) revenue are never combined.
- **Q-04** - reviews stay at review-and-order grain; there is no order-level review score.
- **Sellers** - payments, reviews and delivery times are never attributed to sellers (or products).
- **Q-11** - the candidate window 2017-01 to 2018-08 is a flag (`in_candidate_window`), not a filter.
- **D-06 / D-08** - delivery metrics use delivered orders with a delivery date; invalid durations are NULL.
- **Freshness** - none configured: the data is a closed historical extract and reloads have no schedule.
{% enddocs %}


{% docs source_warehouse %}
The dimensional model loaded from the Gold Parquet outputs (`data/processed/analytical/`):
four fact tables (`fact_order_lines`, `fact_orders`, `fact_payments`, `fact_reviews`) and four
dimension tables (`dim_customers`, `dim_products`, `dim_sellers`, `dim_dates`), plus the
lineage table `load_run`.

The warehouse **mirrors Gold exactly** - same tables, grain, column names and values (W-02). It
adds declared types (`numeric(12,2)` for money, `timestamp(0)` without time zone for events,
`date` for the day keys), primary keys, the 13 Gold foreign keys, grain guards and four CHECK
invariants. All transformation logic (D-13, D-15, durations, flags) lives in `src/analytical`;
do not re-implement it in dbt.

The schema is replaced as a whole on every load: one transaction truncates and reloads all eight
tables (W-13), so there is no incremental history.

**Star schema.** Every foreign key references a primary key, so every fact-to-dimension join is
many-to-one and cannot fan out. Facts are never joined to each other except many-to-one into
`fact_orders` (by `order_id`). Joining two one-to-many facts on `order_id` (e.g. order lines to
payments) multiplies rows - aggregate each fact to the order first (D-13).

**Denormalised order context** (W-11): every fact carries `customer_unique_id`, `order_status`,
`order_purchase_timestamp`, `purchase_date` and `in_candidate_window` copied from `fact_orders`;
their consistency is guaranteed by the Gold build and checked after each load.

**Timezone** (Q-14): the source timezone is unknown, so event timestamps are timezone-naive.
{% enddocs %}


{% docs source_analytics %}
Seven analytical views built on the `warehouse` tables (`src/database/sql/views/NN_<view>.sql`,
created by `python -m src.database create-views`, verified and reconciled with the tables by
`verify-views`). Each view aggregates every fact to the view grain **before** joining, so no fact
row is multiplied. Views are always current with the warehouse tables (no materialisation).

All view columns are reported as nullable by the catalog (PostgreSQL does not propagate NOT NULL
through views); the descriptions state which columns can actually be NULL.
{% enddocs %}


{% docs rule_revenue_separation %}
**Item-based and payment-based revenue are never combined (Q-03).** `items_*` / `line_price_total`
/ `freight_total` come from order lines (price and freight); `payment_*` comes from payment
transactions. They measure different things and differ for 303 orders
(`fact_orders.dq_payment_items_mismatch`); neither is "the" revenue. Report them as separate,
labelled measures and never add, subtract, coalesce or substitute one for the other.
{% enddocs %}


{% docs rule_reviews_grain %}
**Reviews stay at review-and-order grain; there is no order-level review score (Q-04).** 547
orders have 2-3 reviews and `review_id` is not unique (a review can be linked to several orders,
D-07). Count reviews with distinct `review_id`; `fact_orders.review_count` is the only order-level
review attribute. Do not derive a per-order score (latest, first, mean or worst) until the business
decides.
{% enddocs %}


{% docs rule_no_seller_attribution %}
**Payments, reviews and delivery times are never attributed to sellers.** They are recorded per
order and 1,278 orders contain several sellers, so any attribution would need an allocation rule
that has not been defined. Seller measures are item-based only (`fact_order_lines`). The same
applies to products: payments are not attributable to products.
{% enddocs %}


{% docs rule_candidate_window %}
**Candidate analysis window (Q-11): purchase months 2017-01 to 2018-08**, applied as the flag
`in_candidate_window` only - no row is removed. Months outside it are near-empty or partial
(2016-09: 4 orders, 2016-10: 324 partial, 2016-11: 0, 2016-12: 1, 2018-09: 16, 2018-10: 4). Filter
on the flag for trend and time-series analysis; totals over all rows stay complete. The window is
a candidate, still to be confirmed by the analytics lead.
{% enddocs %}


{% docs rule_delivery_eligibility %}
**Delivery-metric population (strategy D-06, as implemented by the delivery views):** orders with
`order_status = 'delivered'` **and** a non-null `order_delivered_customer_date`. Each duration is
averaged only where it is defined: Gold sets a duration to NULL where an input is missing or its
D-08 chronology flag marks the sequence invalid. Lateness (`is_late`) compares delivery with the
estimate at day level. Status conflicts - 8 delivered orders without a delivery date (Q-02) and 6
canceled orders with one (Q-01) - are counted, not included in the metrics.
{% enddocs %}


{% docs rule_delivered_variant %}
`*_delivered` measures are the same measure restricted to orders with `order_status = 'delivered'`
(Task 4.4 eligibility variant). The unsuffixed measure covers all orders.
{% enddocs %}


{% docs rule_full_history %}
**Full-history measures - descriptive only.** They span the whole order history and are **not**
valid prediction features (target leakage). Any model must derive time-bounded features from the
fact tables with an explicit cut-off date.
{% enddocs %}


{% docs rule_no_freshness %}
**No source freshness is configured.** The order data is a closed historical extract (purchases
2016-09-04 to 2018-10-17), so a freshness check on event timestamps would always fail and say
nothing about the pipeline. The only load-time timestamp, `warehouse.load_run.finished_at`,
records a manual full reload (W-13) that has no agreed schedule, so any `warn_after` / `error_after`
threshold would be invented. Lineage is checked through `load_run` and `validate-load` instead.
{% enddocs %}


{% docs col_customer_unique_id %}
The person (customer entity, D-01). One person can have several `customer_id`s (2,997 persons have
several orders). FK to `dim_customers.customer_unique_id`.
{% enddocs %}

{% docs col_order_id %}
Order identifier (32-character hex). FK to `fact_orders.order_id`.
{% enddocs %}

{% docs col_order_status %}
Order status as recorded in the source (8 observed values, e.g. `delivered`, `shipped`, `canceled`,
`unavailable`). The vocabulary is observed, not a business rule (W-09); denormalised from
`fact_orders` on the other facts (W-11).
{% enddocs %}

{% docs col_order_purchase_timestamp %}
Purchase timestamp of the order (timezone unknown, Q-14). Denormalised from `fact_orders` (W-11).
{% enddocs %}

{% docs col_purchase_date %}
Calendar day of `order_purchase_timestamp` (`date`). FK to `dim_dates.date`; denormalised from
`fact_orders` (W-11).
{% enddocs %}

{% docs col_in_candidate_window %}
True when the purchase month is inside the Q-11 candidate window (2017-01 to 2018-08). A flag only;
no rows are excluded.
{% enddocs %}

{% docs col_product_id %}
Product identifier. FK to `dim_products.product_id`.
{% enddocs %}

{% docs col_seller_id %}
Seller identifier. FK to `dim_sellers.seller_id`.
{% enddocs %}

{% docs col_category_reporting %}
Reporting category from the project-owned category mapping (D-12). Never NULL: products without a
category get the provisional label `uncategorized`. Labels are provisional (Q-05); the `_2` twins
are kept as separate categories.
{% enddocs %}
