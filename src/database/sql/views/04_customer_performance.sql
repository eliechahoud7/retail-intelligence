-- analytics.customer_performance
-- Grain: one person (customer_unique_id), all persons of dim_customers.
-- FULL-HISTORY measures: descriptive only. They are NOT valid prediction features; any model
-- must derive time-bounded features from the fact tables with an explicit cut-off date.
-- Averages are explicit ratios over the orders that have the measure (Q-16: orders without
-- items / payments are excluded from the respective denominator, never counted as 0).
CREATE OR REPLACE VIEW {a}.customer_performance AS
WITH o AS (
    SELECT customer_unique_id,
           count(*) FILTER (WHERE has_items)    AS n_orders_with_items,
           count(*) FILTER (WHERE has_payment)  AS n_orders_with_payment,
           sum(review_count)                    AS n_review_links
    FROM {w}.fact_orders GROUP BY customer_unique_id
)
SELECT c.customer_unique_id,
       c.cohort_month,
       c.first_purchase_ts,
       c.last_purchase_ts,
       round((extract(epoch FROM (c.last_purchase_ts - c.first_purchase_ts)) / 86400)::numeric, 2)
                                                  AS days_first_to_last_purchase,
       c.n_orders,
       c.n_orders > 1                             AS is_repeat_customer,
       o.n_orders_with_items,
       o.n_orders_with_payment,
       c.n_items,
       c.items_price_total,
       c.items_freight_total,
       c.payment_total,
       round(c.items_price_total / nullif(o.n_orders_with_items, 0), 2)  AS avg_items_price_per_order,
       round(c.payment_total / nullif(o.n_orders_with_payment, 0), 2)    AS avg_payment_per_order,
       c.n_orders_delivered,
       c.n_items_delivered,
       c.items_price_total_delivered,
       c.items_freight_total_delivered,
       c.payment_total_delivered,
       c.first_delivered_purchase_ts,
       c.last_delivered_purchase_ts,
       o.n_review_links,
       c.n_customer_ids,
       c.n_distinct_zips,
       c.n_distinct_states
FROM {w}.dim_customers c
LEFT JOIN o ON o.customer_unique_id = c.customer_unique_id;

COMMENT ON VIEW {a}.customer_performance IS
'Grain: one person (customer_unique_id). FULL-HISTORY descriptive measures (RFM inputs, cohorts): not valid prediction features without an explicit cut-off date (target leakage). Averages divide by orders that have items / payments. No address at person level (it varies by order; see fact_orders).';
