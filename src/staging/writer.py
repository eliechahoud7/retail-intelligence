"""Parquet persistence with embedded lineage metadata and round-trip verification."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

LINEAGE_KEY = b"retail_intelligence.lineage"


def write_parquet(df: pd.DataFrame, path: Path, lineage: dict) -> Path:
    """Write ``df`` atomically (temp file + rename) with lineage stored in the Parquet schema metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata[LINEAGE_KEY] = json.dumps(lineage, sort_keys=True).encode("utf-8")
    table = table.replace_schema_metadata(metadata)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)
    return path


def read_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def read_lineage(path: Path) -> dict:
    metadata = pq.read_schema(path).metadata or {}
    return json.loads(metadata[LINEAGE_KEY].decode("utf-8")) if LINEAGE_KEY in metadata else {}


def roundtrip_differences(df: pd.DataFrame, path: Path) -> str | None:
    """Return None if the Parquet file reads back identical to ``df`` (values and dtypes), else a message."""
    try:
        pd.testing.assert_frame_equal(read_parquet(path), df, check_dtype=True, check_exact=True)
    except AssertionError as exc:
        return str(exc).splitlines()[0][:300]
    return None
