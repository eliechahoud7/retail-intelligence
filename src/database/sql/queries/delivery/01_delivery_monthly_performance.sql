-- name: delivery_monthly_performance
-- grain: one DELIVERY month (month of order_delivered_customer_date), every month from the first to
--        the last delivery month (months without deliveries appear with 0 and NULL metrics)
-- key: delivery_month
-- purpose: Monthly delivery performance: deliveries, delivery duration (average, median, 90th
--          percentile), estimated duration, average delay, early / on-estimated-day / late counts,
--          on-time and late rates, and the change versus the previous calendar month.
-- competencies: CTE chain, calendar spine from dim_dates (gap-free months), conditional aggregation,
--               percentile_cont (median, p90), LAG for month-over-month change, NULL-safe rates
-- notes: Eligible orders (strategy D-06): order_status = 'delivered' AND a delivery timestamp.
--        Canceled / other orders and delivered orders without a delivery date are never included
--        and never treated as 0-day deliveries.
--        delivery duration = purchase_to_delivery_days (exact days purchase -> delivery, Gold).
--        estimated duration = estimated delivery date - purchase date (day level, D-08).
--        delay = delivery_vs_estimate_days = delivered date - estimated date (day level, Gold):
--        < 0 early, 0 on the estimated day, > 0 late (= Gold is_late). on-time = not late.
--        Duration and delay are different measures. Grouped by DELIVERY month (analytics.
--        delivery_monthly groups by purchase month). No analysis window is applied; sparse months
--        show their n_delivered. Rates and averages are NULL when a month has no deliveries.
WITH months AS (
    SELECT DISTINCT year_month AS delivery_month
    FROM {w}.dim_dates
    WHERE date BETWEEN (SELECT min(order_delivered_customer_date)::date FROM {w}.fact_orders
                        WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL)
                   AND (SELECT max(order_delivered_customer_date)::date FROM {w}.fact_orders
                        WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL)
), eligible AS (
    SELECT to_char(order_delivered_customer_date, 'YYYY-MM')                AS delivery_month,
           purchase_to_delivery_days                                        AS delivery_days,
           order_estimated_delivery_date::date - purchase_date              AS estimated_days,
           delivery_vs_estimate_days                                        AS delay_days
    FROM {w}.fact_orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
), monthly AS (
    SELECT delivery_month,
           count(*)                                                         AS n_delivered,
           count(*) FILTER (WHERE delay_days < 0)                           AS n_early,
           count(*) FILTER (WHERE delay_days = 0)                           AS n_on_estimated_day,
           count(*) FILTER (WHERE delay_days > 0)                           AS n_late,
           round(avg(delivery_days)::numeric, 2)                            AS avg_delivery_days,
           round((percentile_cont(0.5) WITHIN GROUP (ORDER BY delivery_days))::numeric, 2) AS median_delivery_days,
           round((percentile_cont(0.9) WITHIN GROUP (ORDER BY delivery_days))::numeric, 2) AS p90_delivery_days,
           round(avg(estimated_days)::numeric, 2)                           AS avg_estimated_days,
           round(avg(delay_days)::numeric, 2)                               AS avg_delay_days
    FROM eligible
    GROUP BY delivery_month
), joined AS (
    SELECT m.delivery_month,
           coalesce(x.n_delivered, 0)        AS n_delivered,
           coalesce(x.n_early, 0)            AS n_early,
           coalesce(x.n_on_estimated_day, 0) AS n_on_estimated_day,
           coalesce(x.n_late, 0)             AS n_late,
           x.avg_delivery_days, x.median_delivery_days, x.p90_delivery_days, x.avg_estimated_days, x.avg_delay_days
    FROM months m
    LEFT JOIN monthly x ON x.delivery_month = m.delivery_month
)
SELECT delivery_month,
       n_delivered,
       n_early,
       n_on_estimated_day,
       n_late,
       round(1.0 * (n_early + n_on_estimated_day) / nullif(n_delivered, 0), 4)   AS on_time_rate,
       round(1.0 * n_late / nullif(n_delivered, 0), 4)                          AS late_rate,
       avg_delivery_days,
       median_delivery_days,
       p90_delivery_days,
       avg_estimated_days,
       avg_delay_days,
       lag(avg_delivery_days) OVER w                                            AS avg_delivery_days_prev_month,
       avg_delivery_days - lag(avg_delivery_days) OVER w                        AS avg_delivery_days_change,
       round(1.0 * n_late / nullif(n_delivered, 0), 4)
         - lag(round(1.0 * n_late / nullif(n_delivered, 0), 4)) OVER w          AS late_rate_change
FROM joined
WINDOW w AS (ORDER BY delivery_month)
ORDER BY delivery_month;
