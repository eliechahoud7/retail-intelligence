"""Staging report: JSON (machine-readable) and Markdown (human-readable)."""

from __future__ import annotations

import json
import platform
from pathlib import Path

import pandas as pd

from src.ingestion.loader import PROJECT_ROOT

from .validation import failed, summarize


def _rel(path) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def build_report(result) -> dict:
    return {
        "run_id": result.run_id,
        "executed_at_utc": result.started_at_utc,
        "git": result.git,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "raw_dir": _rel(result.raw_dir),
        "output_dir": _rel(result.out_dir),
        "baseline_verified": result.baseline_verified,
        "outputs_written": result.written,
        "success": result.success,
        "check_summary": summarize(result.checks),
        "outputs": [
            {
                "output_dataset": rec.name,
                "kind": rec.kind,
                "path": _rel(rec.path),
                "source_datasets": rec.sources,
                "source_files": rec.source_files,
                "source_sha256": rec.source_sha256,
                "rows_in": rec.rows_in,
                "rows_out": rec.rows_out,
                "columns": rec.frame.shape[1],
                "schema": {c: str(t) for c, t in rec.frame.dtypes.items()},
                "missing_values": {c: int(n) for c, n in rec.frame.isna().sum().items()},
                "transformations": rec.transformations,
            }
            for rec in result.outputs
        ],
        "checks": [c.as_dict() for c in result.checks],
    }


def render_markdown(report: dict) -> str:
    s = report["check_summary"]
    lines = [
        "# Staging Report",
        "",
        f"- **Run ID:** `{report['run_id']}`",
        f"- **Executed (UTC):** {report['executed_at_utc']}",
        f"- **Git commit:** `{report['git'].get('commit')}`"
        + (" (working tree has uncommitted changes)" if report["git"].get("dirty") else ""),
        f"- **Raw directory:** `{report['raw_dir']}` · **Output directory:** `{report['output_dir']}`",
        f"- **Baseline checksums verified:** {report['baseline_verified']}",
        f"- **Outputs written:** {report['outputs_written']} · **Success:** {report['success']}",
        "",
        "## Validation summary",
        "",
        "| Class | Total | Passed | Failed |",
        "|---|---:|---:|---:|",
    ]
    for cls in ("blocking", "regression", "warning"):
        if cls in s:
            lines.append(f"| {cls} | {s[cls]['total']} | {s[cls]['passed']} | {s[cls]['failed']} |")
    fails = [c for c in report["checks"] if c["passed"] is False]
    lines += ["", "**Failed checks:** " + ("none" if not fails else "")]
    lines += [f"- `{c['check_id']}` {c['description']}: expected `{c['expected']}`, observed `{c['observed']}`" for c in fails]
    lines += ["", "**Warnings (logged values):**", ""]
    lines += [f"- `{c['check_id']}` {c['description']}: **{c['observed']}**" for c in report["checks"] if c["check_class"] == "warning"]

    lines += ["", "## Outputs", "",
              "| Output | Kind | Source | Rows in | Rows out | Columns | Missing cells |",
              "|---|---|---|---:|---:|---:|---:|"]
    for o in report["outputs"]:
        lines.append(f"| `{o['output_dataset']}` | {o['kind']} | {', '.join(o['source_datasets'])} | {o['rows_in']:,} | "
                     f"{o['rows_out']:,} | {o['columns']} | {sum(o['missing_values'].values()):,} |")
    for o in report["outputs"]:
        lines += ["", f"### `{o['output_dataset']}`", "",
                  f"Source file(s): {', '.join(f'`{f}`' for f in o['source_files'])} · Path: `{o['path']}`", "",
                  "Transformations:", ""] + [f"- {t}" for t in o["transformations"]] + [
                  "", "| Column | dtype | Missing |", "|---|---|---:|"]
        lines += [f"| `{c}` | {t} | {o['missing_values'][c]:,} |" for c, t in o["schema"].items()]
    lines += ["", "## All checks", "", "| ID | Class | Description | Expected | Observed | Result |", "|---|---|---|---|---|---|"]
    for c in report["checks"]:
        res = "PASS" if c["passed"] is True else "FAIL" if c["passed"] is False else "logged"
        lines.append(f"| `{c['check_id']}` | {c['check_class']} | {c['description']} | `{c['expected']}` | `{c['observed']}` | {res} |")
    return "\n".join(lines) + "\n"


def write_reports(result, report_dir: Path) -> list[Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(result)
    json_path = report_dir / "staging_report.json"
    md_path = report_dir / "staging_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return [json_path, md_path]


def console_summary(result) -> str:
    s = summarize(result.checks)
    lines = [
        "STAGING RUN",
        f"  run id            : {result.run_id}",
        f"  git commit        : {result.git.get('commit')}{' (dirty)' if result.git.get('dirty') else ''}",
        f"  baseline verified : {result.baseline_verified}",
        f"  outputs written   : {result.written}  ->  {_rel(result.out_dir)}",
        "",
        f"  {'output':<26} {'kind':<8} {'rows in':>10} {'rows out':>10} {'cols':>5}",
    ]
    for rec in result.outputs:
        lines.append(f"  {rec.name:<26} {rec.kind:<8} {rec.rows_in:>10,} {rec.rows_out:>10,} {rec.frame.shape[1]:>5}")
    lines += ["", "  checks: " + ", ".join(f"{k} {v['passed']}/{v['total']} passed" if k != "warning"
                                          else f"{v['total']} warnings logged" for k, v in s.items())]
    for c in failed(result.checks):
        lines.append(f"  FAILED {c.check_id}: {c.description} (expected {c.expected}, observed {c.observed})")
    for c in result.checks:
        if c.check_class == "warning":
            lines.append(f"  warning {c.check_id}: {c.description} = {c.observed}")
    lines += [f"  report: {_rel(p)}" for p in result.report_paths]
    lines.append(f"  RESULT: {'SUCCESS' if result.success else 'FAILED'}")
    return "\n".join(lines)
