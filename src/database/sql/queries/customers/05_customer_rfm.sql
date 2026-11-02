-- name: customer_rfm
-- grain: one person with at least one order purchased on or before the reference date
-- key: customer_unique_id
-- purpose: Recency, Frequency and Monetary (RFM) description of customers AS OF an explicit
--          reference date, with tie-aware 1-5 scores.
-- competencies: bind parameters, CTE chain, conditional aggregation, PERCENT_RANK scoring,
--               explicit handling of persons without eligible history
-- params: reference_date:date; order_scope:choice(delivered|all)=delivered
-- notes: AS-OF CUT-OFF: every order used must have purchase_date <= :reference_date, so no later
--        orders leak in. The reference date is required; there is no CURRENT_DATE default.
--        ELIGIBILITY: in BOTH scopes an order must have items and purchase_date <= :reference_date.
--        In scope 'all', these two conditions are the whole rule (any order status counts).
--        Scope 'delivered' (the default; the approved delivered-order variant, Task 4.4) also
--        requires order_status = 'delivered' and
--        order_delivered_customer_date::date <= :reference_date. An order still in transit on the
--        reference date therefore does not count, and a delivered order with a missing delivery
--        date is excluded.
--        STATUS: order_status is the current status at data extraction, not the historical status
--        as of :reference_date, because Gold has no status history.
--        Recency = :reference_date - last eligible purchase_date (whole days).
--        Frequency = number of eligible orders (orders, not purchase events).
--        Monetary = sum of item-based items_price_total of eligible orders (freight excluded).
--        Scores: 1 + floor(5 * percent_rank), capped at 5, computed over eligible persons only;
--        recency is ranked descending (more recent = higher score), frequency and monetary
--        ascending. Ties always get the same score.
--        LIMITATIONS: scores are relative to this population and reference date. Most persons have
--        exactly one order, so the frequency score mostly separates 1 order from 2+ and is not a
--        uniform quintile. No segment names are assigned because there is no approved business
--        definition.
--        NO ELIGIBLE PURCHASE: persons with orders on or before the reference date but no eligible
--        order (e.g. only orders without items, or in scope 'delivered' only canceled orders or
--        orders not yet delivered on the reference date) remain in the output with
--        has_eligible_purchase = false and NULL R, F, M and scores.
--        RFM here is DESCRIPTIVE, not predictive.
WITH population AS (
    SELECT customer_unique_id,
           count(*) AS n_orders_to_date
    FROM {w}.fact_orders
    WHERE purchase_date <= CAST(:reference_date AS date)
    GROUP BY customer_unique_id
), eligible AS (
    SELECT customer_unique_id,
           max(purchase_date)        AS last_eligible_purchase_date,
           count(*)                  AS frequency,
           sum(items_price_total)    AS monetary
    FROM {w}.fact_orders
    WHERE purchase_date <= CAST(:reference_date AS date)
      AND has_items
      AND (CAST(:order_scope AS text) = 'all'
           OR (order_status = 'delivered'
               AND order_delivered_customer_date::date <= CAST(:reference_date AS date)))
    GROUP BY customer_unique_id
), scored AS (
    SELECT *,
           CAST(:reference_date AS date) - last_eligible_purchase_date              AS recency_days,
           least(5, 1 + floor(5 * percent_rank() OVER (ORDER BY CAST(:reference_date AS date)
                                                       - last_eligible_purchase_date DESC)))::int AS r_score,
           least(5, 1 + floor(5 * percent_rank() OVER (ORDER BY frequency)))::int  AS f_score,
           least(5, 1 + floor(5 * percent_rank() OVER (ORDER BY monetary)))::int   AS m_score
    FROM eligible
)
SELECT p.customer_unique_id,
       CAST(:reference_date AS date)              AS reference_date,
       CAST(:order_scope AS text)                 AS order_scope,
       p.n_orders_to_date,
       s.customer_unique_id IS NOT NULL           AS has_eligible_purchase,
       s.last_eligible_purchase_date,
       s.recency_days,
       s.frequency,
       s.monetary,
       s.r_score,
       s.f_score,
       s.m_score,
       s.r_score::text || s.f_score::text || s.m_score::text AS rfm_cell
FROM population p
LEFT JOIN scored s ON s.customer_unique_id = p.customer_unique_id
ORDER BY p.customer_unique_id;
