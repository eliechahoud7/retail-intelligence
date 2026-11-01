"""Command-line entry point: ``python -m src.analytical`` (run from the project root)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.clean.pipeline import DEFAULT_CLEAN_DIR, DEFAULT_MANIFEST_DIR
from src.ingestion import DEFAULT_RAW_DIR, IngestionError

from .inputs import AnalyticalInputError
from .pipeline import DEFAULT_ANALYTICAL_DIR, DEFAULT_REPORT_DIR, run_analytical
from .report import console_summary

EXIT_OK = 0
EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.analytical",
                                description="Build the analytical layer (star schema) from the clean outputs.")
    p.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="Raw CSV directory, checksummed only (default: data/raw).")
    p.add_argument("--clean-dir", type=Path, default=DEFAULT_CLEAN_DIR, help="Clean Parquet directory (default: data/processed/clean).")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_ANALYTICAL_DIR,
                   help="Analytical Parquet output directory (default: data/processed/analytical).")
    p.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR,
                   help="Run manifest directory (default: data/processed/_manifest).")
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Report directory (default: reports/analytical).")
    p.add_argument("--skip-baseline-check", action="store_true",
                   help="Skip baseline checksum and regression checks (test fixtures / alternative data only).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_analytical(args.raw_dir, args.clean_dir, args.out_dir, args.manifest_dir, args.report_dir,
                                verify_baseline=not args.skip_baseline_check)
    except (IngestionError, AnalyticalInputError, pd.errors.MergeError) as exc:
        print(f"ANALYTICAL FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(console_summary(result))
    return EXIT_OK if result.success else EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
