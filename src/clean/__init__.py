"""Clean layer (Task 4.3).

Consumes the staging Parquet outputs and writes, to ``data/processed/clean/``:

* the nine source tables at their source grain: the staged rows and values unchanged,
  plus standardised copies (``city_key``, ``*_state_std``, ``geolocation_city_std``) and
  ``dq_*`` data-quality flags;
* two reference tables: ``category_map`` (D-12) and ``zip_lookup`` (D-10).

Typical use::

    from src.clean import run_clean
    result = run_clean()      # writes data/processed/clean/*.parquet, a run manifest and reports/clean/*
"""

from .inputs import CleanInputError, load_staging
from .lineage import content_hash
from .pipeline import (DEFAULT_CLEAN_DIR, DEFAULT_MANIFEST_DIR, DEFAULT_REPORT_DIR, CleanResult, run_clean)
from .reference import CategoryMapError, build_category_map, build_zip_lookup
from .transform import build_clean

__all__ = [
    "run_clean", "CleanResult", "DEFAULT_CLEAN_DIR", "DEFAULT_MANIFEST_DIR", "DEFAULT_REPORT_DIR",
    "build_clean", "build_category_map", "build_zip_lookup", "load_staging", "content_hash",
    "CleanInputError", "CategoryMapError",
]
