"""PostgreSQL connection settings, SQLAlchemy engines and role setup (Tasks 5.1, 5.2.1).

Two logins, both configured through environment variables with a project-root ``.env`` file
as fallback (real environment variables win):

* the **application** role (``POSTGRES_USER`` / ``POSTGRES_PASSWORD``, e.g. ``retail_app``):
  used by the project code; no superuser, createdb or createrole privileges;
* the **administrative** login (``POSTGRES_ADMIN_USER`` / ``POSTGRES_ADMIN_PASSWORD``, e.g.
  ``postgres``): used only to create the database and the application role.

Passwords are never printed: ``Settings`` hides them in its repr, URLs are only rendered with
``hide_password=True``, and the application password is sent to the server only as a SCRAM
verifier computed client-side by libpq (as psql's ``\\password`` does).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from src.ingestion.loader import PROJECT_ROOT

ENV_FILE = PROJECT_ROOT / ".env"
DRIVER = "postgresql+psycopg"
REQUIRED = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_DB")  # passwords checked when used
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Database privileges of the application role: connect, temporary tables, and CREATE (create its
# own schemas, which it then owns). Nothing on other databases beyond PostgreSQL's defaults.
APP_DATABASE_PRIVILEGES = ("CONNECT", "TEMPORARY", "CREATE")
APP_ROLE_ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"


class DatabaseConfigError(Exception):
    """Connection settings are missing or invalid."""


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines (comments and blank lines ignored; optional quotes stripped)."""
    values: dict[str, str] = {}
    if not Path(path).is_file():
        return values
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str
    admin_database: str = "postgres"
    connect_timeout: int = 10  # seconds; libpq waits indefinitely by default
    admin_user: str | None = None
    admin_password: str | None = field(default=None, repr=False)

    def url(self, database: str | None = None, admin: bool = False) -> URL:
        if admin and not (self.admin_user and self.admin_password):
            raise DatabaseConfigError("administrative login not configured: set POSTGRES_ADMIN_USER and "
                                      "POSTGRES_ADMIN_PASSWORD (see .env.example)")
        if not admin and not self.password:
            raise DatabaseConfigError("application password not configured: set POSTGRES_PASSWORD (see .env.example)")
        user, password = (self.admin_user, self.admin_password) if admin else (self.user, self.password)
        return URL.create(DRIVER, username=user, password=password, host=self.host, port=self.port,
                          database=database or self.database)

    def safe_url(self, database: str | None = None, admin: bool = False) -> str:
        return self.url(database, admin).render_as_string(hide_password=True)


def load_settings(env_file: Path = ENV_FILE, environ: dict | None = None) -> Settings:
    merged = {**read_env_file(env_file), **{k: v for k, v in (os.environ if environ is None else environ).items()
                                             if k.startswith("POSTGRES_")}}
    missing = [k for k in REQUIRED if not merged.get(k)]
    if missing:
        raise DatabaseConfigError(f"missing setting(s): {', '.join(missing)} (set them in {env_file.name} "
                                  "or the environment; see .env.example)")
    try:
        port = int(merged["POSTGRES_PORT"])
        timeout = int(merged.get("POSTGRES_CONNECT_TIMEOUT") or 10)
    except ValueError:
        raise DatabaseConfigError("POSTGRES_PORT and POSTGRES_CONNECT_TIMEOUT must be integers") from None
    for key in ("POSTGRES_DB", "POSTGRES_ADMIN_DB", "POSTGRES_USER"):
        if key in merged and not _IDENTIFIER.fullmatch(merged[key]):
            raise DatabaseConfigError(f"{key} must be a lower-case identifier ([a-z_][a-z0-9_]*)")
    return Settings(host=merged["POSTGRES_HOST"], port=port, user=merged["POSTGRES_USER"],
                    password=merged.get("POSTGRES_PASSWORD", ""), database=merged["POSTGRES_DB"],
                    admin_database=merged.get("POSTGRES_ADMIN_DB") or "postgres", connect_timeout=timeout,
                    admin_user=merged.get("POSTGRES_ADMIN_USER") or None,
                    admin_password=merged.get("POSTGRES_ADMIN_PASSWORD") or None)


def get_engine(settings: Settings | None = None, database: str | None = None, admin: bool = False,
               **kwargs) -> Engine:
    """SQLAlchemy engine; the application role by default, the admin login with ``admin=True``."""
    settings = settings or load_settings()
    connect_args = {"connect_timeout": settings.connect_timeout, **kwargs.pop("connect_args", {})}
    return create_engine(settings.url(database, admin), pool_pre_ping=True, hide_parameters=True,
                         connect_args=connect_args, **kwargs)


