-- name: estimated_vs_actual_delivery
-- grain: one eligible delivered order (order_id)
-- key: order_id
-- purpose: Per-order comparison of the promised (estimated) and the actual delivery: estimated
--          duration, actual duration, delay, early / on-estimated-day / late class, absolute error,
--          and population-wide error metrics alongside each order.
-- competencies: derived measures with explicit timestamp semantics, CASE classification, window
--               aggregates over the whole population (AVG / SUM / COUNT OVER ()), PERCENT_RANK
-- notes: Eligible orders only (D-06: status 'delivered' AND a delivery timestamp); one row per order
--        from fact_orders, no join to lines or payments.
--        Three different measures - do not confuse them:
--          estimated_days         = estimated delivery date - purchase date (day level; the
--                                   estimate is date-only, D-08);
--          actual_days            = delivered date - purchase date (day level, comparable with
--                                   estimated_days);
--          actual_elapsed_days    = purchase_to_delivery_days (exact elapsed time, Gold);
--          delay_days             = delivered date - estimated date = actual_days - estimated_days
--                                   (Gold delivery_vs_estimate_days; < 0 early, 0 on the day, > 0 late).
--        abs_error_days = |delay_days| (how far the estimate was off, either direction).
--        mean_delay_days (bias) and mean_abs_error_days (MAE) are over all eligible orders.
--        delay_percent_rank: 0 = earliest delivery relative to its estimate, 1 = latest. No
--        analysis window.
WITH eligible AS (
    SELECT order_id,
           customer_state_std,
           order_purchase_timestamp,
           order_estimated_delivery_date::date                            AS estimated_delivery_date,
           order_delivered_customer_date,
           order_estimated_delivery_date::date - purchase_date            AS estimated_days,
           order_delivered_customer_date::date - purchase_date            AS actual_days,
           round(purchase_to_delivery_days::numeric, 2)                   AS actual_elapsed_days,
           delivery_vs_estimate_days::int                                 AS delay_days
    FROM {w}.fact_orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
)
SELECT order_id,
       customer_state_std,
       order_purchase_timestamp,
       estimated_delivery_date,
       order_delivered_customer_date,
       estimated_days,
       actual_days,
       actual_elapsed_days,
       delay_days,
       CASE WHEN delay_days < 0 THEN 'early' WHEN delay_days = 0 THEN 'on_estimated_day' ELSE 'late' END AS delivery_class,
       abs(delay_days)                                                    AS abs_error_days,
       round(avg(delay_days) OVER (), 4)                                  AS mean_delay_days,
       round(avg(abs(delay_days)) OVER (), 4)                             AS mean_abs_error_days,
       round(1.0 * count(*) FILTER (WHERE delay_days > 0) OVER () / count(*) OVER (), 4) AS population_late_rate,
       round(percent_rank() OVER (ORDER BY delay_days)::numeric, 4)       AS delay_percent_rank
FROM eligible
ORDER BY order_id;
