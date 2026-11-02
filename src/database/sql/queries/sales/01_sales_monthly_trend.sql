-- name: sales_monthly_trend
-- grain: one purchase month (every month from the first to the last purchase date)
-- key: year_month
-- purpose: Monthly sales trend with month-over-month and year-over-year comparisons, running
--          totals and a 3-month moving average, item-based and payment-based side by side.
-- competencies: CTE, aggregation, LAG (offset 1 and 12), running total, moving average, named WINDOW
-- notes: Built on analytics.sales_daily, which is gap-free by calendar day, so months are
--        contiguous and LAG(.., 12) is exactly "same month last year". Growth is NULL when the
--        base is 0 or missing (never divided by zero). is_complete_month marks the partial first
--        and last months; trend conclusions should use in_candidate_window months (Q-11).
--        mom_base_in_window / yoy_base_in_window say whether the comparison month is itself in
--        the window: growth against a near-empty 2016 month is arithmetically right but not
--        meaningful (e.g. 2017-12 vs 2016-12 with 1 order).
--        items_* = item-based, payment_* = payment-based; they are never combined (Q-03).
WITH monthly AS (
    SELECT year_month,
           count(*)                                          AS n_days_in_data,
           bool_and(in_candidate_window)                     AS in_candidate_window,
           count(*) = extract(day FROM (date_trunc('month', min(purchase_date)) + interval '1 month - 1 day'))
                                                             AS is_complete_month,
           sum(n_orders)                                     AS n_orders,
           sum(n_orders_delivered)                           AS n_orders_delivered,
           sum(units)                                        AS units,
           sum(items_price_total)                            AS items_price_total,
           sum(items_freight_total)                          AS items_freight_total,
           sum(payment_total)                                AS payment_total
    FROM {a}.sales_daily
    GROUP BY year_month
)
SELECT year_month,
       n_days_in_data,
       is_complete_month,
       in_candidate_window,
       n_orders,
       n_orders_delivered,
       units,
       items_price_total,
       items_freight_total,
       payment_total,
       lag(items_price_total) OVER w                                        AS items_price_total_prev_month,
       items_price_total - lag(items_price_total) OVER w                    AS items_price_mom_change,
       round(100 * (items_price_total - lag(items_price_total) OVER w)
             / nullif(lag(items_price_total) OVER w, 0), 2)                 AS items_price_mom_growth_pct,
       coalesce(lag(in_candidate_window) OVER w, false)                     AS mom_base_in_window,
       lag(items_price_total, 12) OVER w                                    AS items_price_total_same_month_prev_year,
       round(100 * (items_price_total - lag(items_price_total, 12) OVER w)
             / nullif(lag(items_price_total, 12) OVER w, 0), 2)             AS items_price_yoy_growth_pct,
       coalesce(lag(in_candidate_window, 12) OVER w, false)                 AS yoy_base_in_window,
       round(100 * (payment_total - lag(payment_total) OVER w)
             / nullif(lag(payment_total) OVER w, 0), 2)                     AS payment_mom_growth_pct,
       sum(items_price_total) OVER (w ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                                                                            AS items_price_running_total,
       sum(payment_total) OVER (w ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                                                                            AS payment_running_total,
       round(avg(items_price_total) OVER (w ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2)
                                                                            AS items_price_moving_avg_3m
FROM monthly
WINDOW w AS (ORDER BY year_month)
ORDER BY year_month;
