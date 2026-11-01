"""Analytical pipeline: verify raw -> load clean -> build -> validate -> write -> verify -> manifest.

Reads only the clean Parquet outputs (raw CSVs are checksummed, never parsed). Outputs and
the manifest are written only if every blocking and regression check passes; the report is
written in every case.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.clean.lineage import code_hash, environment, rel
from src.clean.lineage import content_hash as _frame_hash
from src.clean.pipeline import DEFAULT_CLEAN_DIR, DEFAULT_MANIFEST_DIR
from src.ingestion import DATASETS, DEFAULT_RAW_DIR, checksum_snapshot, sha256_file, validate_raw_files
from src.ingestion.integrity import verify_against_baseline
from src.ingestion.loader import PROJECT_ROOT
from src.staging import write_parquet
from src.staging.pipeline import git_info
from src.staging.validation import BLOCKING, CheckResult, failed, summarize
from src.staging.writer import roundtrip_differences

from .inputs import load_clean
from .report import write_reports
from .schema import DECISIONS, PARAMETERS, TABLES
from .transform import build_analytical
from .validation import blocking_checks, lineage_checks, regression_checks, warning_checks

DEFAULT_ANALYTICAL_DIR = PROJECT_ROOT / "data" / "processed" / "analytical"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "analytical"


@dataclass
class OutputRecord:
    name: str
    frame: pd.DataFrame
    path: Path | None = None
    sha256: str | None = None
    content_sha256: str | None = None


@dataclass
class AnalyticalResult:
    run_id: str
    started_at_utc: str
    git: dict
    code_sha256: str
    raw_dir: Path
    clean_dir: Path
    out_dir: Path
    baseline_verified: bool
    outputs: list[OutputRecord]
    checks: list[CheckResult]
    steps: list[dict]
    joins: list[dict]
    input_registry: dict
    clean_run_id: str | None
    written: bool = False
    finished_at_utc: str | None = None
    manifest_path: Path | None = None
    report_paths: list[Path] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.written and not failed(self.checks)


def content_hash(df: pd.DataFrame) -> str:
    """Clean-layer content hash, with list cells (``order_item_ids``) encoded as 'a|b|c' text first."""
    lists = [c for c in df.columns if df[c].dtype == object
             and df[c].map(lambda v: hasattr(v, "__len__") and not isinstance(v, str)).any()]
    if not lists:
        return _frame_hash(df)
    enc = df.copy()
    for c in lists:
        enc[c] = enc[c].map(lambda v: "|".join(map(str, v))).astype("string")
    return _frame_hash(enc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _spec_dict(name: str) -> dict:
    t = TABLES[name]
    return {"kind": t.kind, "purpose": t.purpose, "grain": t.grain, "primary_key": list(t.primary_key),
            "foreign_keys": [{"column": c, "references": f"{p}.{pc}"} for c, p, pc in t.foreign_keys],
            "sources": [f"clean.{s}" for s in t.sources]}


def _lineage(result: AnalyticalResult, rec: OutputRecord, inputs) -> dict:
    spec = TABLES[rec.name]
    return {"run_id": result.run_id, "generated_at_utc": result.started_at_utc, "git": result.git,
            "code_sha256": result.code_sha256, "layer": "analytical", "dataset": rec.name, **_spec_dict(rec.name),
            "source_files": [rel(inputs[s].path) for s in spec.sources],
            "source_sha256": {rel(inputs[s].path): inputs[s].sha256 for s in spec.sources},
            "clean_run_id": result.clean_run_id, "rows_out": len(rec.frame),
            "parameters": dict(PARAMETERS), "decisions": dict(DECISIONS)}


def write_manifest(result: AnalyticalResult, manifest_dir: Path) -> Path:
    manifest = {
        "run_id": result.run_id, "layer": "analytical", "started_at_utc": result.started_at_utc,
        "finished_at_utc": result.finished_at_utc, "git": result.git, "code_sha256": result.code_sha256,
        "environment": environment(), "baseline_verified": result.baseline_verified,
        "parameters": dict(PARAMETERS), "decisions": dict(DECISIONS), "input_registry": result.input_registry,
        "steps": result.steps, "joins": result.joins,
        "outputs": [{"dataset": r.name, "path": rel(r.path), "layer": "analytical", **_spec_dict(r.name),
                     "rows": len(r.frame), "columns": r.frame.shape[1], "sha256": r.sha256,
                     "content_sha256": r.content_sha256, "schema": {c: str(t) for c, t in r.frame.dtypes.items()}}
                    for r in result.outputs],
        "check_summary": summarize(result.checks), "success": result.success,
    }
    Path(manifest_dir).mkdir(parents=True, exist_ok=True)
    path = Path(manifest_dir) / f"{result.run_id}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def run_analytical(raw_dir: Path = DEFAULT_RAW_DIR, clean_dir: Path = DEFAULT_CLEAN_DIR,
                   out_dir: Path = DEFAULT_ANALYTICAL_DIR, manifest_dir: Path | None = DEFAULT_MANIFEST_DIR,
                   report_dir: Path | None = DEFAULT_REPORT_DIR, verify_baseline: bool = True) -> AnalyticalResult:
    """Run the analytical layer end to end.

    Raises ``IngestionError`` (raw files missing / not the baseline), ``AnalyticalInputError``
    (clean outputs missing) or ``pandas.errors.MergeError`` (a join cardinality breach).
    """
    run_id = uuid.uuid4().hex[:12]
    started = _now()
    validate_raw_files(raw_dir)
    raw_before = checksum_snapshot(raw_dir, DATASETS)
    if verify_baseline:
        verify_against_baseline(raw_before, DATASETS)

    inputs = load_clean(clean_dir)
    clean = {n: i.frame for n, i in inputs.items()}
    build = build_analytical(clean)

    filenames = {s.name: s.filename for s in DATASETS}
    checks = lineage_checks(inputs, raw_before, filenames)
    checks += blocking_checks(clean, build.frames, build.joins)
    if verify_baseline:
        checks += regression_checks(build.frames)
    checks += warning_checks(clean, build.frames)
    raw_mid = checksum_snapshot(raw_dir, DATASETS)
    checks.insert(0, CheckResult("V01", BLOCKING, "raw files unchanged from run start to validation (SHA-256)"
                                 + ("; baseline verified" if verify_baseline else "; baseline check skipped"),
                                 True, raw_before == raw_mid, raw_before == raw_mid))

    run_ids = {i.lineage.get("run_id") for i in inputs.values()}
    registry = {
        "raw": [{"dataset": s.name, "file": s.filename, "sha256": raw_before[s.name]} for s in DATASETS],
        "clean": [{"dataset": n, "path": rel(i.path), "sha256": i.sha256, "rows": len(i.frame),
                   "clean_run_id": i.lineage.get("run_id"), "clean_git_commit": (i.lineage.get("git") or {}).get("commit")}
                  for n, i in inputs.items()],
    }
    outputs = [OutputRecord(name=n, frame=build.frames[n]) for n in TABLES]
    result = AnalyticalResult(run_id=run_id, started_at_utc=started, git=git_info(), code_sha256=code_hash(),
                              raw_dir=Path(raw_dir), clean_dir=Path(clean_dir), out_dir=Path(out_dir),
                              baseline_verified=verify_baseline, outputs=outputs, checks=checks, steps=build.steps,
                              joins=build.joins.records, input_registry=registry,
                              clean_run_id=next(iter(run_ids)) if len(run_ids) == 1 else None)

    if not failed(checks):
        for rec in outputs:
            rec.path = write_parquet(rec.frame, Path(out_dir) / f"{rec.name}.parquet", _lineage(result, rec, inputs))
            rec.sha256 = sha256_file(rec.path)
            rec.content_sha256 = content_hash(rec.frame)
        for rec in outputs:
            diff = roundtrip_differences(rec.frame, rec.path)
            checks.append(CheckResult(f"P01.{rec.name}", BLOCKING, f"{rec.name}: Parquet read-back identical (values and dtypes)",
                                      None, diff, diff is None))
        clean_after = {n: sha256_file(i.path) for n, i in inputs.items()}
        unchanged = clean_after == {n: i.sha256 for n, i in inputs.items()}
        checks.append(CheckResult("C03", BLOCKING, "clean input files unchanged during the run (SHA-256)", True, unchanged, unchanged))
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
