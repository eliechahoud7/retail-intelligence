"""PostgreSQL connection layer (Task 5.1). Settings come from the environment / ``.env``.

Typical use::

    from src.database import get_engine
    engine = get_engine()            # project database from .env / environment
"""

from .connection import (DatabaseConfigError, Settings, check_connection, ensure_app_role, ensure_database,
                         get_engine, load_settings, role_report)

__all__ = ["get_engine", "load_settings", "ensure_database", "ensure_app_role", "role_report", "check_connection",
           "Settings", "DatabaseConfigError"]