def _quote(engine: Engine, identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise DatabaseConfigError(f"invalid identifier: {identifier!r}")
    return engine.dialect.identifier_preparer.quote(identifier)


def ensure_database(settings: Settings | None = None) -> bool:
    """Create the project database (admin login) if it does not exist. Returns True if created."""
    settings = settings or load_settings()
    engine = get_engine(settings, settings.admin_database, admin=True, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": settings.database}).scalar():
                return False
            conn.execute(text(f"CREATE DATABASE {_quote(engine, settings.database)} ENCODING 'UTF8'"))
            return True
    finally:
        engine.dispose()


def ensure_app_role(settings: Settings | None = None) -> dict:
    """Create or update the application role and its database privileges (admin login; idempotent).

    * role attributes: ``APP_ROLE_ATTRIBUTES`` (login only; no superuser/createdb/createrole);
    * password: set from ``POSTGRES_PASSWORD`` as a SCRAM-SHA-256 verifier computed by libpq;
    * ``REVOKE ALL ON DATABASE <db> FROM PUBLIC``, then ``GRANT CONNECT, TEMPORARY, CREATE`` to
      the role, so only the application role (and superusers) can use the project database.
    """
    settings = settings or load_settings()
    if settings.admin_user and settings.user == settings.admin_user:
        raise DatabaseConfigError("POSTGRES_USER must be the application role, not the admin login")
    if not settings.password:
        raise DatabaseConfigError("set POSTGRES_PASSWORD (the new application role password) before setup-app-role")
    engine = get_engine(settings, settings.database, admin=True, isolation_level="AUTOCOMMIT")
    role, db = _quote(engine, settings.user), _quote(engine, settings.database)
    try:
        with engine.connect() as conn:
            existed = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": settings.user}).scalar()
            conn.execute(text(f"{'ALTER' if existed else 'CREATE'} ROLE {role} {APP_ROLE_ATTRIBUTES}"))
            raw = conn.connection.dbapi_connection  # psycopg connection: libpq computes the verifier
            verifier = raw.pgconn.encrypt_password(settings.password.encode("utf-8"), settings.user.encode("utf-8"),
                                                   b"scram-sha-256").decode("ascii")
            raw.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(settings.user), sql.Literal(verifier)))
            conn.execute(text(f"REVOKE ALL ON DATABASE {db} FROM PUBLIC"))
            conn.execute(text(f"GRANT {', '.join(APP_DATABASE_PRIVILEGES)} ON DATABASE {db} TO {role}"))
        return {"role": settings.user, "created": not existed}
    finally:
        engine.dispose()


def role_report(settings: Settings | None = None) -> dict:
    """Attributes and project-database privileges of the application role (admin login)."""
    settings = settings or load_settings()
    engine = get_engine(settings, settings.database, admin=True)
    try:
        with engine.connect() as conn:
            attrs = conn.execute(text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin, rolinherit, "
                "rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname = :r"), {"r": settings.user}).one()
            privs = {p: conn.execute(text("SELECT has_database_privilege(:r, :d, :p)"),
                                     {"r": settings.user, "d": settings.database, "p": p}).scalar()
                     for p in ("CONNECT", "TEMPORARY", "CREATE")}
            public_connect = conn.execute(text("SELECT has_database_privilege('public', :d, 'CONNECT')"),
                                          {"d": settings.database}).scalar()
            memberships = [r[0] for r in conn.execute(text(
                "SELECT b.rolname FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid "
                "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = :r"), {"r": settings.user})]
        keys = ("superuser", "createdb", "createrole", "replication", "bypassrls", "login", "inherit", "scram_password")
        return {"attributes": dict(zip(keys, attrs)), "database_privileges": privs,
                "public_can_connect": public_connect, "member_of": memberships}
    finally:
        engine.dispose()


def check_connection(settings: Settings | None = None, admin: bool = False) -> dict[str, str]:
    """Connect to the project database and return server version, database and user."""
    engine = get_engine(settings, admin=admin)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version(), current_setting('server_version'), current_database(), "
                                    "current_user")).one()
        return {"version": row[0], "server_version": row[1], "database": row[2], "user": row[3]}
    finally:
        engine.dispose()
