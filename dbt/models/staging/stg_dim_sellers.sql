-- stg_dim_sellers: thin staging view over source warehouse.dim_sellers.
-- Grain: one seller; primary key seller_id - unchanged from the source.
-- All 9 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    seller_id,
    seller_zip_code_prefix,
    seller_city,
    city_key,
    seller_state_std,
    dq_city_malformed,
    lat_centroid,
    lng_centroid,
    dq_no_geolocation
from {{ source('warehouse', 'dim_sellers') }}
