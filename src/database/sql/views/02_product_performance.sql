-- analytics.product_performance
-- Grain: one product (all products of dim_products; never-sold products show 0).
-- Item-based only: payments cannot be attributed to products without an allocation rule.
CREATE OR REPLACE VIEW {a}.product_performance AS
WITH lines AS (
    SELECT product_id,
           count(DISTINCT order_id)                                             AS n_orders,
           count(*)                                                             AS n_order_lines,
           sum(quantity)                                                        AS units,
           sum(line_price_total)                                                AS items_price_total,
           sum(freight_total)                                                   AS items_freight_total,
           count(DISTINCT order_id) FILTER (WHERE order_status = 'delivered')   AS n_orders_delivered,
           sum(quantity)            FILTER (WHERE order_status = 'delivered')   AS units_delivered,
           sum(line_price_total)    FILTER (WHERE order_status = 'delivered')   AS items_price_total_delivered,
           count(DISTINCT seller_id)                                            AS n_sellers,
           count(DISTINCT customer_unique_id)                                   AS n_customers,
           min(order_purchase_timestamp)                                        AS first_sale_ts,
           max(order_purchase_timestamp)                                        AS last_sale_ts
    FROM {w}.fact_order_lines GROUP BY product_id
)
SELECT p.product_id,
       p.category_reporting,
       p.category_en_clean,
       p.product_category_name,
       coalesce(l.n_orders, 0)                       AS n_orders,
       coalesce(l.n_order_lines, 0)                  AS n_order_lines,
       coalesce(l.units, 0)                          AS units,
       coalesce(l.items_price_total, 0)              AS items_price_total,
       coalesce(l.items_freight_total, 0)            AS items_freight_total,
       round(l.items_price_total / nullif(l.units, 0), 2) AS avg_unit_price,
       coalesce(l.n_orders_delivered, 0)             AS n_orders_delivered,
       coalesce(l.units_delivered, 0)                AS units_delivered,
       coalesce(l.items_price_total_delivered, 0)    AS items_price_total_delivered,
       coalesce(l.n_sellers, 0)                      AS n_sellers,
       coalesce(l.n_customers, 0)                    AS n_customers,
       l.first_sale_ts,
       l.last_sale_ts,
       p.dq_listing_metadata_missing,
       p.dq_physical_attributes_missing
FROM {w}.dim_products p
LEFT JOIN lines l ON l.product_id = p.product_id;

COMMENT ON VIEW {a}.product_performance IS
'Grain: one product (all of dim_products). Item-based measures from fact_order_lines; payments are not attributable to products. avg_unit_price = items_price_total / units (unit-weighted). *_delivered = order_status delivered. category_reporting labels are provisional (Q-05).';
