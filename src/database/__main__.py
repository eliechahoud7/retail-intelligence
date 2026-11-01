"""``python -m src.database {check,create-db,setup-app-role,show-role}`` (run from the project root).

Never prints a password.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from .connection import (DatabaseConfigError, check_connection, ensure_app_role, ensure_database, load_settings,
                         role_report)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.database", description="PostgreSQL connection utilities.")
    p.add_argument("command", choices=["check", "create-db", "setup-app-role", "show-role"],
                   help="check: connect and report version/database/user; create-db: create POSTGRES_DB if missing "
                        "(admin); setup-app-role: create/update the application role and grants (admin); "
                        "show-role: application role attributes and privileges (admin)")
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
        if args.command == "show-role":
            for k, v in role_report(settings).items():
                print(f"{k}: {v}")
            return 0
        print(f"target: {settings.safe_url(admin=args.admin)}")
        info = check_connection(settings, admin=args.admin)
    except DatabaseConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
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
