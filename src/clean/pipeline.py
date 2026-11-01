"""Clean pipeline: verify raw -> load staging -> build -> validate -> write -> verify -> manifest.

The clean layer reads only the staging Parquet outputs. The raw CSVs are checksummed
(never parsed) to prove they are unchanged and that staging was built from them.
Outputs and the manifest are written only if every blocking and regression check passes.
The report is written in every case.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.ingestion import DATASETS, DEFAULT_RAW_DIR, checksum_snapshot, sha256_file, validate_raw_files
from src.ingestion.integrity import verify_against_baseline
from src.ingestion.loader import PROJECT_ROOT
from src.staging import DEFAULT_STAGING_DIR, write_parquet
from src.staging.pipeline import git_info
from src.staging.validation import BLOCKING, CheckResult, failed, summarize
from src.staging.writer import roundtrip_differences

from . import reference as ref
from .inputs import load_staging
from .lineage import code_hash, content_hash, environment, rel
from .report import write_reports
from .schema import ADDED_COLUMNS, GRAIN, PARAMETERS, SOURCE_TABLES
from .transform import build_clean, transformations
from .validation import blocking_checks, lineage_checks, regression_checks, warning_checks

DEFAULT_CLEAN_DIR = PROJECT_ROOT / "data" / "processed" / "clean"
DEFAULT_MANIFEST_DIR = PROJECT_ROOT / "data" / "processed" / "_manifest"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "clean"

_GEO_REF = ["geolocation_points", "geolocation_rows"]
SOURCES = {  # output -> staging inputs it is built from (first = row source)
    "customers": ["customers", *_GEO_REF], "sellers": ["sellers", *_GEO_REF], "geolocation": ["geolocation"],
    "orders": ["orders", "customers"], "order_items": ["order_items", "orders"], "payments": ["payments"],
    "reviews": ["reviews", "orders"], "products": ["products", "products_metadata_flags", "translation"],
    "translation": ["translation"], "category_map": ["translation", "products"], "zip_lookup": _GEO_REF,
}

CATEGORY_MAP_INFO = {
    "version": ref.CATEGORY_MAP_VERSION, "change_notes": list(ref.CATEGORY_MAP_CHANGE_NOTES),
    "english_typo_corrections": {k: list(v) for k, v in ref.ENGLISH_TYPO_CORRECTIONS.items()},
    "project_translations": dict(ref.PROJECT_TRANSLATIONS), "twins_kept_separate": sorted(ref.TWIN_CATEGORIES),
    "uncategorised_label": ref.UNCATEGORISED_LABEL,
}


@dataclass
class OutputRecord:
    name: str
    kind: str  # "source" (a source table at its grain) or "reference"
    frame: pd.DataFrame
    sources: list[str]
    rows_in: int
    transformations: list[str]
    path: Path | None = None
    sha256: str | None = None
    content_sha256: str | None = None

    @property
    def rows_out(self) -> int:
        return len(self.frame)


@dataclass
class CleanResult:
    run_id: str
    started_at_utc: str
    git: dict
    code_sha256: str
    raw_dir: Path
    staging_dir: Path
    out_dir: Path
    baseline_verified: bool
    outputs: list[OutputRecord]
    checks: list[CheckResult]
    steps: list[dict]
    notes: list[str]
    input_registry: dict
    staging_run_id: str | None
    written: bool = False
    finished_at_utc: str | None = None
    manifest_path: Path | None = None
    report_paths: list[Path] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.written and not failed(self.checks)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _input_registry(raw_dir: Path, raw_sha: dict[str, str], inputs) -> dict:
    return {
        "raw": [{"dataset": s.name, "file": s.filename, "size_bytes": (Path(raw_dir) / s.filename).stat().st_size,
                 "sha256": raw_sha[s.name]} for s in DATASETS],
        "staging": [{"dataset": n, "path": rel(i.path), "sha256": i.sha256, "rows": len(i.frame),
                     "staging_run_id": i.lineage.get("run_id"), "staging_git_commit": (i.lineage.get("git") or {}).get("commit")}
                    for n, i in inputs.items()],
    }


def _lineage(result: CleanResult, rec: OutputRecord, inputs, raw_sha: dict[str, str]) -> dict:
    by_name = {s.name: s.filename for s in DATASETS}
    raw_used = sorted({f for src in rec.sources for f in inputs[src].lineage.get("source_sha256", {})})
    lineage = {
        "run_id": result.run_id, "generated_at_utc": result.started_at_utc, "git": result.git,
        "code_sha256": result.code_sha256, "layer": "clean", "dataset": rec.name, "kind": rec.kind,
        "grain": GRAIN[rec.name], "sources": rec.sources,
        "source_files": [rel(inputs[s].path) for s in rec.sources],
        "source_sha256": {rel(inputs[s].path): inputs[s].sha256 for s in rec.sources},
        "staging_run_id": result.staging_run_id,
        "raw_sha256": {f: raw_sha[n] for n, f in by_name.items() if f in raw_used},
        "rows_in": rec.rows_in, "rows_out": rec.rows_out, "transformations": rec.transformations,
        "added_columns": {c: {"dtype": s.dtype, "sources": list(s.sources), "rule": s.rule, "decision": s.decision}
                          for c, s in ADDED_COLUMNS.get(rec.name, ())},
        "parameters": dict(PARAMETERS),
    }
    if rec.name in ("category_map", "products"):
        lineage["category_map"] = CATEGORY_MAP_INFO
    return lineage


def _reference_transformations(name: str) -> list[str]:
    if name == "zip_lookup":
        return ["one row per zip prefix of geolocation_points (D-10.3)",
                "lat/lng_centroid = component-wise median over distinct points with dq_outside_brazil False, unweighted",
                "n_points_used, n_points_excluded_outside_brazil, raw_rows_represented (sum of raw_row_count over used points)",
                "n_distinct_states over the zip's geolocation_rows; dq_zip_multi_state = n_distinct_states > 1",
                "dq_zip_no_valid_point = no valid point (centroids missing)"]
    return [f"one row per Portuguese category; category_map version {ref.CATEGORY_MAP_VERSION}",
            "category_en_source = source translation (unchanged, missing when untranslated)",
            "category_en_clean = source translation with the 4 typo corrections; 2 project-added translations (provisional, Q-05)",
            "'_2' twins kept separate (Q-05)"]


def write_manifest(result: CleanResult, manifest_dir: Path) -> Path:
    manifest = {
        "run_id": result.run_id, "layer": "clean", "started_at_utc": result.started_at_utc,
        "finished_at_utc": result.finished_at_utc, "git": result.git, "code_sha256": result.code_sha256,
        "environment": environment(), "baseline_verified": result.baseline_verified,
        "parameters": dict(PARAMETERS), "category_map": CATEGORY_MAP_INFO,
        "input_registry": result.input_registry, "steps": result.steps,
        "outputs": [{"dataset": r.name, "path": rel(r.path), "layer": "clean", "kind": r.kind, "grain": GRAIN[r.name],
                     "rows": r.rows_out, "columns": r.frame.shape[1], "sha256": r.sha256, "content_sha256": r.content_sha256,
                     "schema": {c: str(t) for c, t in r.frame.dtypes.items()}} for r in result.outputs],
        "check_summary": summarize(result.checks), "success": result.success,
    }
    manifest_dir = Path(manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / f"{result.run_id}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def run_clean(raw_dir: Path = DEFAULT_RAW_DIR, staging_dir: Path = DEFAULT_STAGING_DIR,
              out_dir: Path = DEFAULT_CLEAN_DIR, manifest_dir: Path | None = DEFAULT_MANIFEST_DIR,
              report_dir: Path | None = DEFAULT_REPORT_DIR, verify_baseline: bool = True) -> CleanResult:
    """Run the clean layer end to end.

    Raises ``IngestionError`` (raw files missing / not the approved baseline),
    ``CleanInputError`` (staging outputs missing) or ``CategoryMapError`` (stale override).
    """
    run_id = uuid.uuid4().hex[:12]
    started = _now()
    validate_raw_files(raw_dir)
    raw_before = checksum_snapshot(raw_dir, DATASETS)
    if verify_baseline:
        verify_against_baseline(raw_before, DATASETS)

    inputs = load_staging(staging_dir)
    staged = {n: i.frame for n, i in inputs.items()}
    build = build_clean(staged)

    filenames = {s.name: s.filename for s in DATASETS}
    checks = lineage_checks(inputs, raw_before, filenames)
    checks += blocking_checks(staged, build.frames)
    if verify_baseline:  # baselines describe the approved raw files only
        checks += regression_checks(staged, build.frames)
    checks += warning_checks(build.frames, build.notes)
    raw_mid = checksum_snapshot(raw_dir, DATASETS)
    checks.insert(0, CheckResult("V01", BLOCKING, "raw files unchanged from run start to validation (SHA-256)"
                                 + ("; baseline verified" if verify_baseline else "; baseline check skipped"),
                                 True, raw_before == raw_mid, raw_before == raw_mid))

    run_ids = {i.lineage.get("run_id") for i in inputs.values()}
    outputs = [OutputRecord(name=n, kind="source" if n in SOURCE_TABLES else "reference", frame=build.frames[n],
                            sources=SOURCES[n], rows_in=len(staged[SOURCES[n][0]]),
                            transformations=transformations(n) if n in SOURCE_TABLES else _reference_transformations(n))
               for n in (*SOURCE_TABLES, "category_map", "zip_lookup")]
    result = CleanResult(run_id=run_id, started_at_utc=started, git=git_info(), code_sha256=code_hash(),
                         raw_dir=Path(raw_dir), staging_dir=Path(staging_dir), out_dir=Path(out_dir),
                         baseline_verified=verify_baseline, outputs=outputs, checks=checks, steps=build.steps,
                         notes=build.notes, input_registry=_input_registry(raw_dir, raw_before, inputs),
                         staging_run_id=next(iter(run_ids)) if len(run_ids) == 1 else None)

    if not failed(checks):
        for rec in outputs:
            rec.path = write_parquet(rec.frame, Path(out_dir) / f"{rec.name}.parquet",
                                     _lineage(result, rec, inputs, raw_before))
            rec.sha256 = sha256_file(rec.path)
            rec.content_sha256 = content_hash(rec.frame)
        for rec in outputs:
            diff = roundtrip_differences(rec.frame, rec.path)
            checks.append(CheckResult(f"P01.{rec.name}", BLOCKING, f"{rec.name}: Parquet read-back identical (values and dtypes)",
                                      None, diff, diff is None))
        raw_end = checksum_snapshot(raw_dir, DATASETS)
        checks.append(CheckResult("V01.end", BLOCKING, "raw files unchanged at the end of the run (SHA-256)",
                                  True, raw_before == raw_end, raw_before == raw_end))
        result.written = True
        result.finished_at_utc = _now()
        if manifest_dir is not None:
            result.manifest_path = write_manifest(result, Path(manifest_dir))
    else:
        result.finished_at_utc = _now()

    if report_dir is not None:
        result.report_paths = write_reports(result, Path(report_dir))
    return result
