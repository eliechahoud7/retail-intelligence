"""Staging pipeline: ingest -> structural transforms -> derived structures -> validate -> write -> verify.

Outputs are written only if every blocking and regression check passes. The report is
written in every case, so a failed run is still documented.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.ingestion import DATASETS, DEFAULT_RAW_DIR, ingest
from src.ingestion.loader import PROJECT_ROOT

from .geolocation import build_geolocation_points, build_geolocation_rows
from .product_flags import build_product_metadata_flags
from .report import write_reports
from .schema import COLUMN_RENAMES
from .transform import StagedDataset, stage_dataset
from .validation import (BLOCKING, CheckResult, blocking_checks, failed, regression_checks,
                         warning_checks)
from .writer import roundtrip_differences, write_parquet

DEFAULT_STAGING_DIR = PROJECT_ROOT / "data" / "staging"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "staging"


@dataclass
class OutputRecord:
    """One staging output and its lineage."""

    name: str
    kind: str  # "staged" (1:1 typed copy of a raw file) or "derived" (built from staged data)
    frame: pd.DataFrame
    sources: list[str]
    source_files: list[str]
    source_sha256: dict[str, str]
    rows_in: int
    transformations: list[str]
    path: Path | None = None

    @property
    def rows_out(self) -> int:
        return len(self.frame)


@dataclass
class StagingResult:
    run_id: str
    started_at_utc: str
    git: dict
    raw_dir: Path
    out_dir: Path
    baseline_verified: bool
    outputs: list[OutputRecord]
    checks: list[CheckResult]
    written: bool = False
    report_paths: list[Path] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.written and not failed(self.checks)


def git_info(root: Path = PROJECT_ROOT) -> dict:
    """Current commit hash and whether the working tree has uncommitted changes (None if unavailable)."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=15, check=True).stdout.strip()
    try:
        return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def _build_outputs(ingestion):
    """Return (outputs, staged datasets, geolocation_rows, geolocation_points, product flags)."""
    by_file = {s.name: s.filename for s in DATASETS}
    sha = ingestion.checksums_before
    staged: dict[str, StagedDataset] = {s.name: stage_dataset(s.name, ingestion.frames[s.name]) for s in DATASETS}

    outputs = [OutputRecord(name=n, kind="staged", frame=st.frame, sources=[n], source_files=[by_file[n]],
                            source_sha256={by_file[n]: sha[n]}, rows_in=len(ingestion.frames[n]),
                            transformations=st.transformations)
               for n, st in staged.items()]

    geo_rows = build_geolocation_rows(staged["geolocation"].frame)
    geo_points = build_geolocation_points(geo_rows)
    flags = build_product_metadata_flags(staged["products"].frame)
    geo_src = {"sources": ["geolocation"], "source_files": [by_file["geolocation"]],
               "source_sha256": {by_file["geolocation"]: sha["geolocation"]}}
    outputs += [
        OutputRecord(name="geolocation_rows", kind="derived", frame=geo_rows, rows_in=len(staged["geolocation"].frame),
                     transformations=["one row per distinct full row (zip, lat, lng, city, state)",
                                      "occurrence_count = number of identical raw rows (lossless; strategy D-10)"],
                     **geo_src),
        OutputRecord(name="geolocation_points", kind="derived", frame=geo_points, rows_in=len(geo_rows),
                     transformations=["one row per distinct (zip, lat, lng), built from geolocation_rows",
                                      "raw_row_count = sum of occurrence_count; n_full_rows, n_city_spellings, n_states",
                                      "dq_outside_brazil: lat outside [-33.75, 5.27] or lng outside [-73.99, -34.79]",
                                      "no ZIP centroid computed (deferred)"],
                     **geo_src),
        OutputRecord(name="products_metadata_flags", kind="derived", frame=flags,
                     sources=["products"], source_files=[by_file["products"]],
                     source_sha256={by_file["products"]: sha["products"]}, rows_in=len(staged["products"].frame),
                     transformations=["8 field-level flags (value missing) and 3 row-level flags (all fields of the "
                                      "group missing), as defined in strategy section 4.3"]),
    ]
    return outputs, staged, geo_rows, geo_points, flags


def run_staging(raw_dir: Path = DEFAULT_RAW_DIR, out_dir: Path = DEFAULT_STAGING_DIR,
                report_dir: Path | None = DEFAULT_REPORT_DIR, verify_baseline: bool = True) -> StagingResult:
    """Run the staging layer end to end. Raises ``IngestionError`` if ingestion fails."""
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ingestion = ingest(raw_dir, verify_baseline=verify_baseline)

    outputs, staged, geo_rows, geo_points, flags = _build_outputs(ingestion)

    checks = [CheckResult("V01", BLOCKING, "raw files unchanged during ingestion (SHA-256 before = after)"
                          + ("; baseline verified" if verify_baseline else "; baseline check skipped"),
                          True, ingestion.checksums_before == ingestion.checksums_after,
                          ingestion.checksums_before == ingestion.checksums_after)]
    checks += blocking_checks(ingestion.frames, staged, geo_rows, geo_points, flags)
    if verify_baseline:  # baselines describe the approved raw files only
        checks += regression_checks(staged, geo_rows, geo_points, flags)
    checks += warning_checks(geo_points)

    result = StagingResult(run_id=run_id, started_at_utc=started, git=git_info(), raw_dir=Path(raw_dir),
                           out_dir=Path(out_dir), baseline_verified=verify_baseline, outputs=outputs, checks=checks)

    if not failed(checks):
        for rec in outputs:
            lineage = {"run_id": run_id, "generated_at_utc": started, "git": result.git, "layer": "staging",
                       "dataset": rec.name, "kind": rec.kind, "sources": rec.sources, "source_files": rec.source_files,
                       "source_sha256": rec.source_sha256, "rows_in": rec.rows_in, "rows_out": rec.rows_out,
                       "transformations": rec.transformations,
                       "column_name_map": dict(COLUMN_RENAMES.get(rec.name, {}))}
            rec.path = write_parquet(rec.frame, Path(out_dir) / f"{rec.name}.parquet", lineage)
        for rec in outputs:
            diff = roundtrip_differences(rec.frame, rec.path)
            result.checks.append(CheckResult(f"P01.{rec.name}", BLOCKING,
                                             f"{rec.name}: Parquet read-back identical (values and dtypes)",
                                             None, diff, diff is None))
        result.written = True

    if report_dir is not None:
        result.report_paths = write_reports(result, Path(report_dir))
    return result
