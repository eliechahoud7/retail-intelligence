-- name: category_monthly_ranking
-- grain: one (purchase month, reporting category) among each month's top 5 categories (ties included)
-- key: year_month, category_reporting
-- purpose: Top categories per month by item-based sales, their share of the month and how their
--          rank moved since the previous month.
-- competencies: CTE chain, many-to-one join, PARTITION BY, RANK, ROW_NUMBER, SUM() OVER (share of
--               total), LAG over a partition
-- notes: fact_order_lines joins dim_products many-to-one (one product per line), so totals are
--        exact. Ranks are computed over ALL categories before the top-5 filter, so
--        rank_prev_month is the true previous rank (NULL if the category had no sales that month).
--        Candidate window only (Q-11). Category labels are provisional (Q-05).
WITH category_month AS (
    SELECT to_char(l.purchase_date, 'YYYY-MM')  AS year_month,
           p.category_reporting,
           count(DISTINCT l.order_id)           AS n_orders,
           sum(l.quantity)                      AS units,
           sum(l.line_price_total)              AS items_price_total
    FROM {w}.fact_order_lines l
    JOIN {w}.dim_products p ON p.product_id = l.product_id
    WHERE l.in_candidate_window
    GROUP BY 1, 2
), ranked AS (
    SELECT *,
           rank() OVER (PARTITION BY year_month ORDER BY items_price_total DESC)                AS rank_in_month,
           row_number() OVER (PARTITION BY year_month ORDER BY items_price_total DESC,
                              category_reporting)                                               AS row_number_in_month,
           round(100 * items_price_total / sum(items_price_total) OVER (PARTITION BY year_month), 2)
                                                                                                 AS share_of_month_pct
    FROM category_month
), with_previous AS (
    SELECT *,
           lag(rank_in_month) OVER (PARTITION BY category_reporting ORDER BY year_month)        AS rank_prev_sales_month,
           lag(year_month)    OVER (PARTITION BY category_reporting ORDER BY year_month)        AS prev_sales_month
    FROM ranked
)
SELECT year_month,
       category_reporting,
       rank_in_month,
       row_number_in_month,
       n_orders,
       units,
       items_price_total,
       share_of_month_pct,
       CASE WHEN prev_sales_month = to_char(to_date(year_month, 'YYYY-MM') - interval '1 month', 'YYYY-MM')
            THEN rank_prev_sales_month END                                                      AS rank_prev_month,
       CASE WHEN prev_sales_month = to_char(to_date(year_month, 'YYYY-MM') - interval '1 month', 'YYYY-MM')
            THEN rank_prev_sales_month - rank_in_month END                                      AS rank_change
FROM with_previous
WHERE rank_in_month <= 5
ORDER BY year_month, rank_in_month, category_reporting;
