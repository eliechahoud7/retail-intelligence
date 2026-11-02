-- stg_dim_dates: thin staging view over source warehouse.dim_dates.
-- Grain: one calendar day; primary key date - unchanged from the source.
-- All 11 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    date,
    year,
    quarter,
    month,
    year_month,
    iso_year,
    iso_week,
    day_of_week,
    day_name,
    is_weekend,
    in_candidate_window
from {{ source('warehouse', 'dim_dates') }}
