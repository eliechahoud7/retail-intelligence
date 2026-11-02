-- mart_customer_cohorts against the staging models (one row per difference): every order and every
-- item-sales cent lands in exactly one cell (an order before its cohort month would fall outside the
-- grid and show here), and the cohort sizes add up to all persons.
with expected as (
    select 'orders' as measure, count(*)::numeric as value from {{ ref('stg_fact_orders') }}
    union all
    select 'items_price_total', coalesce(sum(items_price_total), 0) from {{ ref('stg_fact_orders') }}
    union all
    select 'persons', count(*) from {{ ref('stg_dim_customers') }}
),

actual as (
    select 'orders' as measure, sum(n_orders)::numeric as value from {{ ref('mart_customer_cohorts') }}
    union all
    select 'items_price_total', sum(items_price_total) from {{ ref('mart_customer_cohorts') }}
    union all
    select 'persons', sum(cohort_size) from {{ ref('mart_customer_cohorts') }} where months_since_first_purchase = 0
)

select e.measure, e.value as expected, a.value as actual
from expected as e
left join actual as a on a.measure = e.measure
where a.value is distinct from e.value
