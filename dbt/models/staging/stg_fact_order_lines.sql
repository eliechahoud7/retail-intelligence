-- stg_fact_order_lines: thin staging view over source warehouse.fact_order_lines.
-- Grain: one order line (D-15 six-attribute grain); primary key order_line_id - unchanged from the source.
-- All 18 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    order_line_id,
    order_id,
    first_order_item_id,
    product_id,
    seller_id,
    shipping_limit_date,
    quantity,
    unit_price,
    freight_value_per_row,
    line_price_total,
    freight_total,
    order_item_ids,
    dq_shipping_limit_out_of_range,
    customer_unique_id,
    order_status,
    order_purchase_timestamp,
    purchase_date,
    in_candidate_window
from {{ source('warehouse', 'fact_order_lines') }}
