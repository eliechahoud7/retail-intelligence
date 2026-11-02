-- name: delivery_state_performance
-- grain: one customer state (customer_state_std of the order's customer record), every state with
--        at least one order
-- key: customer_state_std
-- purpose: Delivery performance per customer state - deliveries, average / median / p90 delivery
--          duration, average delay, on-time and late rates - ranked by speed and by punctuality.
-- competencies: CTE, conditional aggregation, percentile_cont, RANK / DENSE_RANK with NULLS LAST,
--               LEFT JOIN from the full state population (states without eligible orders kept)
-- notes: Aggregated directly from fact_orders (one row per order; no join to order lines or
--        payments, so nothing is multiplied) because p90 and the on-time split are not in
--        analytics.delivery_by_state. Eligibility and definitions as in delivery_monthly_performance
--        (D-06; duration = purchase_to_delivery_days; delay = delivered date - estimated date;
--        on-time = delay <= 0). speed_rank: 1 = shortest MEDIAN duration (median_delivery_days);
--        punctuality_rank: 1 = highest on-time rate. delivery_region_ranking ranks regions by
--        AVERAGE duration instead (different metric; the two speed ranks are not directly
--        comparable). States without eligible orders get NULL metrics and NULL ranks.
--        The state is the location of the order's customer record. No analysis window.
WITH states AS (
    SELECT DISTINCT customer_state_std FROM {w}.fact_orders
), eligible AS (
    SELECT customer_state_std,
           count(*)                                                       AS n_delivered,
           count(*) FILTER (WHERE delivery_vs_estimate_days > 0)          AS n_late,
           round(avg(purchase_to_delivery_days)::numeric, 2)              AS avg_delivery_days,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY purchase_to_delivery_days))::numeric, 2) AS median_delivery_days,
           round((percentile_cont(0.9) WITHIN GROUP (ORDER BY purchase_to_delivery_days))::numeric, 2) AS p90_delivery_days,
           round(avg(order_estimated_delivery_date::date - purchase_date)::numeric, 2) AS avg_estimated_days,
           round(avg(delivery_vs_estimate_days)::numeric, 2)              AS avg_delay_days
    FROM {w}.fact_orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
    GROUP BY customer_state_std
), metrics AS (
    SELECT s.customer_state_std,
           coalesce(e.n_delivered, 0)                                     AS n_delivered,
           coalesce(e.n_late, 0)                                          AS n_late,
           round(1.0 * (e.n_delivered - e.n_late) / nullif(e.n_delivered, 0), 4) AS on_time_rate,
           round(1.0 * e.n_late / nullif(e.n_delivered, 0), 4)            AS late_rate,
           e.avg_delivery_days, e.median_delivery_days, e.p90_delivery_days, e.avg_estimated_days, e.avg_delay_days
    FROM states s
    LEFT JOIN eligible e ON e.customer_state_std = s.customer_state_std
)
SELECT customer_state_std,
       n_delivered,
       n_late,
       on_time_rate,
       late_rate,
       avg_delivery_days,
       median_delivery_days,
       p90_delivery_days,
       avg_estimated_days,
       avg_delay_days,
       CASE WHEN n_delivered > 0 THEN rank() OVER (ORDER BY median_delivery_days ASC NULLS LAST) END AS speed_rank,
       CASE WHEN n_delivered > 0 THEN dense_rank() OVER (ORDER BY median_delivery_days ASC NULLS LAST) END AS speed_dense_rank,
       CASE WHEN n_delivered > 0 THEN rank() OVER (ORDER BY on_time_rate DESC NULLS LAST) END       AS punctuality_rank
FROM metrics
ORDER BY speed_rank NULLS LAST, customer_state_std;
