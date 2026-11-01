-- analytics.delivery_by_state
-- Grain: one customer state (customer_state_std of the order's customer record).
-- Same eligible population and measures as analytics.delivery_monthly (strategy D-06, D-08).
CREATE OR REPLACE VIEW {a}.delivery_by_state AS
SELECT customer_state_std,
       count(*)                                                                    AS n_orders,
       count(*) FILTER (WHERE order_status = 'delivered')                          AS n_orders_delivered,
       count(*) FILTER (WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL)
                                                                                   AS n_delivery_eligible,
       count(*) FILTER (WHERE dq_delivered_missing_delivery_date)                  AS n_delivered_missing_date,
       count(*) FILTER (WHERE dq_status_canceled_with_date)                        AS n_canceled_with_delivery_date,
       count(*) FILTER (WHERE eligible AND is_late)                                AS n_late,
       round(count(*) FILTER (WHERE eligible AND is_late)::numeric
             / nullif(count(*) FILTER (WHERE eligible), 0), 4)                     AS late_rate,
       round(avg(purchase_to_delivery_days) FILTER (WHERE eligible)::numeric, 2)   AS avg_purchase_to_delivery_days,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY purchase_to_delivery_days)
              FILTER (WHERE eligible))::numeric, 2)                                AS median_purchase_to_delivery_days,
       round(avg(carrier_to_customer_days) FILTER (WHERE eligible)::numeric, 2)    AS avg_carrier_to_customer_days,
       round(avg(approval_hours) FILTER (WHERE eligible)::numeric, 2)              AS avg_approval_hours,
       round(avg(delivery_vs_estimate_days) FILTER (WHERE eligible)::numeric, 2)   AS avg_delivery_vs_estimate_days,
       count(*) FILTER (WHERE eligible AND carrier_to_customer_days IS NULL)       AS n_eligible_without_valid_transit
FROM (SELECT *, (order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL) AS eligible
      FROM {w}.fact_orders) o
GROUP BY customer_state_std;

COMMENT ON VIEW {a}.delivery_by_state IS
'Grain: one customer state (location of the order''s customer record). Same D-06 eligible population and measures as the delivery_monthly view.';
