-- analytics.sales_daily
-- Grain: one calendar day, from the first to the last purchase date (days without orders = 0).
-- Each fact is aggregated to the day on its own (orders, order lines, payments) and the three
-- one-row-per-day results are joined to dim_dates, so no fact rows are ever multiplied.
-- Item-based (items_*) and payment-based (payment_*) totals stay separate (Q-03).
-- *_delivered columns: orders with order_status = 'delivered' (Task 4.4 eligibility variants).
CREATE OR REPLACE VIEW {a}.sales_daily AS
WITH bounds AS (
    SELECT min(purchase_date) AS first_date, max(purchase_date) AS last_date FROM {w}.fact_orders
), orders AS (
    SELECT purchase_date,
           count(*)                                                AS n_orders,
           count(*) FILTER (WHERE order_status = 'delivered')      AS n_orders_delivered,
           count(*) FILTER (WHERE order_status = 'canceled')       AS n_orders_canceled
    FROM {w}.fact_orders GROUP BY purchase_date
), lines AS (
    SELECT purchase_date,
           count(DISTINCT order_id)                                             AS n_orders_with_items,
           sum(quantity)                                                        AS units,
           sum(line_price_total)                                                AS items_price_total,
           sum(freight_total)                                                   AS items_freight_total,
           sum(quantity)         FILTER (WHERE order_status = 'delivered')      AS units_delivered,
           sum(line_price_total) FILTER (WHERE order_status = 'delivered')      AS items_price_total_delivered,
           sum(freight_total)    FILTER (WHERE order_status = 'delivered')      AS items_freight_total_delivered
    FROM {w}.fact_order_lines GROUP BY purchase_date
), payments AS (
    SELECT purchase_date,
           count(*)                                                        AS n_payments,
           sum(payment_value)                                              AS payment_total,
           sum(payment_value) FILTER (WHERE order_status = 'delivered')    AS payment_total_delivered
    FROM {w}.fact_payments GROUP BY purchase_date
)
SELECT d.date                                         AS purchase_date,
       d.year, d.month, d.year_month, d.iso_year, d.iso_week, d.day_of_week, d.is_weekend,
       d.in_candidate_window,
       coalesce(o.n_orders, 0)                        AS n_orders,
       coalesce(o.n_orders_delivered, 0)              AS n_orders_delivered,
       coalesce(o.n_orders_canceled, 0)               AS n_orders_canceled,
       coalesce(l.n_orders_with_items, 0)             AS n_orders_with_items,
       coalesce(l.units, 0)                           AS units,
       coalesce(l.items_price_total, 0)               AS items_price_total,
       coalesce(l.items_freight_total, 0)             AS items_freight_total,
       coalesce(l.units_delivered, 0)                 AS units_delivered,
       coalesce(l.items_price_total_delivered, 0)     AS items_price_total_delivered,
       coalesce(l.items_freight_total_delivered, 0)   AS items_freight_total_delivered,
       coalesce(p.n_payments, 0)                      AS n_payments,
       coalesce(p.payment_total, 0)                   AS payment_total,
       coalesce(p.payment_total_delivered, 0)         AS payment_total_delivered
FROM {w}.dim_dates d
CROSS JOIN bounds b
LEFT JOIN orders   o ON o.purchase_date = d.date
LEFT JOIN lines    l ON l.purchase_date = d.date
LEFT JOIN payments p ON p.purchase_date = d.date
WHERE d.date BETWEEN b.first_date AND b.last_date;

COMMENT ON VIEW {a}.sales_daily IS
'Grain: one calendar day between the first and last purchase date (no-order days = 0). Orders, order lines and payments are aggregated per day independently, then joined 1:1 - no fan-out. items_* = item-based (order lines), payment_* = payment-based (payments); never combine them into one revenue (Q-03). *_delivered = order_status delivered. Filter on in_candidate_window for trend analysis (Q-11).';
