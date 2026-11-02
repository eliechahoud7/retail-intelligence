-- mart_review_scores_by_delivery
-- Grain: one delivery_class x review_score, for every combination with at least one review link.
--
-- Review grain (Q-04 / D-07): reviews are counted at their own grain - review-order links and
-- distinct review_id. No order-level review score is derived; each link keeps its own score.
-- * delivery_class is the class of the reviewed order, as in the approved delivery query
--   (src/database/sql/queries/delivery/04_estimated_vs_actual_delivery.sql): for D-06 eligible
--   orders (status 'delivered' with a delivery date) by Gold delivery_vs_estimate_days (day level):
--   < 0 'early', = 0 'on_estimated_day', > 0 'late'. Reviews of all other orders are kept in
--   'not_delivery_eligible' instead of being dropped.
-- * Reviews are joined many-to-one to their order (order_id is the orders primary key): no fan-out.
-- * n_reviews counts distinct review_id. A review shared by several orders (D-07) can fall into
--   several delivery classes, so n_reviews is NOT additive across classes; it is additive across the
--   scores of one class (a review has one score). review_share_pct is the score's share of the class's
--   distinct reviews.
with review_links as (
    select
        r.review_id,
        r.review_score,
        case
            when not (o.order_status = 'delivered' and o.order_delivered_customer_date is not null)
                then 'not_delivery_eligible'
            when o.delivery_vs_estimate_days < 0 then 'early'
            when o.delivery_vs_estimate_days = 0 then 'on_estimated_day'
            else 'late'
        end as delivery_class
    from {{ ref('stg_fact_reviews') }} as r
    left join {{ ref('stg_fact_orders') }} as o
        on o.order_id = r.order_id
),

by_class_and_score as (
    select
        delivery_class,
        review_score,
        count(*)                    as n_review_links,
        count(distinct review_id)   as n_reviews
    from review_links
    group by delivery_class, review_score
),

class_totals as (
    select
        delivery_class,
        count(distinct review_id)   as class_n_reviews
    from review_links
    group by delivery_class
)

select
    s.delivery_class,
    s.review_score,
    s.n_review_links,
    s.n_reviews,
    c.class_n_reviews,
    round(100.0 * s.n_reviews / nullif(c.class_n_reviews, 0), 4)::numeric(7, 4) as review_share_pct
from by_class_and_score as s
inner join class_totals as c
    on c.delivery_class = s.delivery_class
