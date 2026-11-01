"""Staging layer (Task 4.2).

Converts the nine ingested raw datasets into typed Parquet files (same rows, same values),
plus three derived structures approved in the preparation strategy:
``geolocation_rows``, ``geolocation_points`` and ``products_metadata_flags``.

Typical use::

    from src.staging import run_staging
    result = run_staging()        # writes data/staging/*.parquet and reports/staging/*
"""

from .geolocation import build_geolocation_points, build_geolocation_rows
from .pipeline import DEFAULT_REPORT_DIR, DEFAULT_STAGING_DIR, StagingResult, run_staging
from .product_flags import build_product_metadata_flags, group_equivalence
from .transform import parse_datetime, stage_dataset
from .writer import read_lineage, read_parquet, write_parquet

__all__ = [
    "run_staging", "StagingResult", "DEFAULT_STAGING_DIR", "DEFAULT_REPORT_DIR",
    "stage_dataset", "parse_datetime",
    "build_geolocation_rows", "build_geolocation_points",
    "build_product_metadata_flags", "group_equivalence",
    "write_parquet", "read_parquet", "read_lineage",
]
