-- mart_payment_methods_monthly
-- Grain: one purchase month x payment type, for every combination with at least one payment
-- transaction (months or types without payments have no row; there are no zero rows).
--
-- Payment-based only: every amount comes from stg_fact_payments.payment_value. No order lines and
-- no item-based revenue are used or combined (Q-03); payments are not attributed to sellers.
-- * n_transactions counts payment rows; n_orders counts distinct orders. An order can have several
--   payment rows (and several types), so n_orders is NOT additive across payment types or months.
-- * Every transaction is kept, including zero-value payments (dq_payment_zero) and zero-instalment
--   credit-card payments (dq_installments_zero, D-16); they are counted in n_zero_value_transactions
--   / n_zero_installment_transactions.
-- * avg_installments excludes the zero-instalment rows (D-16: instalment statistics exclude them; no
--   substitute value). NULL when a group has no row with a recorded instalment count >= 1.
-- * The purchase month is dim_dates.year_month of the payment's purchase_date (many-to-one on the
--   date key, so no row can be multiplied). in_candidate_window is the Q-11 flag of the month, carried
--   from the payment rows as a flag - nothing is filtered.
with payments as (
    select
        p.order_id,
        p.payment_type,
        p.payment_installments,
        p.payment_value,
        p.dq_payment_zero,
        p.dq_installments_zero,
        p.in_candidate_window,
        d.year_month as purchase_year_month
    from {{ ref('stg_fact_payments') }} as p
    left join {{ ref('stg_dim_dates') }} as d
        on d.date = p.purchase_date
),

by_month_and_type as (
    select
        purchase_year_month,
        payment_type,
        in_candidate_window,
        count(*)                                                    as n_transactions,
        count(distinct order_id)                                    as n_orders,
        sum(payment_value)                                          as payment_value_total,
        avg(payment_installments) filter (where not dq_installments_zero)
                                                                    as avg_installments,
        count(*) filter (where dq_installments_zero)                as n_zero_installment_transactions,
        count(*) filter (where dq_payment_zero)                     as n_zero_value_transactions
    from payments
    group by purchase_year_month, payment_type, in_candidate_window
),

with_month_total as (
    select
        purchase_year_month,
        payment_type,
        in_candidate_window,
        n_transactions,
        n_orders,
        payment_value_total,
        sum(payment_value_total) over (partition by purchase_year_month) as month_payment_value_total,
        avg_installments,
        n_zero_installment_transactions,
        n_zero_value_transactions
    from by_month_and_type
)

select
    purchase_year_month,
    payment_type,
    in_candidate_window,
    n_transactions,
    n_orders,
    payment_value_total::numeric(14, 2)                                         as payment_value_total,
    month_payment_value_total::numeric(14, 2)                                   as month_payment_value_total,
    -- share of the month's payment value, in percent; NULL (not a division error) for a zero month
    round(100 * payment_value_total / nullif(month_payment_value_total, 0), 4)::numeric(7, 4)
                                                                                as payment_value_share_pct,
    round(avg_installments, 2)::numeric(6, 2)                                   as avg_installments,
    n_zero_installment_transactions,
    n_zero_value_transactions
from with_month_total
