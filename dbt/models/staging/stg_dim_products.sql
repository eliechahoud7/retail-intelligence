-- stg_dim_products: thin staging view over source warehouse.dim_products.
-- Grain: one product; primary key product_id - unchanged from the source.
-- All 26 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    product_id,
    product_category_name,
    category_en_clean,
    category_reporting,
    product_name_length,
    product_description_length,
    product_photos_qty,
    product_weight_g,
    weight_g_valid,
    product_length_cm,
    product_height_cm,
    product_width_cm,
    dq_missing_category,
    dq_missing_name_length,
    dq_missing_description_length,
    dq_missing_photos_qty,
    dq_missing_weight_g,
    dq_missing_length_cm,
    dq_missing_height_cm,
    dq_missing_width_cm,
    dq_listing_metadata_missing,
    dq_physical_attributes_missing,
    dq_all_attributes_missing,
    dq_weight_zero,
    dq_density_suspicious,
    dq_dims_16x2x11
from {{ source('warehouse', 'dim_products') }}
