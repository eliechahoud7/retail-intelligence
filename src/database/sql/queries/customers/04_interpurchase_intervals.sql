-- name: interpurchase_intervals
-- grain: one purchase event after a person's first (persons with >= 2 distinct purchase timestamps)
-- key: customer_unique_id, purchase_seq
-- purpose: Time between consecutive purchases per person, with the person's purchase sequence and
--          the person-level and overall average and median intervals.
-- competencies: CTE chain, LAG / LEAD over PARTITION BY, ROW_NUMBER, AVG() OVER (PARTITION BY),
--               percentile_cont (median) as an ordered-set aggregate, CROSS JOIN of a summary CTE
-- notes: A purchase event = one distinct order_purchase_timestamp of a person. Several orders
--        placed in the same second (split checkouts, Q-09: dq_same_person_same_second) form ONE
--        event, so no 0-second "interval" is produced; n_orders_in_event shows how many orders it
--        contains. Only consecutive events with both timestamps present are compared (purchase
--        timestamps are never NULL). Intervals are exact elapsed days (fractional). All order
--        statuses count as purchases (same population as customer_purchase_summary).
--        FULL-HISTORY, DESCRIPTIVE ONLY.
WITH events AS (
    SELECT customer_unique_id,
           order_purchase_timestamp,
           count(*)          AS n_orders_in_event,
           min(order_id)     AS first_order_id
    FROM {w}.fact_orders
    GROUP BY customer_unique_id, order_purchase_timestamp
), sequenced AS (
    SELECT *,
           row_number() OVER p                                  AS purchase_seq,
           count(*)     OVER (PARTITION BY customer_unique_id)  AS n_purchase_events,
           lag(order_purchase_timestamp) OVER p                 AS prev_purchase_ts,
           lead(order_purchase_timestamp) OVER p                AS next_purchase_ts
    FROM events
    WINDOW p AS (PARTITION BY customer_unique_id ORDER BY order_purchase_timestamp)
), intervals AS (
    SELECT *,
           round((extract(epoch FROM (order_purchase_timestamp - prev_purchase_ts)) / 86400)::numeric, 2)
               AS days_since_prev_purchase
    FROM sequenced
    WHERE prev_purchase_ts IS NOT NULL
), overall AS (
    SELECT round(avg(days_since_prev_purchase), 2)                                                 AS overall_avg_interval_days,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY days_since_prev_purchase))::numeric, 2) AS overall_median_interval_days,
           count(*)                                                                                AS n_intervals
    FROM intervals
)
SELECT i.customer_unique_id,
       i.purchase_seq,
       i.n_purchase_events,
       i.first_order_id,
       i.n_orders_in_event,
       i.prev_purchase_ts,
       i.order_purchase_timestamp                                                AS purchase_ts,
       i.next_purchase_ts,
       i.days_since_prev_purchase,
       round(avg(i.days_since_prev_purchase) OVER (PARTITION BY i.customer_unique_id), 2) AS customer_avg_interval_days,
       o.overall_avg_interval_days,
       o.overall_median_interval_days,
       o.n_intervals
FROM intervals i
CROSS JOIN overall o
ORDER BY i.customer_unique_id, i.purchase_seq;
