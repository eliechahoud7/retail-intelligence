-- name: sales_by_weekday
-- grain: one ISO day of week (1 = Monday ... 7 = Sunday)
-- key: day_of_week
-- purpose: Weekly purchasing pattern: average orders and item-based sales per weekday and each
--          weekday's share of total sales.
-- competencies: aggregation, scalar subqueries (share of a grand total), averages over calendar days
-- notes: Averages are per calendar day including no-order days (sales_daily is zero-filled), so
--        they are true daily averages. Candidate window only (Q-11); the subquery uses the same
--        filter, so shares sum to 100.
SELECT day_of_week,
       min(to_char(purchase_date, 'FMDay'))                     AS day_name,
       count(*)                                                 AS n_days,
       sum(n_orders)                                            AS n_orders,
       round(avg(n_orders), 2)                                  AS avg_orders_per_day,
       sum(items_price_total)                                   AS items_price_total,
       round(avg(items_price_total), 2)                         AS avg_items_price_per_day,
       round(100 * sum(items_price_total)
             / (SELECT nullif(sum(items_price_total), 0) FROM {a}.sales_daily WHERE in_candidate_window), 2)
                                                                AS share_of_items_price_pct,
       round(100.0 * sum(n_orders)
             / (SELECT nullif(sum(n_orders), 0) FROM {a}.sales_daily WHERE in_candidate_window), 2)
                                                                AS share_of_orders_pct
FROM {a}.sales_daily
WHERE in_candidate_window
GROUP BY day_of_week
ORDER BY day_of_week;
