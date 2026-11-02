-- Reconciliation of both payment marts with stg_fact_payments (fails with one row per difference).
-- Transaction counts and payment value per month x type, per type x instalment count and in total
-- must be identical - no transaction missing, duplicated or moved to another group, no amount
-- changed. Payment value is compared exactly (sums of numeric(12,2) values; no rounding involved).
-- An independent recalculation from warehouse.fact_payments is done outside dbt (Milestone 4 report).
with stg as (
    select
        p.payment_type,
        p.payment_installments,
        p.payment_value,
        d.year_month as purchase_year_month
    from {{ ref('stg_fact_payments') }} as p
    left join {{ ref('stg_dim_dates') }} as d
        on d.date = p.purchase_date
),

expected as (
    select 'month_x_type' as check_level, purchase_year_month as key_1, payment_type as key_2,
           count(*) as n_transactions, sum(payment_value) as payment_value_total
    from stg group by purchase_year_month, payment_type
    union all
    select 'type_x_installments', payment_type, payment_installments::text,
           count(*), sum(payment_value)
    from stg group by payment_type, payment_installments
    union all
    select 'total_monthly_mart', 'all', 'all', count(*), sum(payment_value) from stg
    union all
    select 'total_installments_mart', 'all', 'all', count(*), sum(payment_value) from stg
),

actual as (
    select 'month_x_type' as check_level, purchase_year_month as key_1, payment_type as key_2,
           n_transactions, payment_value_total
    from {{ ref('mart_payment_methods_monthly') }}
    union all
    select 'type_x_installments', payment_type, payment_installments::text,
           n_transactions, payment_value_total
    from {{ ref('mart_payment_installments') }}
    union all
    select 'total_monthly_mart', 'all', 'all', sum(n_transactions), sum(payment_value_total)
    from {{ ref('mart_payment_methods_monthly') }}
    union all
    select 'total_installments_mart', 'all', 'all', sum(n_transactions), sum(payment_value_total)
    from {{ ref('mart_payment_installments') }}
)

(select 'missing_or_different_in_mart' as problem, check_level, key_1, key_2, n_transactions, payment_value_total
 from expected
 except
 select 'missing_or_different_in_mart', check_level, key_1, key_2, n_transactions, payment_value_total
 from actual)
union all
(select 'unexpected_in_mart', check_level, key_1, key_2, n_transactions, payment_value_total
 from actual
 except
 select 'unexpected_in_mart', check_level, key_1, key_2, n_transactions, payment_value_total
 from expected)
