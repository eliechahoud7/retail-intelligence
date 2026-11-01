"""``python -m src.database {check,create-db,setup-app-role,show-role}`` (run from the project root).

Never prints a password.
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from src.analytical.pipeline import DEFAULT_ANALYTICAL_DIR
from src.clean.pipeline import DEFAULT_MANIFEST_DIR

from .compare import validate_loaded
from .connection import get_engine
from .loader import GoldSourceError, LoadValidationError, load_warehouse, locate_gold

from .connection import (DatabaseConfigError, check_connection, ensure_app_role, ensure_database, load_settings,
                         role_report)
from .warehouse import WAREHOUSE_SCHEMA, create_warehouse, verify_warehouse


def _print_validation(v: dict) -> None:
    for t, d in v["tables"].items():
        print(f"  {t:17} rows gold/db={d.get('rows')}  null_cols={d.get('null_columns')}  "
              f"money={d.get('money_totals', '')}")
    print(f"  rows total: {v['rows_total']:,}  cells compared: {v['cells_compared']:,}")
    for p in v["problems"]:
        print(f"  PROBLEM: {p}")
    print(f"  VALIDATION: {'OK' if v['ok'] else 'FAILED'}")


def _print_verification(result: dict) -> int:
    for k, v in result["summary"].items():
        print(f"  {k}: {v}")
    for p in result["problems"]:
        print(f"  PROBLEM: {p}")
    print(f"  RESULT: {'OK' if result['ok'] else 'FAILED'}")
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.database", description="PostgreSQL connection utilities.")
    p.add_argument("command", choices=["check", "create-db", "setup-app-role", "show-role", "create-warehouse",
                                       "verify-warehouse", "load-warehouse", "validate-load"],
                   help="check: connect and report version/database/user; create-db: create POSTGRES_DB if missing "
                        "(admin); setup-app-role: create/update the application role and grants (admin); "
                        "show-role: application role attributes and privileges (admin); create-warehouse: create "
                        "the warehouse/analytics schemas and missing tables as the application role, then verify; "
                        "verify-warehouse: compare the database with the approved design; load-warehouse: "
                        "atomically refresh the warehouse from the Gold Parquet outputs, then re-validate; "
                        "validate-load: compare the warehouse contents with Gold")
    p.add_argument("--expect-empty", action="store_true",
                   help="create-warehouse / verify-warehouse: also require every warehouse table to be empty")
    p.add_argument("--gold-dir", type=Path, default=DEFAULT_ANALYTICAL_DIR, help="Gold Parquet directory")
    p.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR, help="run manifest directory")
    p.add_argument("--admin", action="store_true", help="check: connect with the administrative login instead")
    args = p.parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "create-db":
            print(f"admin target: {settings.safe_url(settings.admin_database, admin=True)}")
            print(f"database '{settings.database}': {'created' if ensure_database(settings) else 'already exists'}")
            return 0
        if args.command == "setup-app-role":
            print(f"admin target: {settings.safe_url(admin=True)}")
            r = ensure_app_role(settings)
            print(f"role '{r['role']}': {'created' if r['created'] else 'updated'} (password set as SCRAM verifier)")
            return 0
        if args.command == "create-warehouse":
            print(f"target: {settings.safe_url()}")
            created = create_warehouse()
            print(f"schemas created: {created['schemas_created'] or 'none (already present)'}")
            print(f"tables created: {created['tables_created'] or 'none (already present)'}")
            print("verification:")
            return _print_verification(verify_warehouse(expect_empty=args.expect_empty))
        if args.command in ("load-warehouse", "validate-load"):
            print(f"target: {settings.safe_url()}")
            gold = locate_gold(args.gold_dir, args.manifest_dir)
            print(f"gold run: {gold.run_id} (git {gold.git.get('commit')}) · manifest verified · "
                  f"{sum(gold.row_counts.values()):,} rows in {len(gold.frames)} tables")
            engine = get_engine(settings)
            try:
                if args.command == "load-warehouse":
                    r = load_warehouse(gold, engine)
                    print(f"loaded in one transaction: load_id={r['load_id']}  "
                          f"{(r['finished_at'] - r['started_at']).total_seconds():.1f}s")
                    for t, n in r["rows_loaded"].items():
                        print(f"  {t:17} {n:>9,}")
                    print(f"  {'total':17} {sum(r['rows_loaded'].values()):>9,}")
                print("post-commit validation (fresh connection):" if args.command == "load-warehouse"
                      else "validation:")
                v = validate_loaded(engine, gold.frames, WAREHOUSE_SCHEMA)
            finally:
                engine.dispose()
            _print_validation(v)
            return 0 if v["ok"] else 1
        if args.command == "verify-warehouse":
            print(f"target: {settings.safe_url()}")
            return _print_verification(verify_warehouse(expect_empty=args.expect_empty))
        if args.command == "show-role":
            for k, v in role_report(settings).items():
                print(f"{k}: {v}")
            return 0
        print(f"target: {settings.safe_url(admin=args.admin)}")
        info = check_connection(settings, admin=args.admin)
    except DatabaseConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1
    except (GoldSourceError, LoadValidationError) as exc:
        print(f"LOAD FAILED (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        orig = getattr(exc, "orig", None) or exc
        print(f"DATABASE ERROR: {type(orig).__name__}: {str(orig).strip().splitlines()[0]}", file=sys.stderr)
        return 1
    print(f"connected: database={info['database']} user={info['user']} server_version={info['server_version']}")
    print(f"version(): {info['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
