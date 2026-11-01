-- analytics.seller_performance
-- Grain: one seller (all sellers of dim_sellers).
-- Item-based only. 1,278 orders contain several sellers, so payments, delivery times and reviews
-- (recorded per order) cannot be attributed to a seller without an allocation rule; they are
-- deliberately not included.
CREATE OR REPLACE VIEW {a}.seller_performance AS
WITH lines AS (
    SELECT seller_id,
           count(DISTINCT order_id)                                             AS n_orders,
           count(*)                                                             AS n_order_lines,
           sum(quantity)                                                        AS units,
           sum(line_price_total)                                                AS items_price_total,
           sum(freight_total)                                                   AS items_freight_total,
           count(DISTINCT order_id) FILTER (WHERE order_status = 'delivered')   AS n_orders_delivered,
           sum(quantity)            FILTER (WHERE order_status = 'delivered')   AS units_delivered,
           sum(line_price_total)    FILTER (WHERE order_status = 'delivered')   AS items_price_total_delivered,
           count(DISTINCT product_id)                                           AS n_products,
           count(DISTINCT customer_unique_id)                                   AS n_customers,
           min(order_purchase_timestamp)                                        AS first_sale_ts,
           max(order_purchase_timestamp)                                        AS last_sale_ts
    FROM {w}.fact_order_lines GROUP BY seller_id
)
SELECT s.seller_id,
       s.seller_state_std,
       s.city_key,
       s.dq_city_malformed,
       coalesce(l.n_orders, 0)                       AS n_orders,
       coalesce(l.n_order_lines, 0)                  AS n_order_lines,
       coalesce(l.units, 0)                          AS units,
       coalesce(l.items_price_total, 0)              AS items_price_total,
       coalesce(l.items_freight_total, 0)            AS items_freight_total,
       round(l.items_price_total / nullif(l.units, 0), 2) AS avg_unit_price,
       coalesce(l.n_orders_delivered, 0)             AS n_orders_delivered,
       coalesce(l.units_delivered, 0)                AS units_delivered,
       coalesce(l.items_price_total_delivered, 0)    AS items_price_total_delivered,
       coalesce(l.n_products, 0)                     AS n_products,
       coalesce(l.n_customers, 0)                    AS n_customers,
       l.first_sale_ts,
       l.last_sale_ts
FROM {w}.dim_sellers s
LEFT JOIN lines l ON l.seller_id = s.seller_id;

COMMENT ON VIEW {a}.seller_performance IS
'Grain: one seller (all of dim_sellers). Item-based measures only; payments, delivery and reviews are per order and 1,278 orders have several sellers, so they are not attributed. *_delivered = order_status delivered.';
