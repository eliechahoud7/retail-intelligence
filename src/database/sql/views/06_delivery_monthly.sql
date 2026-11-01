-- analytics.delivery_monthly
-- Grain: one purchase month.
-- Delivery metrics use the approved eligible population (strategy D-06): orders with status
-- 'delivered' AND a delivery date. Each duration is averaged only where it is defined (Gold sets
-- a duration to NULL where its D-08 chronology flag makes it invalid). Lateness is day-level.
CREATE OR REPLACE VIEW {a}.delivery_monthly AS
SELECT purchase_year_month,
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
GROUP BY purchase_year_month;

COMMENT ON VIEW {a}.delivery_monthly IS
'Grain: one purchase month. Delivery metrics over D-06 eligible orders (status delivered with a delivery date); durations averaged where defined (NULL when invalid per D-08); is_late at day level. Status conflicts (Q-01 / Q-02) are counted, not included in the metrics.';
