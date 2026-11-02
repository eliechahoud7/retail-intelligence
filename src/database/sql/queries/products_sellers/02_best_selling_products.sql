-- name: best_selling_products
-- grain: one product (every product of dim_products, including never-sold products)
-- key: product_id
-- purpose: Product ranking by units sold and by item-based sales, overall and within the product's
--          reporting category, with orders, sellers, average unit price and first / last sale date.
-- competencies: building on a verified view, RANK and DENSE_RANK on two different measures,
--               PARTITION BY (rank within category), ROW_NUMBER for a deterministic order
-- notes: Measures come from analytics.product_performance (aggregated from fact_order_lines).
--        avg_unit_price = items_price_total / units (unit-weighted); NULL for never-sold products,
--        whose units and sales are 0 and which rank last (tied). Full history, all order statuses;
--        no analysis window (ranking of totals). Category labels are provisional (Q-05).
SELECT product_id,
       category_reporting,
       rank()       OVER (ORDER BY units DESC)                                        AS units_rank,
       dense_rank() OVER (ORDER BY units DESC)                                        AS units_dense_rank,
       rank()       OVER (ORDER BY items_price_total DESC)                            AS sales_rank,
       rank()       OVER (PARTITION BY category_reporting ORDER BY items_price_total DESC) AS sales_rank_in_category,
       row_number() OVER (ORDER BY units DESC, items_price_total DESC, product_id)    AS row_number_by_units,
       units,
       n_orders,
       n_sellers,
       items_price_total,
       avg_unit_price,
       first_sale_ts::date                                                            AS first_sale_date,
       last_sale_ts::date                                                             AS last_sale_date,
       units = 0                                                                      AS never_sold
FROM {a}.product_performance
ORDER BY row_number_by_units;
