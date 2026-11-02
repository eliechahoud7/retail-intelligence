-- stg_dim_customers: thin staging view over source warehouse.dim_customers.
-- Grain: one person (customer_unique_id); primary key customer_unique_id - unchanged from the source.
-- All 19 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    customer_unique_id,
    n_customer_ids,
    n_distinct_zips,
    n_distinct_states,
    first_purchase_ts,
    last_purchase_ts,
    cohort_month,
    n_orders,
    n_items,
    items_price_total,
    items_freight_total,
    payment_total,
    n_orders_delivered,
    n_items_delivered,
    items_price_total_delivered,
    items_freight_total_delivered,
    payment_total_delivered,
    first_delivered_purchase_ts,
    last_delivered_purchase_ts
from {{ source('warehouse', 'dim_customers') }}
