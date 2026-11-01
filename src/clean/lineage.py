"""Lineage helpers for the clean layer (strategy D-14).

* ``content_hash``: SHA-256 of a DataFrame's column names, dtypes and values. Unlike the
  Parquet file hash, it does not depend on the run ID or timestamp stored in the file
  metadata, so it is equal for any two runs on the same inputs with the same code.
* ``code_hash``: SHA-256 over every ``src/**/*.py`` file (Q-13 default: identifies the code
  version even when the working tree has uncommitted changes).
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from src.ingestion.loader import PROJECT_ROOT


def content_hash(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([[str(c), str(t)] for c, t in df.dtypes.items()]).encode("utf-8"))
    h.update(pd.util.hash_pandas_object(df, index=False, categorize=False).to_numpy().tobytes())
    return h.hexdigest()


def code_hash(root: Path = PROJECT_ROOT / "src") -> str:
    h = hashlib.sha256()
    for path in sorted(Path(root).rglob("*.py")):
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def environment() -> dict:
    return {"python": platform.python_version(), "executable": sys.executable, "pandas": pd.__version__,
            "numpy": np.__version__, "pyarrow": pa.__version__}


def rel(path) -> str | None:
    """Project-relative POSIX path (absolute if outside the project)."""
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)
