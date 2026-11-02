-- name: repeat_customer_analysis
-- grain: one (customer_segment, n_orders) frequency group, plus a subtotal row per segment
--        (n_orders NULL) and one grand-total row (customer_segment = 'all')
-- key: customer_segment, n_orders
-- purpose: One-time versus repeat customers: customer counts and percentages, orders by purchase
--          frequency, and each group's contribution to historical item-based sales.
-- competencies: CTE chain, GROUPING SETS, GROUPING(), window-free shares via a grand-total CTE,
--               explicit denominators
-- notes: Population = every person with at least one order (all of dim_customers). Denominators:
--        pct_of_customers over that population; pct_of_orders over all orders; pct_of_items_price
--        over total item-based sales. Repeat = more than one order of any status (same-second split
--        orders count as separate orders, Q-09). Persons whose orders have no items contribute 0 to
--        the item-based total and are counted in n_customers_without_items. FULL-HISTORY,
--        DESCRIPTIVE ONLY.
WITH person AS (
    SELECT customer_unique_id,
           count(*)                                         AS n_orders,
           count(*) FILTER (WHERE has_items)                AS n_orders_with_items,
           coalesce(sum(items_price_total), 0)              AS items_price_total
    FROM {w}.fact_orders
    GROUP BY customer_unique_id
), segmented AS (
    SELECT *, CASE WHEN n_orders > 1 THEN 'repeat' ELSE 'one_time' END AS customer_segment
    FROM person
), grouped AS (
    SELECT CASE WHEN grouping(customer_segment) = 1 THEN 'all' ELSE customer_segment END AS customer_segment,
           CASE WHEN grouping(n_orders) = 0 THEN n_orders END                           AS n_orders,
           count(*)                                                AS n_customers,
           count(*) FILTER (WHERE n_orders_with_items = 0)         AS n_customers_without_items,
           sum(n_orders)                                           AS n_orders_total,
           sum(n_orders_with_items)                                AS n_orders_with_items,
           sum(items_price_total)                                  AS items_price_total
    FROM segmented
    GROUP BY GROUPING SETS ((customer_segment, n_orders), (customer_segment), ())
), totals AS (
    SELECT n_customers AS all_customers, n_orders_total AS all_orders, items_price_total AS all_items_price
    FROM grouped WHERE customer_segment = 'all'
)
SELECT g.customer_segment,
       g.n_orders,
       g.n_customers,
       round(100.0 * g.n_customers / nullif(t.all_customers, 0), 2)       AS pct_of_customers,
       g.n_customers_without_items,
       g.n_orders_total,
       round(100.0 * g.n_orders_total / nullif(t.all_orders, 0), 2)       AS pct_of_orders,
       g.n_orders_with_items,
       g.items_price_total,
       round(100 * g.items_price_total / nullif(t.all_items_price, 0), 2) AS pct_of_items_price,
       round(g.items_price_total / nullif(g.n_customers, 0), 2)           AS items_price_per_customer
FROM grouped g CROSS JOIN totals t
ORDER BY CASE g.customer_segment WHEN 'one_time' THEN 1 WHEN 'repeat' THEN 2 ELSE 3 END,
         g.n_orders NULLS LAST;
