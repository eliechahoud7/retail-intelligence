"""Command-line entry point: ``python -m src.ingestion`` (run from the project root)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import IngestionError
from .loader import DEFAULT_RAW_DIR, ingest
from .profiling import build_json_report, format_text_report, profile_result, write_json_report

EXIT_OK = 0
EXIT_INGESTION_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingestion",
        description="Load and profile the nine raw Olist CSV files (read-only; no cleaning).")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR,
                        help="Directory containing the raw CSV files (default: data/raw).")
    parser.add_argument("--report-json", type=Path, default=None,
                        help="Optional path for a JSON copy of the ingestion report.")
    parser.add_argument("--skip-baseline-check", action="store_true",
                        help="Do not compare raw checksums with the approved baseline "
                             "(for test fixtures or alternative data only).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = ingest(args.raw_dir, verify_baseline=not args.skip_baseline_check)
    except IngestionError as exc:
        print(f"INGESTION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INGESTION_ERROR

    profiles = profile_result(result)
    print(format_text_report(result, profiles))
    if args.report_json:
        path = write_json_report(build_json_report(result, profiles), args.report_json)
        print(f"\nJSON report written to {path}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
