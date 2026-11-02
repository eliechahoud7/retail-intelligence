-- name: sales_best_worst_months
-- grain: one purchase month among the three best and three worst months (ties included)
-- key: year_month
-- purpose: Best and worst performing months by item-based sales, with the different ranking
--          functions side by side.
-- competencies: CTE chain, RANK, DENSE_RANK, ROW_NUMBER, filtering on window results
-- notes: Restricted to the candidate analysis window (in_candidate_window, Q-11): the near-empty
--        months at both ends of the data (e.g. 2016-11 with 0 orders) would otherwise always be
--        "worst". RANK leaves gaps after ties, DENSE_RANK does not, ROW_NUMBER breaks ties by
--        year_month. A month can appear as both best and worst only if the window has < 6 months.
--        Output order: rank_best, then year_month, so tied months always appear in the same order.
WITH monthly AS (
    SELECT year_month,
           sum(n_orders)          AS n_orders,
           sum(units)             AS units,
           sum(items_price_total) AS items_price_total
    FROM {a}.sales_daily
    WHERE in_candidate_window
    GROUP BY year_month
), ranked AS (
    SELECT *,
           rank()       OVER (ORDER BY items_price_total DESC)            AS rank_best,
           rank()       OVER (ORDER BY items_price_total ASC)             AS rank_worst,
           dense_rank() OVER (ORDER BY n_orders DESC)                     AS dense_rank_by_orders,
           row_number() OVER (ORDER BY items_price_total DESC, year_month) AS row_number_by_sales
    FROM monthly
)
SELECT year_month,
       n_orders,
       units,
       items_price_total,
       rank_best,
       rank_worst,
       dense_rank_by_orders,
       row_number_by_sales,
       CASE WHEN rank_best <= 3 AND rank_worst <= 3 THEN 'best and worst'
            WHEN rank_best <= 3 THEN 'best'
            ELSE 'worst' END AS position
FROM ranked
WHERE rank_best <= 3 OR rank_worst <= 3
ORDER BY rank_best, year_month;
