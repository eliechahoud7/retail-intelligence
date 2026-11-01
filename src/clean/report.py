"""Clean-layer report: JSON (machine-readable) and Markdown (human-readable)."""

from __future__ import annotations

import json
from pathlib import Path

from src.staging.validation import failed, summarize

from .lineage import environment, rel
from .schema import ADDED_COLUMNS, GRAIN, PARAMETERS


def _flag_counts(rec) -> dict[str, int]:
    return {c: int(rec.frame[c].sum()) for c in rec.frame.columns
            if c.startswith(("dq_", "has_")) and rec.frame[c].dtype == bool}


def build_report(result) -> dict:
    return {
        "run_id": result.run_id,
        "layer": "clean",
        "executed_at_utc": result.started_at_utc,
        "finished_at_utc": result.finished_at_utc,
        "git": result.git,
        "code_sha256": result.code_sha256,
        "environment": {k: v for k, v in environment().items() if k != "executable"},
        "raw_dir": rel(result.raw_dir),
        "staging_dir": rel(result.staging_dir),
        "staging_run_id": result.staging_run_id,
        "output_dir": rel(result.out_dir),
        "manifest": rel(result.manifest_path),
        "baseline_verified": result.baseline_verified,
        "outputs_written": result.written,
        "success": result.success,
        "parameters": dict(PARAMETERS),
        "check_summary": summarize(result.checks),
        "outputs": [
            {
                "output_dataset": rec.name,
                "kind": rec.kind,
                "grain": GRAIN[rec.name],
                "path": rel(rec.path),
                "source_datasets": rec.sources,
                "rows_in": rec.rows_in,
                "rows_out": rec.rows_out,
                "columns": rec.frame.shape[1],
                "sha256": rec.sha256,
                "content_sha256": rec.content_sha256,
                "added_columns": [c for c, _ in ADDED_COLUMNS.get(rec.name, ())],
                "flag_counts": _flag_counts(rec),
                "schema": {c: str(t) for c, t in rec.frame.dtypes.items()},
                "missing_values": {c: int(n) for c, n in rec.frame.isna().sum().items()},
                "transformations": rec.transformations,
            }
            for rec in result.outputs
        ],
        "steps": result.steps,
        "notes": result.notes,
        "checks": [c.as_dict() for c in result.checks],
    }


def render_markdown(report: dict) -> str:
    s = report["check_summary"]
    lines = [
        "# Clean Layer Report",
        "",
        f"- **Run ID:** `{report['run_id']}`",
        f"- **Executed (UTC):** {report['executed_at_utc']} → {report['finished_at_utc']}",
        f"- **Git commit:** `{report['git'].get('commit')}`"
        + (" (working tree has uncommitted changes)" if report["git"].get("dirty") else ""),
        f"- **Code SHA-256 (src/):** `{report['code_sha256']}`",
        f"- **Inputs:** staging `{report['staging_dir']}` (staging run `{report['staging_run_id']}`); raw `{report['raw_dir']}` (checksums only)",
        f"- **Output directory:** `{report['output_dir']}` · **Manifest:** `{report['manifest']}`",
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
              "| Output | Kind | Grain | Rows in | Rows out | Columns | Added columns |",
              "|---|---|---|---:|---:|---:|---:|"]
    for o in report["outputs"]:
        lines.append(f"| `{o['output_dataset']}` | {o['kind']} | {o['grain']} | {o['rows_in']:,} | {o['rows_out']:,} | "
                     f"{o['columns']} | {len(o['added_columns'])} |")

    lines += ["", "## Flag counts", "", "| Output | Flag | Rows flagged |", "|---|---|---:|"]
    for o in report["outputs"]:
        lines += [f"| `{o['output_dataset']}` | `{f}` | {n:,} |" for f, n in o["flag_counts"].items()]

    for o in report["outputs"]:
        lines += ["", f"### `{o['output_dataset']}`", "",
                  f"Sources: {', '.join(f'`{x}`' for x in o['source_datasets'])} · Path: `{o['path']}`", "",
                  "Transformations:", ""] + [f"- {t}" for t in o["transformations"]] + [
                  "", "| Column | dtype | Missing |", "|---|---|---:|"]
        lines += [f"| `{c}` | {t} | {o['missing_values'][c]:,} |" for c, t in o["schema"].items()]

    if report["notes"]:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    lines += ["", "## All checks", "", "| ID | Class | Description | Expected | Observed | Result |", "|---|---|---|---|---|---|"]
    for c in report["checks"]:
        res = "PASS" if c["passed"] is True else "FAIL" if c["passed"] is False else "logged"
        lines.append(f"| `{c['check_id']}` | {c['check_class']} | {c['description']} | `{c['expected']}` | `{c['observed']}` | {res} |")
    return "\n".join(lines) + "\n"


def write_reports(result, report_dir: Path) -> list[Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(result)
    json_path = report_dir / "clean_report.json"
    md_path = report_dir / "clean_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return [json_path, md_path]


def console_summary(result) -> str:
    s = summarize(result.checks)
    lines = [
        "CLEAN RUN",
        f"  run id            : {result.run_id}",
        f"  git commit        : {result.git.get('commit')}{' (dirty)' if result.git.get('dirty') else ''}",
        f"  staging run       : {result.staging_run_id}",
        f"  baseline verified : {result.baseline_verified}",
        f"  outputs written   : {result.written}  ->  {rel(result.out_dir)}",
        f"  manifest          : {rel(result.manifest_path)}",
        "",
        f"  {'output':<14} {'kind':<10} {'rows in':>10} {'rows out':>10} {'cols':>5}",
    ]
    for rec in result.outputs:
        lines.append(f"  {rec.name:<14} {rec.kind:<10} {rec.rows_in:>10,} {rec.rows_out:>10,} {rec.frame.shape[1]:>5}")
    lines += ["", "  checks: " + ", ".join(f"{k} {v['passed']}/{v['total']} passed" if k != "warning"
                                          else f"{v['total']} warnings logged" for k, v in s.items())]
    for c in failed(result.checks):
        lines.append(f"  FAILED {c.check_id}: {c.description} (expected {c.expected}, observed {c.observed})")
    for c in result.checks:
        if c.check_class == "warning":
            lines.append(f"  warning {c.check_id}: {c.description} = {c.observed}")
    lines += [f"  report: {rel(p)}" for p in result.report_paths]
    lines.append(f"  RESULT: {'SUCCESS' if result.success else 'FAILED'}")
    return "\n".join(lines)
