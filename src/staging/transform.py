"""Structural transformations of the nine ingested datasets (no row or value changes).

Each function returns a *new* DataFrame; ingested frames are never mutated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .schema import CATEGORICAL_COLUMNS, COLUMN_RENAMES, DATE_COLUMNS, DATE_FORMAT


@dataclass
class StagedDataset:
    """One staged table plus a description of what was done to it."""

    name: str
    frame: pd.DataFrame
    transformations: list[str] = field(default_factory=list)
    coerced_dates: dict[str, int] = field(default_factory=dict)  # values that failed to parse


def parse_datetime(series: pd.Series, fmt: str = DATE_FORMAT) -> tuple[pd.Series, int]:
    """Parse text timestamps with one explicit format (timezone-naive).

    Returns the parsed Series and the number of non-missing values that could not be
    parsed (they become NaT via ``errors="coerce"`` and must be reported, never ignored).
    """
    parsed = pd.to_datetime(series, format=fmt, errors="coerce")
    coerced = int((series.notna() & parsed.isna()).sum())
    return parsed, coerced


def to_category(series: pd.Series) -> pd.Series:
    """Convert to an unordered categorical whose categories are the sorted observed values.

    Categories use pandas' default ``str`` dtype: that is the form Parquet reads back, so
    the in-memory and on-disk representations are identical (a ``string``-typed category
    index would come back as ``str``). Values and missing entries are unchanged.
    """
    categories = pd.Index(sorted(series.dropna().unique()), dtype="str")
    return series.astype(pd.CategoricalDtype(categories=categories, ordered=False))


def stage_dataset(name: str, ingested: pd.DataFrame) -> StagedDataset:
    """Apply the approved structural transformations for one dataset."""
    df = ingested.copy()
    steps: list[str] = []
    coerced: dict[str, int] = {}

    for col in DATE_COLUMNS.get(name, ()):
        df[col], coerced[col] = parse_datetime(df[col])
        steps.append(f"{col}: text -> {df[col].dtype} (format '{DATE_FORMAT}', timezone-naive)")

    for col in CATEGORICAL_COLUMNS.get(name, ()):
        df[col] = to_category(df[col])
        steps.append(f"{col}: string -> category ({len(df[col].cat.categories)} observed values)")

    renames = dict(COLUMN_RENAMES.get(name, {}))
    if renames:
        df = df.rename(columns=renames)
        steps += [f"renamed {old} -> {new} (strategy D-05)" for old, new in renames.items()]

    if not steps:
        steps.append("no structural change (types already final from ingestion)")
    return StagedDataset(name=name, frame=df, transformations=steps, coerced_dates=coerced)


def staged_column_name(dataset: str, raw_column: str) -> str:
    """Map a raw column name to its staged name."""
    return COLUMN_RENAMES.get(dataset, {}).get(raw_column, raw_column)
