-- mart_review_scores_by_delivery against stg_fact_reviews (one row per problem):
-- * review links per score summed over all classes = stg_fact_reviews links per score (nothing lost
--   or duplicated by the join to orders), and distinct reviews per score likewise cannot exceed
--   the per-class sum;
-- * the score shares of each class add up to 100 within rounding (4 dp each, <= 5 scores).
with stg as (
    select review_score, count(*) as n_review_links, count(distinct review_id) as n_reviews
    from {{ ref('stg_fact_reviews') }}
    group by review_score
),

mart as (
    select review_score, sum(n_review_links) as n_review_links, sum(n_reviews) as n_reviews
    from {{ ref('mart_review_scores_by_delivery') }}
    group by review_score
)

select 'links_per_score' as problem, coalesce(s.review_score, m.review_score)::text as key,
       s.n_review_links as expected, m.n_review_links as actual
from stg as s
full outer join mart as m on m.review_score = s.review_score
where s.n_review_links is distinct from m.n_review_links
   or m.n_reviews < s.n_reviews
union all
select 'shares_per_class', delivery_class, 100, sum(review_share_pct)
from {{ ref('mart_review_scores_by_delivery') }}
group by delivery_class
having abs(sum(review_share_pct) - 100) > 0.001
