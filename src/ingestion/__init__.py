"""Raw data ingestion layer (Task 4.1).

Loads the nine raw Olist CSV files read-only, with an explicit schema, and reports their
structure. No cleaning or transformation is performed here.

Typical use::

    from src.ingestion import ingest, profile_result
    result = ingest()                    # loads data/raw/, verifies checksums
    orders = result.frames["orders"]
"""

from .errors import (DatasetLoadError, IngestionError, MissingRawFilesError,
                     RawDataIntegrityError, SchemaMismatchError)
from .integrity import checksum_snapshot, sha256_file
from .loader import DEFAULT_RAW_DIR, IngestionResult, ingest, load_dataset, validate_raw_files
from .profiling import DatasetProfile, profile_dataframe, profile_result
from .schema import DATASETS, DATASETS_BY_NAME, ZIP_COLUMNS, DatasetSpec

__all__ = [
    "DATASETS", "DATASETS_BY_NAME", "ZIP_COLUMNS", "DatasetSpec",
    "ingest", "load_dataset", "validate_raw_files", "IngestionResult", "DEFAULT_RAW_DIR",
    "profile_dataframe", "profile_result", "DatasetProfile",
    "sha256_file", "checksum_snapshot",
    "IngestionError", "MissingRawFilesError", "SchemaMismatchError", "DatasetLoadError",
    "RawDataIntegrityError",
]
