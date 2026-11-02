-- name: customer_purchase_summary
-- grain: one person (customer_unique_id), every person of dim_customers
-- key: customer_unique_id
-- purpose: Per-person purchase history: first and most recent purchase, number of orders,
--          orders with items, historical item-based spending and average spending per order.
-- competencies: CTE, conditional aggregation (FILTER), many-to-one person mapping, NULL-safe
--               averages, LEFT JOIN from the dimension (population explicit)
-- notes: The person is customer_unique_id; fact_orders.customer_id is a per-order key (one per
--        order, D-01), so n_customer_ids always equals n_orders. Spending = item-based
--        fact_orders.items_price_total (= sum of its order lines; one row per order, so no fan-out);
--        freight and payments are not included. Orders without items (Q-16) count as orders but
--        add no spending: spending and the average are NULL (not 0) for persons with no items.
--        is_repeat_customer = more than one order of any status (same rule as
--        analytics.customer_performance). FULL-HISTORY, DESCRIPTIVE ONLY: not valid prediction
--        features without an explicit cut-off date.
WITH person_orders AS (
    SELECT customer_unique_id,
           count(*)                                         AS n_orders,
           count(DISTINCT customer_id)                      AS n_customer_ids,
           count(*) FILTER (WHERE has_items)                AS n_orders_with_items,
           count(*) FILTER (WHERE order_status = 'delivered') AS n_orders_delivered,
           min(order_purchase_timestamp)                    AS first_purchase_ts,
           max(order_purchase_timestamp)                    AS last_purchase_ts,
           sum(items_price_total)                           AS items_price_total
    FROM {w}.fact_orders
    GROUP BY customer_unique_id
)
SELECT c.customer_unique_id,
       o.first_purchase_ts,
       o.last_purchase_ts,
       o.first_purchase_ts::date                                         AS first_purchase_date,
       o.last_purchase_ts::date                                          AS last_purchase_date,
       coalesce(o.n_orders, 0)                                           AS n_orders,
       coalesce(o.n_customer_ids, 0)                                     AS n_customer_ids,
       coalesce(o.n_orders_with_items, 0)                                AS n_orders_with_items,
       coalesce(o.n_orders_delivered, 0)                                 AS n_orders_delivered,
       o.items_price_total,
       round(o.items_price_total / nullif(o.n_orders_with_items, 0), 2)  AS avg_items_price_per_order_with_items,
       coalesce(o.n_orders, 0) > 1                                       AS is_repeat_customer
FROM {w}.dim_customers c
LEFT JOIN person_orders o ON o.customer_unique_id = c.customer_unique_id
ORDER BY c.customer_unique_id;
