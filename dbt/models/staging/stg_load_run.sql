-- stg_load_run: thin staging view over source warehouse.load_run.
-- Grain: one committed warehouse load; primary key load_id - unchanged from the source.
-- All 8 source columns, explicitly listed in source order, same names and types; no rows
-- filtered, no values changed (see models/staging/_staging__models.yml).
select
    load_id,
    gold_run_id,
    gold_manifest_path,
    gold_manifest_sha256,
    row_counts,
    content_hashes,
    started_at,
    finished_at
from {{ source('warehouse', 'load_run') }}
