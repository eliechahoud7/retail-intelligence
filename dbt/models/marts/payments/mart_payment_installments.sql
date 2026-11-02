-- mart_payment_installments
-- Grain: one payment type x recorded instalment count (payment_installments), for every combination
-- with at least one payment transaction.
--
-- Payment-based only (stg_fact_payments); no order lines, no item-based revenue (Q-03).
-- * Instalment counts are kept exactly as recorded: the 2 credit-card payments with 0 instalments form
--   their own row, marked is_zero_installments (D-16 / Q-07: keep 0, flag, never substitute).
-- * Zero-value payments (dq_payment_zero: vouchers, not_defined) stay in every count and total;
--   has_zero_value_transactions / n_zero_value_transactions mark them. avg_payment_value excludes them
--   (strategy: zero values are excluded from average-ticket statistics) and is NULL when a row has only
--   zero-value payments.
-- * n_orders counts distinct orders in the row. An order can pay with several types or instalment
--   counts, so n_orders is NOT additive across rows.
with payments as (
    select
        order_id,
        payment_type,
        payment_installments,
        payment_value,
        dq_payment_zero,
        dq_installments_zero
    from {{ ref('stg_fact_payments') }}
),

by_type_and_installments as (
    select
        payment_type,
        payment_installments,
        -- the Gold flag, not a re-derivation; constant within the grain (it depends only on the
        -- instalment count), which the unique grain test guarantees
        dq_installments_zero                                        as is_zero_installments,
        count(*)                                                    as n_transactions,
        count(distinct order_id)                                    as n_orders,
        sum(payment_value)                                          as payment_value_total,
        avg(payment_value) filter (where not dq_payment_zero)       as avg_payment_value,
        count(*) filter (where dq_payment_zero)                     as n_zero_value_transactions
    from payments
    group by payment_type, payment_installments, dq_installments_zero
)

select
    payment_type,
    payment_installments,
    is_zero_installments,
    n_transactions,
    n_orders,
    payment_value_total::numeric(14, 2)                 as payment_value_total,
    round(avg_payment_value, 2)::numeric(12, 2)         as avg_payment_value,
    n_zero_value_transactions > 0                       as has_zero_value_transactions,
    n_zero_value_transactions
from by_type_and_installments
