"""Run the dbt CLI against the project database (Task 5.4).

``python -m src.database dbt <dbt arguments>`` - e.g. ``... dbt debug`` - runs the dbt executable
of the separate ``.venv-dbt`` environment (``requirements-dbt.txt``) on the project in ``dbt/``.

* dbt is a separate toolchain: it is run as a subprocess, never imported, so the project
  environment (``.venv``) needs no dbt dependencies.
* Connection settings come from the same place as for every other command (``.env`` and the
  process environment, via ``load_settings``) and are passed ONLY to the dbt child process. The
  password is passed as ``DBT_ENV_SECRET_POSTGRES_PASSWORD``, which dbt scrubs from its output and
  logs; nothing is written to disk and nothing is printed.
* dbt always connects as the application role (``POSTGRES_USER``). The admin login and any other
  ``POSTGRES_*`` / ``DBT_ENV_SECRET_*`` variables are removed from the child environment.
* The working directory is ``dbt/`` (also passed as project and profiles directory), so dbt's
  ``target/`` and ``logs/`` stay inside ``dbt/``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .connection import DatabaseConfigError, Settings, load_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBT_PROJECT_DIR = PROJECT_ROOT / "dbt"
DBT_VENV = PROJECT_ROOT / ".venv-dbt"
PASSWORD_VARIABLE = "DBT_ENV_SECRET_POSTGRES_PASSWORD"


class DbtNotInstalledError(DatabaseConfigError):
    """The dbt environment (.venv-dbt) or the dbt project directory is missing."""


def dbt_executable(venv: Path | None = None) -> Path:
    exe = (venv or DBT_VENV) / ("Scripts/dbt.exe" if os.name == "nt" else "bin/dbt")
    if not exe.is_file():
        raise DbtNotInstalledError(f"dbt is not installed: {exe} not found (create .venv-dbt and install "
                                   "requirements-dbt-lock.txt; see dbt/README.md)")
    return exe


def dbt_environment(settings: Settings, base: dict | None = None, project_dir: Path = DBT_PROJECT_DIR) -> dict:
    """Environment of the dbt child process: ``base`` (default: this process) without any PostgreSQL
    credentials, plus exactly the application-role connection settings that ``dbt/profiles.yml`` reads."""
    if not settings.password:
        raise DatabaseConfigError("application password not configured: set POSTGRES_PASSWORD (see .env.example)")
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if not (k.startswith("POSTGRES_") or k.startswith("DBT_ENV_SECRET_"))}
    env.update({
        "POSTGRES_HOST": settings.host,
        "POSTGRES_PORT": str(settings.port),
        "POSTGRES_USER": settings.user,
        "POSTGRES_DB": settings.database,
        "POSTGRES_CONNECT_TIMEOUT": str(settings.connect_timeout),
        PASSWORD_VARIABLE: settings.password,
        "DBT_PROJECT_DIR": str(project_dir),
        "DBT_PROFILES_DIR": str(project_dir),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    })
    return env


def run_dbt(args: list[str], settings: Settings | None = None, runner=subprocess.run, **run_kwargs) -> int:
    """Run ``dbt <args>`` in ``dbt/`` with the project connection settings; returns dbt's exit code."""
    if not (DBT_PROJECT_DIR / "dbt_project.yml").is_file():
        raise DbtNotInstalledError(f"dbt project not found: {DBT_PROJECT_DIR / 'dbt_project.yml'}")
    exe = dbt_executable()
    env = dbt_environment(settings or load_settings())
    completed = runner([str(exe), *args], cwd=str(DBT_PROJECT_DIR), env=env, check=False, **run_kwargs)
    return completed.returncode
