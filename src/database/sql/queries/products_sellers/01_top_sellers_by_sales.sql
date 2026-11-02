-- name: top_sellers_by_sales
-- grain: one seller (every seller of dim_sellers, including sellers without sales)
-- key: seller_id
-- purpose: Seller ranking by item-based sales with orders, order lines, units, freight (separate),
--          distinct products and customers, share of total item-based sales, and how many of the
--          seller's orders also contain other sellers.
-- competencies: building on a verified view, CTE, RANK, DENSE_RANK, SUM() OVER () share of total,
--               running SUM() OVER for cumulative share, many-to-one join of an order-level CTE
-- notes: Item-based measures come from analytics.seller_performance (aggregated from
--        fact_order_lines). Some orders contain several sellers: payments, reviews and delivery
--        times are recorded per order and are NOT attributed to sellers. n_orders_shared_with_other_
--        sellers counts the seller's orders that contain at least one other seller; n_orders is
--        therefore not additive across sellers. Full history, all order statuses (the view's
--        *_delivered columns give the delivered variant); no analysis window is applied because
--        this is a ranking of totals, not a trend. Sellers without sales rank last (tied) with share 0.
WITH order_sellers AS (
    SELECT order_id, count(DISTINCT seller_id) AS n_sellers_in_order
    FROM {w}.fact_order_lines
    GROUP BY order_id
), shared AS (
    SELECT l.seller_id,
           count(DISTINCT l.order_id) FILTER (WHERE o.n_sellers_in_order > 1) AS n_orders_shared_with_other_sellers
    FROM {w}.fact_order_lines l
    JOIN order_sellers o ON o.order_id = l.order_id
    GROUP BY l.seller_id
)
SELECT s.seller_id,
       s.seller_state_std,
       rank()       OVER (ORDER BY s.items_price_total DESC)                         AS sales_rank,
       dense_rank() OVER (ORDER BY s.items_price_total DESC)                         AS sales_dense_rank,
       s.n_orders,
       coalesce(sh.n_orders_shared_with_other_sellers, 0)                            AS n_orders_shared_with_other_sellers,
       s.n_order_lines,
       s.units,
       s.items_price_total,
       s.items_freight_total,
       s.avg_unit_price,
       s.n_products,
       s.n_customers,
       round(100 * s.items_price_total / nullif(sum(s.items_price_total) OVER (), 0), 4) AS share_of_items_price_pct,
       round(100 * sum(s.items_price_total) OVER (ORDER BY s.items_price_total DESC, s.seller_id
                                                  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             / nullif(sum(s.items_price_total) OVER (), 0), 4)                      AS cumulative_share_pct,
       s.first_sale_ts::date                                                          AS first_sale_date,
       s.last_sale_ts::date                                                           AS last_sale_date
FROM {a}.seller_performance s
LEFT JOIN shared sh ON sh.seller_id = s.seller_id
ORDER BY sales_rank, s.seller_id;
