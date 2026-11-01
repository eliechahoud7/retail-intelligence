"""Load the raw Olist CSV files into pandas DataFrames, read-only and without transformation.

What ingestion does:
  1. checks that every expected file exists (all are checked before anything is read);
  2. verifies raw checksums against the approved baseline (optional, on by default);
  3. checks each file's header against the declared schema;
  4. reads each file with an explicit per-column dtype (``schema.py``);
  5. re-checks the checksums afterwards to prove nothing was modified.

What it does not do: no renaming, date parsing, de-duplication, filling, filtering or
any other cleaning. Those belong to later layers (see the data preparation strategy).

Missing values: only *empty fields* are treated as missing (``keep_default_na=False``,
``na_values=[""]``). Literal text such as "NA", "null" or "None" is kept as text, so no
source value is silently converted to missing.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

from .errors import DatasetLoadError, IngestionError, MissingRawFilesError, SchemaMismatchError
from .integrity import checksum_snapshot, verify_against_baseline, verify_unchanged
from .schema import DATASETS, DatasetSpec

# "utf-8-sig" decodes UTF-8 and strips a leading byte-order mark if present (identical to
# "utf-8" otherwise). product_category_name_translation.csv starts with a BOM, which would
# otherwise become part of its first column name ("﻿product_category_name").
ENCODING = "utf-8-sig"
NA_VALUES = [""]  # only empty fields are missing

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"


@dataclass
class IngestionResult:
    """Everything produced by one ingestion run (in memory only; nothing is written)."""

    raw_dir: Path
    frames: dict[str, pd.DataFrame]
    checksums_before: dict[str, str]
    checksums_after: dict[str, str]
    baseline_verified: bool
    specs: tuple[DatasetSpec, ...] = field(default=DATASETS)


def validate_raw_files(raw_dir: Path, specs: Iterable[DatasetSpec] = DATASETS) -> None:
    """Raise ``MissingRawFilesError`` listing *every* missing file (not just the first)."""
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise MissingRawFilesError(raw_dir, [s.filename for s in specs])
    missing = [s.filename for s in specs if not (raw_dir / s.filename).is_file()]
    if missing:
        raise MissingRawFilesError(raw_dir, missing)


def read_header(path: Path) -> list[str]:
    """Return the column names in the first line of a CSV file."""
    with Path(path).open("r", encoding=ENCODING, newline="") as fh:
        try:
            return next(csv.reader(fh))
        except StopIteration:
            return []


def check_header(spec: DatasetSpec, path: Path) -> None:
    """Raise ``SchemaMismatchError`` unless the header equals the declared columns, in order."""
    actual = read_header(path)
    expected = list(spec.column_names)
    if actual != expected:
        missing = [c for c in expected if c not in actual]
        extra = [c for c in actual if c not in expected]
        detail = f"missing={missing}, unexpected={extra}" if (missing or extra) else "column order differs"
        raise SchemaMismatchError(f"{spec.filename}: header does not match schema ({detail}). "
                                  f"Expected {expected}, found {actual}")


def load_dataset(spec: DatasetSpec, raw_dir: Path) -> pd.DataFrame:
    """Load one raw file with its declared dtypes. Raises a subclass of ``IngestionError``."""
    path = Path(raw_dir) / spec.filename
    if not path.is_file():
        raise MissingRawFilesError(raw_dir, [spec.filename])
    try:
        check_header(spec, path)
        return pd.read_csv(
            path,
            dtype=dict(spec.columns),
            keep_default_na=False,
            na_values=NA_VALUES,
            encoding=ENCODING,
        )
    except IngestionError:
        raise
    except (ValueError, TypeError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DatasetLoadError(f"{spec.filename}: could not be loaded ({type(exc).__name__}: {exc})") from exc


def ingest(raw_dir: Path = DEFAULT_RAW_DIR, specs: tuple[DatasetSpec, ...] = DATASETS,
           verify_baseline: bool = True) -> IngestionResult:
    """Run the full ingestion: existence check, checksums, load all datasets, re-verify checksums.

    Every dataset is attempted; if any fail, a single ``DatasetLoadError`` lists all failures.
    """
    raw_dir = Path(raw_dir)
    validate_raw_files(raw_dir, specs)

    before = checksum_snapshot(raw_dir, specs)
    if verify_baseline:
        verify_against_baseline(before, specs)

    frames: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    for spec in specs:
        try:
            frames[spec.name] = load_dataset(spec, raw_dir)
        except IngestionError as exc:
            failures.append(str(exc))

    after = checksum_snapshot(raw_dir, specs)
    verify_unchanged(before, after)

    if failures:
        raise DatasetLoadError(f"{len(failures)} dataset(s) failed to load:\n  " + "\n  ".join(failures))

    return IngestionResult(raw_dir=raw_dir, frames=frames, checksums_before=before,
                           checksums_after=after, baseline_verified=verify_baseline, specs=specs)
