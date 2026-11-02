-- name: customer_spending_ranking
-- grain: one person among the top 100 by historical item-based spending (rank <= 100, ties included)
-- key: customer_unique_id
-- purpose: Top customers by historical item-based spending, their share of total item-based
--          sales and the cumulative share (concentration / Pareto view).
-- competencies: CTE chain, RANK, DENSE_RANK, ROW_NUMBER, running SUM() OVER for cumulative share,
--               NTILE (spending decile), share of a grand total via SUM() OVER ()
-- notes: Population = persons with item-based spending (persons whose orders have no items are
--        excluded, not ranked as 0). Shares, cumulative shares and deciles are computed over that
--        whole population BEFORE the top-100 filter. Ties share a RANK; the cumulative share uses a
--        deterministic order (spending DESC, then customer_unique_id). spending_decile (NTILE) is
--        NOT tie-aware: it uses the same order, so customers with EQUAL spending can be split across
--        two adjacent deciles, decided by customer_unique_id. The split is deterministic; use
--        spending_rank / spending_dense_rank when ties must stay together. Spending = item-based
--        fact_orders.items_price_total over all orders. FULL-HISTORY, DESCRIPTIVE ONLY: not a valid
--        prediction feature without an explicit cut-off date.
WITH spend AS (
    SELECT customer_unique_id,
           count(*) FILTER (WHERE has_items)  AS n_orders_with_items,
           sum(items_price_total)             AS items_price_total
    FROM {w}.fact_orders
    GROUP BY customer_unique_id
    HAVING sum(items_price_total) IS NOT NULL
), ranked AS (
    SELECT *,
           rank()       OVER (ORDER BY items_price_total DESC)                     AS spending_rank,
           dense_rank() OVER (ORDER BY items_price_total DESC)                     AS spending_dense_rank,
           row_number() OVER (ORDER BY items_price_total DESC, customer_unique_id) AS spending_row_number,
           ntile(10)    OVER (ORDER BY items_price_total DESC, customer_unique_id) AS spending_decile,
           round(100 * items_price_total / sum(items_price_total) OVER (), 4)      AS share_of_items_price_pct,
           round(100 * sum(items_price_total) OVER (ORDER BY items_price_total DESC, customer_unique_id
                                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                 / sum(items_price_total) OVER (), 4)                              AS cumulative_share_pct,
           count(*) OVER ()                                                        AS n_ranked_customers
    FROM spend
)
SELECT customer_unique_id,
       spending_rank,
       spending_dense_rank,
       spending_row_number,
       spending_decile,
       n_orders_with_items,
       items_price_total,
       share_of_items_price_pct,
       cumulative_share_pct,
       n_ranked_customers
FROM ranked
WHERE spending_rank <= 100
ORDER BY spending_row_number;
