-- mart_customer_cohorts
-- Grain: one cohort month x months since first purchase (0, 1, 2, ...), for every month from the
-- cohort month to the last purchase month in the data - months without activity are explicit zeros.
--
-- Person level (D-01): a customer is customer_unique_id. cohort_month is Gold dim_customers.cohort_month
-- (month of the person's first purchase over all orders), reused, not re-derived. Activity uses the
-- same population - all orders, any status - so in month 0 every person of the cohort is active.
-- * Active customers: distinct persons of the cohort with >= 1 order purchased in the activity month.
-- * retention_pct = 100 * active_customers / cohort_size.
-- * Order-grain source only (stg_fact_orders, one row per order). items_price_total is Gold's
--   order-level item total, aggregated before any join (D-13); order lines and payments are not
--   joined, so nothing can fan out. Item-based only - never combined with payment totals (Q-03);
--   orders without items (Q-16) count as orders and add nothing to sales.
-- * Full-history, descriptive measures (not prediction features). Q-11: cohort_in_candidate_window /
--   activity_in_candidate_window are flags, nothing is filtered.
with persons as (
    select
        customer_unique_id,
        cohort_month
    from {{ ref('stg_dim_customers') }}
),

cohort_sizes as (
    select
        cohort_month,
        count(*) as cohort_size
    from persons
    group by cohort_month
),

months as (
    -- one row per calendar month; the Q-11 flag is defined by the month, so it is constant within it
    select distinct
        year_month,
        in_candidate_window
    from {{ ref('stg_dim_dates') }}
),

last_purchase_month as (
    select max(purchase_year_month) as last_month
    from {{ ref('stg_fact_orders') }}
),

activity as (
    select
        p.cohort_month,
        o.purchase_year_month                   as activity_month,
        count(distinct o.customer_unique_id)    as active_customers,
        count(*)                                as n_orders,
        sum(o.items_price_total)                as items_price_total
    from {{ ref('stg_fact_orders') }} as o
    inner join persons as p
        on p.customer_unique_id = o.customer_unique_id
    group by p.cohort_month, o.purchase_year_month
),

grid as (
    -- 'YYYY-MM' text compares in calendar order
    select
        c.cohort_month,
        c.cohort_size,
        m.year_month                            as activity_month,
        m.in_candidate_window                   as activity_in_candidate_window
    from cohort_sizes as c
    cross join last_purchase_month as l
    inner join months as m
        on m.year_month between c.cohort_month and l.last_month
)

select
    g.cohort_month,
    (date_part('year', to_date(g.activity_month, 'YYYY-MM')) * 12 + date_part('month', to_date(g.activity_month, 'YYYY-MM')))::integer
      - (date_part('year', to_date(g.cohort_month, 'YYYY-MM')) * 12 + date_part('month', to_date(g.cohort_month, 'YYYY-MM')))::integer
                                                                    as months_since_first_purchase,
    g.activity_month,
    cm.in_candidate_window                                          as cohort_in_candidate_window,
    g.activity_in_candidate_window,
    g.cohort_size,
    coalesce(a.active_customers, 0)                                 as active_customers,
    round(100.0 * coalesce(a.active_customers, 0) / nullif(g.cohort_size, 0), 4)::numeric(7, 4)
                                                                    as retention_pct,
    coalesce(a.n_orders, 0)                                         as n_orders,
    coalesce(a.items_price_total, 0)::numeric(14, 2)                as items_price_total
from grid as g
inner join months as cm
    on cm.year_month = g.cohort_month
left join activity as a
    on a.cohort_month = g.cohort_month
   and a.activity_month = g.activity_month
