-- analytics.category_performance
-- Grain: one reporting category (dim_products.category_reporting, incl. 'uncategorized').
-- Lines are joined many-to-one to dim_products (one product per line), so totals are exact.
-- n_orders counts distinct orders per category; an order with products from several categories
-- is counted in each, so n_orders is NOT additive across categories (units and totals are).
CREATE OR REPLACE VIEW {a}.category_performance AS
WITH products AS (
    SELECT category_reporting, count(*) AS n_products FROM {w}.dim_products GROUP BY category_reporting
), lines AS (
    SELECT p.category_reporting,
           count(DISTINCT l.product_id)                                           AS n_products_sold,
           count(DISTINCT l.order_id)                                             AS n_orders,
           sum(l.quantity)                                                        AS units,
           sum(l.line_price_total)                                                AS items_price_total,
           sum(l.freight_total)                                                   AS items_freight_total,
           count(DISTINCT l.order_id) FILTER (WHERE l.order_status = 'delivered') AS n_orders_delivered,
           sum(l.quantity)            FILTER (WHERE l.order_status = 'delivered') AS units_delivered,
           sum(l.line_price_total)    FILTER (WHERE l.order_status = 'delivered') AS items_price_total_delivered
    FROM {w}.fact_order_lines l
    JOIN {w}.dim_products p ON p.product_id = l.product_id
    GROUP BY p.category_reporting
)
SELECT c.category_reporting,
       c.n_products,
       coalesce(l.n_products_sold, 0)                AS n_products_sold,
       coalesce(l.n_orders, 0)                       AS n_orders,
       coalesce(l.units, 0)                          AS units,
       coalesce(l.items_price_total, 0)              AS items_price_total,
       coalesce(l.items_freight_total, 0)            AS items_freight_total,
       round(l.items_price_total / nullif(l.units, 0), 2) AS avg_unit_price,
       coalesce(l.n_orders_delivered, 0)             AS n_orders_delivered,
       coalesce(l.units_delivered, 0)                AS units_delivered,
       coalesce(l.items_price_total_delivered, 0)    AS items_price_total_delivered
FROM products c
LEFT JOIN lines l ON l.category_reporting = c.category_reporting;

COMMENT ON VIEW {a}.category_performance IS
'Grain: one category_reporting value. Item-based. units and *_total are additive across categories; n_orders is not (multi-category orders count in each). Labels are provisional (Q-05); ''_2'' twins are separate categories.';
