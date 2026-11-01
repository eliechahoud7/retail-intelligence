"""Structural ingestion report: rows, columns, column names, dtypes and missing values."""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .loader import IngestionResult, PROJECT_ROOT


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    missing: int
    missing_pct: float


@dataclass(frozen=True)
class DatasetProfile:
    name: str
    filename: str
    rows: int
    columns: int
    sha256: str
    column_profiles: tuple[ColumnProfile, ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.column_profiles]

    @property
    def total_missing(self) -> int:
        return sum(c.missing for c in self.column_profiles)


def profile_dataframe(name: str, filename: str, df: pd.DataFrame, sha256: str) -> DatasetProfile:
    """Describe one loaded DataFrame (read-only)."""
    n = len(df)
    missing = df.isna().sum()
    cols = tuple(
        ColumnProfile(name=c, dtype=str(df[c].dtype), missing=int(missing[c]),
                      missing_pct=round(missing[c] / n * 100, 3) if n else 0.0)
        for c in df.columns
    )
    return DatasetProfile(name=name, filename=filename, rows=n, columns=df.shape[1],
                          sha256=sha256, column_profiles=cols)


def profile_result(result: IngestionResult) -> list[DatasetProfile]:
    return [profile_dataframe(s.name, s.filename, result.frames[s.name], result.checksums_before[s.name])
            for s in result.specs]


def format_text_report(result: IngestionResult, profiles: list[DatasetProfile]) -> str:
    """Human-readable report printed by the CLI."""
    lines = [
        "RAW DATA INGESTION REPORT",
        f"Raw directory : {result.raw_dir}",
        f"Datasets      : {len(profiles)} loaded",
        f"Baseline SHA-256 verified before load : {'yes' if result.baseline_verified else 'skipped'}",
        f"Raw files unchanged after load        : "
        f"{'yes' if result.checksums_before == result.checksums_after else 'NO'}",
        "",
    ]
    for p in profiles:
        lines += [
            "=" * 78,
            f"{p.name}  ({p.filename})",
            f"  rows: {p.rows:,}   columns: {p.columns}   missing cells: {p.total_missing:,}",
            f"  sha256: {p.sha256}",
            f"  {'column':<32} {'dtype':<10} {'missing':>9} {'missing %':>10}",
        ]
        for c in p.column_profiles:
            lines.append(f"  {c.name:<32} {c.dtype:<10} {c.missing:>9,} {c.missing_pct:>9.3f}%")
    lines += ["=" * 78,
              f"TOTAL rows: {sum(p.rows for p in profiles):,}   "
              f"columns: {sum(p.columns for p in profiles)}   "
              f"missing cells: {sum(p.total_missing for p in profiles):,}"]
    return "\n".join(lines)


def build_json_report(result: IngestionResult, profiles: list[DatasetProfile]) -> dict:
    """Machine-readable report (lineage input registry)."""
    def rel(path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(PROJECT_ROOT))
        except ValueError:
            return str(path)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "raw_dir": rel(result.raw_dir),
        "baseline_verified": result.baseline_verified,
        "raw_unchanged_after_load": result.checksums_before == result.checksums_after,
        "datasets": [
            {**{k: v for k, v in asdict(p).items() if k != "column_profiles"},
             "columns_detail": [asdict(c) for c in p.column_profiles]}
            for p in profiles
        ],
    }


def write_json_report(report: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
