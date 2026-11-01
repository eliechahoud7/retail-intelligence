"""Analytical-layer report: JSON (machine-readable) and Markdown (human-readable)."""

from __future__ import annotations

import json
from pathlib import Path

from src.clean.lineage import environment, rel
from src.staging.validation import failed, summarize

from .schema import DECISIONS, PARAMETERS, TABLES


def build_report(result) -> dict:
    return {
        "run_id": result.run_id, "layer": "analytical",
        "executed_at_utc": result.started_at_utc, "finished_at_utc": result.finished_at_utc,
        "git": result.git, "code_sha256": result.code_sha256,
        "environment": {k: v for k, v in environment().items() if k != "executable"},
        "raw_dir": rel(result.raw_dir), "clean_dir": rel(result.clean_dir), "clean_run_id": result.clean_run_id,
        "output_dir": rel(result.out_dir), "manifest": rel(result.manifest_path),
        "baseline_verified": result.baseline_verified, "outputs_written": result.written, "success": result.success,
        "parameters": dict(PARAMETERS), "decisions": dict(DECISIONS),
        "check_summary": summarize(result.checks),
        "outputs": [{
            "output_dataset": r.name, "kind": TABLES[r.name].kind, "purpose": TABLES[r.name].purpose,
            "grain": TABLES[r.name].grain, "primary_key": list(TABLES[r.name].primary_key),
            "foreign_keys": [f"{c} -> {p}.{pc}" for c, p, pc in TABLES[r.name].foreign_keys],
            "sources": [f"clean.{s}" for s in TABLES[r.name].sources],
            "path": rel(r.path), "rows": len(r.frame), "columns": r.frame.shape[1],
            "sha256": r.sha256, "content_sha256": r.content_sha256,
            "schema": {c: str(t) for c, t in r.frame.dtypes.items()},
            "missing_values": {c: int(n) for c, n in r.frame.isna().sum().items()},
        } for r in result.outputs],
        "joins": result.joins, "steps": result.steps,
        "checks": [c.as_dict() for c in result.checks],
    }


def render_markdown(report: dict) -> str:
    s = report["check_summary"]
    lines = [
        "# Analytical Layer Report", "",
        f"- **Run ID:** `{report['run_id']}`",
        f"- **Executed (UTC):** {report['executed_at_utc']} → {report['finished_at_utc']}",
        f"- **Git commit:** `{report['git'].get('commit')}`"
        + (" (working tree has uncommitted changes)" if report["git"].get("dirty") else ""),
        f"- **Code SHA-256 (src/):** `{report['code_sha256']}`",
        f"- **Inputs:** clean `{report['clean_dir']}` (clean run `{report['clean_run_id']}`); raw `{report['raw_dir']}` (checksums only)",
        f"- **Output directory:** `{report['output_dir']}` · **Manifest:** `{report['manifest']}`",
        f"- **Baseline checksums verified:** {report['baseline_verified']}",
        f"- **Outputs written:** {report['outputs_written']} · **Success:** {report['success']}",
        "", "## Validation summary", "", "| Class | Total | Passed | Failed |", "|---|---:|---:|---:|",
    ]
    for cls in ("blocking", "regression", "warning"):
        if cls in s:
            lines.append(f"| {cls} | {s[cls]['total']} | {s[cls]['passed']} | {s[cls]['failed']} |")
    fails = [c for c in report["checks"] if c["passed"] is False]
    lines += ["", "**Failed checks:** " + ("none" if not fails else "")]
    lines += [f"- `{c['check_id']}` {c['description']}: expected `{c['expected']}`, observed `{c['observed']}`" for c in fails]
    lines += ["", "**Warnings (logged values):**", ""]
    lines += [f"- `{c['check_id']}` {c['description']}: **{c['observed']}**" for c in report["checks"] if c["check_class"] == "warning"]

    lines += ["", "## Decisions applied", ""] + [f"- **{k}:** {v}" for k, v in report["decisions"].items()]
    lines += ["", "## Tables", "", "| Table | Kind | Grain | Primary key | Rows | Columns |", "|---|---|---|---|---:|---:|"]
    for o in report["outputs"]:
        lines.append(f"| `{o['output_dataset']}` | {o['kind']} | {o['grain']} | `{', '.join(o['primary_key'])}` | "
                     f"{o['rows']:,} | {o['columns']} |")
    lines += ["", "## Joins (all validated)", "", "| Join | validate | Left rows | Right rows | Result rows |",
              "|---|---|---:|---:|---:|"]
    lines += [f"| `{j['join']}` | {j['validate']} | {j['left_rows']:,} | {j['right_rows']:,} | {j['result_rows']:,} |"
              for j in report["joins"]]
    for o in report["outputs"]:
        lines += ["", f"### `{o['output_dataset']}`", "", f"**Purpose:** {o['purpose']}", "",
                  f"**Grain:** {o['grain']} · **PK:** `{', '.join(o['primary_key'])}` · "
                  f"**FKs:** {', '.join(f'`{x}`' for x in o['foreign_keys']) or 'none'} · "
                  f"**Sources:** {', '.join(o['sources'])} · **Path:** `{o['path']}`", "",
                  "| Column | dtype | Missing |", "|---|---|---:|"]
        lines += [f"| `{c}` | {t} | {o['missing_values'][c]:,} |" for c, t in o["schema"].items()]
    lines += ["", "## All checks", "", "| ID | Class | Description | Expected | Observed | Result |", "|---|---|---|---|---|---|"]
    for c in report["checks"]:
        res = "PASS" if c["passed"] is True else "FAIL" if c["passed"] is False else "logged"
        lines.append(f"| `{c['check_id']}` | {c['check_class']} | {c['description']} | `{c['expected']}` | `{c['observed']}` | {res} |")
    return "\n".join(lines) + "\n"


def write_reports(result, report_dir: Path) -> list[Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(result)
    json_path, md_path = report_dir / "analytical_report.json", report_dir / "analytical_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return [json_path, md_path]


def console_summary(result) -> str:
    s = summarize(result.checks)
    lines = ["ANALYTICAL RUN",
             f"  run id            : {result.run_id}",
             f"  git commit        : {result.git.get('commit')}{' (dirty)' if result.git.get('dirty') else ''}",
             f"  clean run         : {result.clean_run_id}",
             f"  baseline verified : {result.baseline_verified}",
             f"  outputs written   : {result.written}  ->  {rel(result.out_dir)}",
             f"  manifest          : {rel(result.manifest_path)}", "",
             f"  {'table':<18} {'kind':<10} {'rows':>10} {'cols':>5}  grain"]
    for r in result.outputs:
        lines.append(f"  {r.name:<18} {TABLES[r.name].kind:<10} {len(r.frame):>10,} {r.frame.shape[1]:>5}  {TABLES[r.name].grain[:60]}")
    lines += ["", "  checks: " + ", ".join(f"{k} {v['passed']}/{v['total']} passed" if k != "warning"
                                          else f"{v['total']} warnings logged" for k, v in s.items())]
    lines += [f"  FAILED {c.check_id}: {c.description} (expected {c.expected}, observed {c.observed})" for c in failed(result.checks)]
    lines += [f"  warning {c.check_id}: {c.description} = {c.observed}" for c in result.checks if c.check_class == "warning"]
    lines += [f"  report: {rel(p)}" for p in result.report_paths]
    lines.append(f"  RESULT: {'SUCCESS' if result.success else 'FAILED'}")
    return "\n".join(lines)
