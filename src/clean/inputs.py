"""Read the staging outputs that the clean layer consumes (never the raw CSVs)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.ingestion import sha256_file
from src.staging import read_lineage, read_parquet

from .schema import STAGING_INPUTS


class CleanInputError(Exception):
    """Staging outputs are missing or unreadable."""


@dataclass
class StagingInput:
    name: str
    path: Path
    frame: pd.DataFrame
    lineage: dict
    sha256: str  # of the Parquet file as read


def load_staging(staging_dir: Path) -> dict[str, StagingInput]:
    """Load all 12 staging outputs. Raises ``CleanInputError`` listing every missing file."""
    staging_dir = Path(staging_dir)
    missing = [f"{n}.parquet" for n in STAGING_INPUTS if not (staging_dir / f"{n}.parquet").is_file()]
    if missing:
        raise CleanInputError(f"{len(missing)} staging output(s) missing from {staging_dir}: {', '.join(missing)}. "
                              "Run `python -m src.staging` first.")
    inputs: dict[str, StagingInput] = {}
    for name in STAGING_INPUTS:
        path = staging_dir / f"{name}.parquet"
        try:
            inputs[name] = StagingInput(name=name, path=path, frame=read_parquet(path), lineage=read_lineage(path),
                                        sha256=sha256_file(path))
        except Exception as exc:  # pyarrow raises several unrelated types for corrupt files
            raise CleanInputError(f"{path}: could not be read ({type(exc).__name__}: {exc})") from exc
    return inputs
