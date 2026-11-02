-- name: product_sales_pareto
-- grain: one product with item-based sales > 0
-- key: product_id
-- purpose: Product contribution to total item-based sales: share, cumulative share, cumulative
--          percentage of products, rank, and an ABC (Pareto) class with explicit thresholds.
-- competencies: bind parameters, CTE chain, ROW_NUMBER, RANK, SUM() OVER () share, cumulative
--               window frames, cumulative product percentage, window aggregate with FILTER
-- params: a_threshold_pct:int=80; b_threshold_pct:int=95
-- notes: Population = products with item-based sales > 0 (never-sold products have no contribution
--        and are excluded; see best_selling_products). Denominator = total item-based sales of that
--        population (= fact_order_lines total) and its product count. Products are ordered by sales
--        DESC, then product_id, so cumulative values are deterministic for ties.
--        ABC class (classic Pareto convention, thresholds are parameters, not a business rule): a
--        product is 'A' if the cumulative share BEFORE it is < :a_threshold_pct (so the product that
--        crosses the threshold is still A), 'B' if it is < :b_threshold_pct, otherwise 'C'.
--        Full history, all order statuses; no analysis window.
WITH sales AS (
    SELECT product_id, category_reporting, units, items_price_total
    FROM {a}.product_performance
    WHERE items_price_total > 0
), cumulative AS (
    SELECT *,
           row_number() OVER ord                                                  AS sales_row_number,
           rank()       OVER (ORDER BY items_price_total DESC)                    AS sales_rank,
           count(*)     OVER ()                                                   AS n_products,
           sum(items_price_total) OVER ()                                         AS total_items_price,
           sum(items_price_total) OVER (ord ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_items_price
    FROM sales
    WINDOW ord AS (ORDER BY items_price_total DESC, product_id)
), classified AS (
    SELECT *,
           CASE WHEN 100 * (cumulative_items_price - items_price_total) / total_items_price < :a_threshold_pct THEN 'A'
                WHEN 100 * (cumulative_items_price - items_price_total) / total_items_price < :b_threshold_pct THEN 'B'
                ELSE 'C' END                                                      AS abc_class
    FROM cumulative
)
SELECT product_id,
       category_reporting,
       sales_rank,
       sales_row_number,
       units,
       items_price_total,
       round(100 * items_price_total / total_items_price, 4)                      AS share_of_items_price_pct,
       round(100 * cumulative_items_price / total_items_price, 4)                 AS cumulative_share_pct,
       round(100.0 * sales_row_number / n_products, 4)                            AS cumulative_pct_of_products,
       abc_class,
       count(*) FILTER (WHERE abc_class = 'A') OVER ()                            AS n_products_in_class_a,
       n_products
FROM classified
ORDER BY sales_row_number;
