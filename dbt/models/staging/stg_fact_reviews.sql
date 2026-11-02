-- stg_fact_reviews: thin staging view over source warehouse.fact_reviews.
-- Grain: one review-order link; primary key (review_id, order_id) - unchanged from the source.
-- All 19 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    review_id,
    order_id,
    review_score,
    review_comment_title,
    review_comment_message,
    review_creation_date,
    review_answer_timestamp,
    has_comment_title,
    has_comment_message,
    dq_review_id_shared,
    dq_review_content_duplicate,
    dq_review_before_purchase,
    dq_review_before_delivery,
    customer_unique_id,
    order_status,
    order_purchase_timestamp,
    purchase_date,
    in_candidate_window,
    order_delivered_customer_date
from {{ source('warehouse', 'fact_reviews') }}
