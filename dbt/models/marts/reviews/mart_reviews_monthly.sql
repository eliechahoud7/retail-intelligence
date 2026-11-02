-- mart_reviews_monthly
-- Grain: one review creation month (dim_dates.year_month of review_creation_date), for every month
-- with at least one review.
--
-- Review grain (Q-04 / D-07): review-level measures use distinct review_id. A review shared by
-- several orders has the same score, creation and answer timestamps and comment flags on every link
-- (true for all 98,410 review_ids), so it is counted once. No order-level review score is derived.
-- * Score distribution: distinct reviews per score 1-5.
-- * comment_rate_pct: share of reviews with a comment message (has_comment_message).
-- * Answer time: review_answer_timestamp - review_creation_date in hours, per review. It uses only
--   the two review timestamps (no review is answered before it is created), so the Q-15 flag
--   dq_review_before_purchase (review vs purchase) does not affect it and no review is excluded.
-- * No candidate-window flag: Q-11 is defined on the purchase month, not the review month.
with reviews as (
    select distinct
        review_id,
        review_score,
        review_creation_date,
        review_answer_timestamp,
        has_comment_message
    from {{ ref('stg_fact_reviews') }}
),

links_by_month as (
    select
        d.year_month                as review_creation_month,
        count(*)                    as n_review_links
    from {{ ref('stg_fact_reviews') }} as r
    left join {{ ref('stg_dim_dates') }} as d
        on d.date = r.review_creation_date::date
    group by d.year_month
),

reviews_by_month as (
    select
        d.year_month                                                as review_creation_month,
        count(*)                                                    as n_reviews,
        count(*) filter (where v.review_score = 1)                  as n_reviews_score_1,
        count(*) filter (where v.review_score = 2)                  as n_reviews_score_2,
        count(*) filter (where v.review_score = 3)                  as n_reviews_score_3,
        count(*) filter (where v.review_score = 4)                  as n_reviews_score_4,
        count(*) filter (where v.review_score = 5)                  as n_reviews_score_5,
        count(*) filter (where v.has_comment_message)               as n_reviews_with_comment,
        avg(extract(epoch from v.review_answer_timestamp - v.review_creation_date) / 3600)
                                                                    as avg_answer_hours,
        percentile_cont(0.5) within group (
            order by extract(epoch from v.review_answer_timestamp - v.review_creation_date) / 3600)
                                                                    as median_answer_hours
    from reviews as v
    left join {{ ref('stg_dim_dates') }} as d
        on d.date = v.review_creation_date::date
    group by d.year_month
)

select
    m.review_creation_month,
    l.n_review_links,
    m.n_reviews,
    m.n_reviews_score_1,
    m.n_reviews_score_2,
    m.n_reviews_score_3,
    m.n_reviews_score_4,
    m.n_reviews_score_5,
    m.n_reviews_with_comment,
    round(100.0 * m.n_reviews_with_comment / nullif(m.n_reviews, 0), 4)::numeric(7, 4) as comment_rate_pct,
    round(m.avg_answer_hours::numeric, 2)::numeric(10, 2)                              as avg_answer_hours,
    round(m.median_answer_hours::numeric, 2)::numeric(10, 2)                           as median_answer_hours
from reviews_by_month as m
inner join links_by_month as l
    on l.review_creation_month = m.review_creation_month
