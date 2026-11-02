-- name: delivery_delay_distribution
-- grain: one delay bucket (delivered date relative to the estimated date, in whole days)
-- key: delay_bucket
-- purpose: Distribution of eligible deliveries over early / on-estimated-day / late buckets with
--          counts, percentages and the cumulative distribution from earliest to latest.
-- competencies: bucket reference table (VALUES) with explicit bounds, range join, LEFT JOIN so empty
--               buckets show 0, SUM() OVER () percentages, running cumulative distribution
-- notes: Eligible orders only (D-06). delay_days = Gold delivery_vs_estimate_days (delivered date -
--        estimated date; always a whole number of days for eligible orders). Buckets are
--        mutually exclusive and exhaustive over the integers: each has an inclusive lower and upper
--        bound in days (NULL = unbounded), with no gaps or overlaps:
--          early > 14 (<= -15), early 8-14, early 4-7, early 1-3, on the estimated day (0),
--          late 1-3, late 4-7, late 8-14, late > 14 (>= 15).
--        The boundaries are reporting buckets, not a business SLA. Percentages use all eligible
--        orders as the denominator. No analysis window.
WITH buckets (bucket_order, delay_bucket, delivery_class, min_days, max_days) AS (
    VALUES (1, 'early_15_plus_days', 'early', NULL::int, -15),
           (2, 'early_8_14_days',    'early', -14, -8),
           (3, 'early_4_7_days',     'early', -7,  -4),
           (4, 'early_1_3_days',     'early', -3,  -1),
           (5, 'on_estimated_day',   'on_estimated_day', 0, 0),
           (6, 'late_1_3_days',      'late',  1,   3),
           (7, 'late_4_7_days',      'late',  4,   7),
           (8, 'late_8_14_days',     'late',  8,   14),
           (9, 'late_15_plus_days',  'late',  15,  NULL::int)
), eligible AS (
    SELECT delivery_vs_estimate_days::int AS delay_days
    FROM {w}.fact_orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
), counted AS (
    SELECT b.bucket_order, b.delay_bucket, b.delivery_class, b.min_days, b.max_days,
           count(e.delay_days) AS n_orders
    FROM buckets b
    LEFT JOIN eligible e
           ON (b.min_days IS NULL OR e.delay_days >= b.min_days)
          AND (b.max_days IS NULL OR e.delay_days <= b.max_days)
    GROUP BY b.bucket_order, b.delay_bucket, b.delivery_class, b.min_days, b.max_days
)
SELECT bucket_order,
       delay_bucket,
       delivery_class,
       min_days,
       max_days,
       n_orders,
       round(100.0 * n_orders / nullif(sum(n_orders) OVER (), 0), 2)        AS pct_of_orders,
       sum(n_orders) OVER (ORDER BY bucket_order ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_orders,
       round(100.0 * sum(n_orders) OVER (ORDER BY bucket_order ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             / nullif(sum(n_orders) OVER (), 0), 2)                         AS cumulative_pct
FROM counted
ORDER BY bucket_order;
