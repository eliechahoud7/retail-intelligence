-- name: seller_product_mix
-- grain: one (seller, product) combination that has sold at least once
-- key: seller_id, product_id
-- purpose: How each seller's sales are spread over its products, and how a product's sales are
--          spread over the sellers offering it: units, orders, item-based sales, average unit price,
--          rank and share of the product within the seller, and of the seller within the product.
-- competencies: CTE, multi-column GROUP BY at a declared grain, two different PARTITION BY windows
--               on the same rows, RANK, ROW_NUMBER, partition shares via SUM() OVER (PARTITION BY)
-- notes: Aggregated from fact_order_lines (each line has exactly one seller and one product, so no
--        fan-out). A product can be sold by several sellers; seller_share_of_product_pct shows how
--        the product's sales split across them (n_sellers_of_product counts them). Combinations
--        that never sold do not exist in the data and are not invented. Full history, all order
--        statuses; no analysis window.
WITH mix AS (
    SELECT seller_id,
           product_id,
           count(DISTINCT order_id)     AS n_orders,
           sum(quantity)                AS units,
           sum(line_price_total)        AS items_price_total,
           sum(freight_total)           AS items_freight_total,
           min(order_purchase_timestamp)::date AS first_sale_date,
           max(order_purchase_timestamp)::date AS last_sale_date
    FROM {w}.fact_order_lines
    GROUP BY seller_id, product_id
)
SELECT m.seller_id,
       m.product_id,
       p.category_reporting,
       m.n_orders,
       m.units,
       m.items_price_total,
       m.items_freight_total,
       round(m.items_price_total / nullif(m.units, 0), 2)                                        AS avg_unit_price,
       rank()       OVER (PARTITION BY m.seller_id ORDER BY m.items_price_total DESC)            AS product_rank_in_seller,
       row_number() OVER (PARTITION BY m.seller_id ORDER BY m.items_price_total DESC, m.product_id) AS product_row_number_in_seller,
       count(*)     OVER (PARTITION BY m.seller_id)                                              AS n_products_of_seller,
       round(100 * m.items_price_total / nullif(sum(m.items_price_total) OVER (PARTITION BY m.seller_id), 0), 4)
                                                                                                 AS product_share_of_seller_pct,
       rank()       OVER (PARTITION BY m.product_id ORDER BY m.items_price_total DESC)           AS seller_rank_for_product,
       count(*)     OVER (PARTITION BY m.product_id)                                             AS n_sellers_of_product,
       round(100 * m.items_price_total / nullif(sum(m.items_price_total) OVER (PARTITION BY m.product_id), 0), 4)
                                                                                                 AS seller_share_of_product_pct,
       m.first_sale_date,
       m.last_sale_date
FROM mix m
JOIN {w}.dim_products p ON p.product_id = m.product_id
ORDER BY m.seller_id, product_row_number_in_seller;
