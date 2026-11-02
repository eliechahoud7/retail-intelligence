-- mart_reviews_monthly against stg_fact_reviews (one row per differing month): review links and
-- distinct reviews per creation month are identical - no month, link or review missing or counted
-- twice (a review with differing attributes on its links would be counted twice in the mart).
with stg as (
    select to_char(review_creation_date, 'YYYY-MM') as review_creation_month,
           count(*)                  as n_review_links,
           count(distinct review_id) as n_reviews
    from {{ ref('stg_fact_reviews') }}
    group by 1
)

select coalesce(s.review_creation_month, m.review_creation_month) as review_creation_month,
       s.n_review_links as expected_links, m.n_review_links as actual_links,
       s.n_reviews      as expected_reviews, m.n_reviews   as actual_reviews
from stg as s
full outer join {{ ref('mart_reviews_monthly') }} as m
    on m.review_creation_month = s.review_creation_month
where s.n_review_links is distinct from m.n_review_links
   or s.n_reviews      is distinct from m.n_reviews
