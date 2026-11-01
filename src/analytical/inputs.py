"""Read the clean-layer outputs that the analytical layer consumes."""

from __future__ import annotations

from pathlib import Path

from src.clean.inputs import StagingInput as LayerInput  # generic (name, path, frame, lineage, sha256) record
from src.ingestion import sha256_file
from src.staging import read_lineage, read_parquet

from .schema import CLEAN_INPUTS


class AnalyticalInputError(Exception):
    """Clean outputs are missing or unreadable."""


def load_clean(clean_dir: Path) -> dict[str, LayerInput]:
    clean_dir = Path(clean_dir)
    missing = [f"{n}.parquet" for n in CLEAN_INPUTS if not (clean_dir / f"{n}.parquet").is_file()]
    if missing:
        raise AnalyticalInputError(f"{len(missing)} clean output(s) missing from {clean_dir}: {', '.join(missing)}. "
                                   "Run `python -m src.clean` first.")
    out: dict[str, LayerInput] = {}
    for name in CLEAN_INPUTS:
        path = clean_dir / f"{name}.parquet"
        try:
            out[name] = LayerInput(name=name, path=path, frame=read_parquet(path), lineage=read_lineage(path),
                                   sha256=sha256_file(path))
        except Exception as exc:  # pyarrow raises several unrelated types for corrupt files
            raise AnalyticalInputError(f"{path}: could not be read ({type(exc).__name__}: {exc})") from exc
    return out
