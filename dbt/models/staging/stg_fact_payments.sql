-- stg_fact_payments: thin staging view over source warehouse.fact_payments.
-- Grain: one payment transaction; primary key (order_id, payment_sequential) - unchanged from the source.
-- All 12 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    order_id,
    payment_sequential,
    payment_type,
    payment_installments,
    payment_value,
    dq_payment_zero,
    dq_installments_zero,
    customer_unique_id,
    order_status,
    order_purchase_timestamp,
    purchase_date,
    in_candidate_window
from {{ source('warehouse', 'fact_payments') }}
