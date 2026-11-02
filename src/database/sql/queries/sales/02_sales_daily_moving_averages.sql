-- name: sales_daily_moving_averages
-- grain: one calendar day from the first to the last purchase date
-- key: purchase_date
-- purpose: Daily orders and item-based sales with 7-day and 28-day moving averages, for
--          smoothing weekly seasonality and spotting short-term changes (e.g. Black Friday).
-- competencies: window frames (ROWS BETWEEN n PRECEDING AND CURRENT ROW), moving averages, named WINDOW
-- notes: analytics.sales_daily has a row for every calendar day (no-order days = 0), so a ROWS
--        frame of 7 rows is exactly 7 calendar days. The first days of the series have shorter
--        frames; ma_7d_complete / ma_28d_complete flag full windows.
SELECT purchase_date,
       day_of_week,
       is_weekend,
       in_candidate_window,
       n_orders,
       units,
       items_price_total,
       round(avg(n_orders)          OVER d7, 2)  AS n_orders_ma_7d,
       round(avg(items_price_total) OVER d7, 2)  AS items_price_ma_7d,
       round(avg(n_orders)          OVER d28, 2) AS n_orders_ma_28d,
       round(avg(items_price_total) OVER d28, 2) AS items_price_ma_28d,
       count(*) OVER d7  = 7                      AS ma_7d_complete,
       count(*) OVER d28 = 28                     AS ma_28d_complete
FROM {a}.sales_daily
WINDOW d7  AS (ORDER BY purchase_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
       d28 AS (ORDER BY purchase_date ROWS BETWEEN 27 PRECEDING AND CURRENT ROW)
ORDER BY purchase_date;
