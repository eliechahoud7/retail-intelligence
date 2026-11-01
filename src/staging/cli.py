"""Command-line entry point: ``python -m src.staging`` (run from the project root)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.ingestion import DEFAULT_RAW_DIR, IngestionError

from .pipeline import DEFAULT_REPORT_DIR, DEFAULT_STAGING_DIR, run_staging
from .report import console_summary

EXIT_OK = 0
EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.staging",
                                description="Build the staging layer: typed Parquet copies of the raw data.")
    p.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="Raw CSV directory (default: data/raw).")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_STAGING_DIR, help="Parquet output directory (default: data/staging).")
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Report directory (default: reports/staging).")
    p.add_argument("--skip-baseline-check", action="store_true",
                   help="Skip baseline checksum and regression checks (test fixtures / alternative data only).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_staging(args.raw_dir, args.out_dir, args.report_dir, verify_baseline=not args.skip_baseline_check)
    except IngestionError as exc:
        print(f"STAGING FAILED during ingestion: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(console_summary(result))
    return EXIT_OK if result.success else EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
