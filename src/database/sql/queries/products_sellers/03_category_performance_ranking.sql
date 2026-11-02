-- name: category_performance_ranking
-- grain: one reporting category (dim_products.category_reporting, including 'uncategorized')
-- key: category_reporting
-- purpose: Category ranking by item-based sales with products, distinct orders, units, share of total
--          sales and units, and cumulative sales share.
-- competencies: building on a verified view, RANK, DENSE_RANK, SUM() OVER () shares, running
--               SUM() OVER cumulative share
-- notes: Measures come from analytics.category_performance (order lines joined many-to-one to
--        dim_products). n_orders counts distinct orders per category: an order with products from
--        several categories is counted in EACH of them, so n_orders is NOT additive
--        across categories; units, sales and shares are additive. category_reporting is the approved
--        reporting label (D-12; provisional wording Q-05; '_2' twins kept separate). Full history,
--        all order statuses; no analysis window.
SELECT category_reporting,
       rank()       OVER (ORDER BY items_price_total DESC)                             AS sales_rank,
       dense_rank() OVER (ORDER BY items_price_total DESC)                             AS sales_dense_rank,
       rank()       OVER (ORDER BY units DESC)                                         AS units_rank,
       n_products,
       n_products_sold,
       n_orders,
       units,
       items_price_total,
       avg_unit_price,
       round(100 * items_price_total / nullif(sum(items_price_total) OVER (), 0), 4)  AS share_of_items_price_pct,
       round(100.0 * units / nullif(sum(units) OVER (), 0), 4)                        AS share_of_units_pct,
       round(100 * sum(items_price_total) OVER (ORDER BY items_price_total DESC, category_reporting
                                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             / nullif(sum(items_price_total) OVER (), 0), 4)                          AS cumulative_share_pct
FROM {a}.category_performance
ORDER BY sales_rank, category_reporting;
