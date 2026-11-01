"""Analytics views in PostgreSQL (Task 5.2.5).

The view definitions live as plain SQL in ``src/database/sql/views/NN_<view>.sql`` with two
placeholders, ``{w}`` (warehouse schema) and ``{a}`` (analytics schema), so the same SQL runs
against the real schemas and against throwaway test schemas.

* ``create_views``: applies every file (``CREATE OR REPLACE VIEW`` + ``COMMENT ON VIEW``) in ONE
  transaction as the application role; rerunning is safe and never touches table data.
* ``verify_views``: every expected view exists, has a comment and executes, and its totals
  reconcile with the underlying fact and dimension tables.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .connection import get_engine
from .warehouse import ANALYTICS_SCHEMA, WAREHOUSE_SCHEMA

SQL_DIR = Path(__file__).resolve().parent / "sql" / "views"


def view_files() -> list[Path]:
    return sorted(SQL_DIR.glob("[0-9][0-9]_*.sql"))


def view_names() -> list[str]:
    return [p.stem.split("_", 1)[1] for p in view_files()]


def render(path: Path, warehouse_schema: str, analytics_schema: str, quote) -> str:
    return path.read_text(encoding="utf-8").format(w=quote(warehouse_schema), a=quote(analytics_schema))


def create_views(engine: Engine | None = None, warehouse_schema: str = WAREHOUSE_SCHEMA,
                 analytics_schema: str = ANALYTICS_SCHEMA) -> list[str]:
    own = engine is None
    engine = engine or get_engine()
    try:
        with engine.begin() as conn:  # all views or none
            raw = conn.connection.dbapi_connection
            quote = conn.dialect.identifier_preparer.quote
            for path in view_files():
                raw.execute(render(path, warehouse_schema, analytics_schema, quote))
        return view_names()
    finally:
        if own:
            engine.dispose()


def reconciliation_checks(w: str, a: str) -> list[tuple[str, str, str]]:
    """(name, query on the view, equivalent query on the warehouse tables) - results must be equal."""
    fol, fo, fp, dc = f"{w}.fact_order_lines", f"{w}.fact_orders", f"{w}.fact_payments", f"{w}.dim_customers"
    dlv = "order_status = 'delivered'"
    eligible = "order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL"
    # coalesce(.., 0) on both sides: a sum over no rows is NULL in SQL, while the views show 0
    line_totals = "coalesce(sum(units), 0), coalesce(sum(items_price_total), 0), coalesce(sum(items_freight_total), 0)"
    base_lines = (f"SELECT coalesce(sum(quantity), 0), coalesce(sum(line_price_total), 0), "
                  f"coalesce(sum(freight_total), 0) FROM {fol}")
    return [
        ("sales_daily: order counts", f"SELECT sum(n_orders), sum(n_orders_delivered), sum(n_orders_canceled) FROM {a}.sales_daily",
         f"SELECT count(*), count(*) FILTER (WHERE {dlv}), count(*) FILTER (WHERE order_status = 'canceled') FROM {fo}"),
        ("sales_daily: item totals", f"SELECT {line_totals} FROM {a}.sales_daily", base_lines),
        ("sales_daily: delivered item totals",
         f"SELECT coalesce(sum(units_delivered), 0), coalesce(sum(items_price_total_delivered), 0), "
         f"coalesce(sum(items_freight_total_delivered), 0) FROM {a}.sales_daily",
         f"SELECT coalesce(sum(quantity), 0), coalesce(sum(line_price_total), 0), coalesce(sum(freight_total), 0) "
         f"FROM {fol} WHERE {dlv}"),
        ("sales_daily: payment totals",
         f"SELECT sum(n_payments), coalesce(sum(payment_total), 0), coalesce(sum(payment_total_delivered), 0) FROM {a}.sales_daily",
         f"SELECT count(*), coalesce(sum(payment_value), 0), coalesce(sum(payment_value) FILTER (WHERE {dlv}), 0) FROM {fp}"),
        ("sales_daily: one row per day in the purchase range, no gaps",
         f"SELECT count(*), count(DISTINCT purchase_date), max(purchase_date) - min(purchase_date) + 1 FROM {a}.sales_daily",
         f"SELECT max(purchase_date) - min(purchase_date) + 1, max(purchase_date) - min(purchase_date) + 1, "
         f"max(purchase_date) - min(purchase_date) + 1 FROM {fo}"),
        ("product_performance: rows and totals", f"SELECT count(*), {line_totals} FROM {a}.product_performance",
         f"SELECT (SELECT count(*) FROM {w}.dim_products), sum(quantity), sum(line_price_total), sum(freight_total) FROM {fol}"),
        ("category_performance: products and totals", f"SELECT sum(n_products), {line_totals} FROM {a}.category_performance",
         f"SELECT (SELECT count(*) FROM {w}.dim_products), sum(quantity), sum(line_price_total), sum(freight_total) FROM {fol}"),
        ("seller_performance: rows and totals", f"SELECT count(*), {line_totals} FROM {a}.seller_performance",
         f"SELECT (SELECT count(*) FROM {w}.dim_sellers), sum(quantity), sum(line_price_total), sum(freight_total) FROM {fol}"),
        ("customer_performance: rows and totals (source: dim_customers)",
         f"SELECT count(*), sum(n_orders), coalesce(sum(items_price_total), 0), coalesce(sum(payment_total), 0) "
         f"FROM {a}.customer_performance",
         f"SELECT count(*), sum(n_orders), coalesce(sum(items_price_total), 0), coalesce(sum(payment_total), 0) FROM {dc}"),
        ("customer_performance: order counts (source: fact_orders)",
         f"SELECT sum(n_orders_with_items), sum(n_orders_with_payment), coalesce(sum(n_review_links), 0) "
         f"FROM {a}.customer_performance",
         f"SELECT count(*) FILTER (WHERE has_items), count(*) FILTER (WHERE has_payment), coalesce(sum(review_count), 0) "
         f"FROM {fo}"),
        ("delivery_monthly: orders, eligible, late",
         f"SELECT sum(n_orders), sum(n_delivery_eligible), sum(n_late) FROM {a}.delivery_monthly",
         f"SELECT count(*), count(*) FILTER (WHERE {eligible}), count(*) FILTER (WHERE {eligible} AND is_late) FROM {fo}"),
        ("delivery_by_state: orders, eligible, late",
         f"SELECT sum(n_orders), sum(n_delivery_eligible), sum(n_late) FROM {a}.delivery_by_state",
         f"SELECT count(*), count(*) FILTER (WHERE {eligible}), count(*) FILTER (WHERE {eligible} AND is_late) FROM {fo}"),
    ]


def verify_views(engine: Engine | None = None, warehouse_schema: str = WAREHOUSE_SCHEMA,
                 analytics_schema: str = ANALYTICS_SCHEMA) -> dict:
    own = engine is None
    engine = engine or get_engine()
    problems, rows, checks = [], {}, []
    try:
        with engine.connect() as conn:
            quote = conn.dialect.identifier_preparer.quote
            w, a = quote(warehouse_schema), quote(analytics_schema)
            existing = {r[0]: r[1] for r in conn.execute(text(
                "SELECT c.relname, obj_description(c.oid, 'pg_class') FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :a AND c.relkind = 'v'"),
                {"a": analytics_schema})}
            other = conn.execute(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                      "WHERE n.nspname = :a AND c.relkind <> 'v'"), {"a": analytics_schema}).scalar()
            if other:
                problems.append(f"{analytics_schema}: {other} non-view relation(s)")
            for v in view_names():
                if v not in existing:
                    problems.append(f"view {v}: missing")
                    continue
                if not existing[v]:
                    problems.append(f"view {v}: no COMMENT (purpose/grain)")
                rows[v] = conn.execute(text(f"SELECT count(*) FROM {a}.{quote(v)}")).scalar()
            for extra in sorted(set(existing) - set(view_names())):
                problems.append(f"view {extra}: not defined in {SQL_DIR.name}")
            if not problems:
                for name, on_view, on_tables in reconciliation_checks(w, a):
                    got, want = tuple(conn.execute(text(on_view)).one()), tuple(conn.execute(text(on_tables)).one())
                    checks.append({"check": name, "view": got, "tables": want, "ok": got == want})
                    if got != want:
                        problems.append(f"{name}: view {got} != tables {want}")
    finally:
        if own:
            engine.dispose()
    return {"ok": not problems, "problems": problems, "rows": rows, "checks": checks}
