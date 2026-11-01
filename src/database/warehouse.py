"""PostgreSQL warehouse schema (Task 5.2.3), implementing reports/database/warehouse_schema_design.md.

* Table and column names/order come from the Gold contract ``src.analytical.schema.TABLES``,
  so the warehouse cannot drift from Gold; the design supplies types, nullability, keys,
  constraints and indexes (sections 3-5, 7, 9).
* ``create_warehouse`` runs as the application role in ONE transaction (PostgreSQL DDL is
  transactional): it creates the schemas and any missing tables with their constraints and
  indexes; existing objects are never altered or dropped, so a rerun is safe.
* ``verify_warehouse`` compares the live catalog with the design, with expected types written
  as PostgreSQL ``format_type`` strings (independent of SQLAlchemy's type rendering).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (ARRAY, Boolean, CheckConstraint, Column, Date, ForeignKeyConstraint, Index, Integer,
                        MetaData, Numeric, PrimaryKeyConstraint, Table, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, TIMESTAMP
from sqlalchemy.engine import Connection, Engine

from src.analytical.schema import TABLES

from .connection import get_engine

WAREHOUSE_SCHEMA = "warehouse"
ANALYTICS_SCHEMA = "analytics"
LOAD_RUN = "load_run"

# ----------------------------------------------------------------------------- design inputs
MONEY_COLUMNS = frozenset({  # section 3 / W-03: numeric(12,2)
    "unit_price", "freight_value_per_row", "line_price_total", "freight_total",
    "items_price_total", "items_freight_total", "payment_total", "payment_minus_items", "payment_value",
    "items_price_total_delivered", "items_freight_total_delivered", "payment_total_delivered",
})
DATE_KEY_COLUMNS = frozenset({"purchase_date", "date"})  # W-05; every other datetime stays timestamp(0) (Q2)

NULLABLE = {  # section 4: exactly the columns that can be missing in Gold
    "fact_order_lines": set(),
    "fact_orders": {"order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date",
                    "customer_lat_centroid", "customer_lng_centroid", "items_price_total", "items_freight_total",
                    "payment_total", "payment_types", "max_installments", "payment_minus_items",
                    "approval_hours", "purchase_to_carrier_days", "approval_to_carrier_days",
                    "carrier_to_customer_days", "approval_to_delivery_days", "purchase_to_delivery_days",
                    "delivery_vs_estimate_days", "is_late"},
    "fact_payments": set(),
    "fact_reviews": {"review_comment_title", "review_comment_message", "order_delivered_customer_date"},
    "dim_customers": {"items_price_total", "items_freight_total", "payment_total", "items_price_total_delivered",
                      "items_freight_total_delivered", "payment_total_delivered", "first_delivered_purchase_ts",
                      "last_delivered_purchase_ts"},
    "dim_products": {"product_category_name", "category_en_clean", "product_name_length",
                     "product_description_length", "product_photos_qty", "product_weight_g", "weight_g_valid",
                     "product_length_cm", "product_height_cm", "product_width_cm"},
    "dim_sellers": {"lat_centroid", "lng_centroid"},
    "dim_dates": set(),
}

UNIQUE = {  # W-07 grain guards
    "fact_order_lines": [("grain", ("order_id", "product_id", "seller_id", "unit_price", "freight_value_per_row",
                                    "shipping_limit_date"))],
    "fact_orders": [("customer_id", ("customer_id",))],
}

CHECKS = {  # W-08 / Q4: pipeline invariants only (Task 4.4 V13 and V06)
    "fact_order_lines": [("quantity_positive", "quantity >= 1"),
                         ("item_ids_match_quantity", "cardinality(order_item_ids) = quantity"),
                         ("line_total", "line_price_total = unit_price * quantity")],
    "fact_orders": [("customer_zip_format", "customer_zip_code_prefix ~ '^[0-9]{5}$'")],
    "dim_sellers": [("seller_zip_format", "seller_zip_code_prefix ~ '^[0-9]{5}$'")],
}

INDEXES = {  # section 5 (PK/UNIQUE indexes are implicit)
    "fact_order_lines": [("product_id",), ("seller_id",), ("customer_unique_id",), ("purchase_date",)],
    "fact_orders": [("customer_unique_id", "order_purchase_timestamp"), ("purchase_date",)],
    "fact_payments": [("customer_unique_id",), ("purchase_date",)],
    "fact_reviews": [("order_id",), ("customer_unique_id",), ("purchase_date",)],
}

# Load order follows the foreign keys (section 2).
LOAD_ORDER = ("dim_dates", "dim_customers", "dim_products", "dim_sellers", "fact_orders", "fact_order_lines",
              "fact_payments", "fact_reviews")

# load_run (Q1): lineage metadata of a warehouse load; one row per committed load. A load is a single
# transaction (W-13), so a failed load leaves no row and needs no status column.
LOAD_RUN_COLUMNS = (
    ("load_id", "text", False),
    ("gold_run_id", "text", False),
    ("gold_manifest_path", "text", False),
    ("gold_manifest_sha256", "text", False),
    ("row_counts", "jsonb", False),       # {table: rows}
    ("content_hashes", "jsonb", False),   # {table: Gold content_sha256}
    ("started_at", "timestamp with time zone", False),   # load-process clock (UTC-aware), not source event time
    ("finished_at", "timestamp with time zone", False),
)


# ----------------------------------------------------------------------------- type mapping (section 3)
def pg_type(column: str, gold_dtype: str) -> str:
    """Expected PostgreSQL ``format_type`` string for a Gold column."""
    if column in MONEY_COLUMNS:
        return "numeric(12,2)"
    if gold_dtype == "datetime64[us]":
        return "date" if column in DATE_KEY_COLUMNS else "timestamp(0) without time zone"
    return {"string": "text", "category": "text", "bool": "boolean", "boolean": "boolean", "int64": "integer",
            "Int64": "integer", "float64": "double precision", "object": "integer[]"}[gold_dtype]


_SA_TYPES = {"text": Text, "numeric(12,2)": lambda: Numeric(12, 2), "date": Date,
             "timestamp(0) without time zone": lambda: TIMESTAMP(timezone=False, precision=0),
             "timestamp with time zone": lambda: TIMESTAMP(timezone=True), "boolean": Boolean,
             "integer": Integer, "double precision": DOUBLE_PRECISION, "integer[]": lambda: ARRAY(Integer),
             "jsonb": JSONB}


def _sa_type(pg: str):
    t = _SA_TYPES[pg]
    return t() if callable(t) else t


@dataclass(frozen=True)
class ExpectedTable:
    name: str
    columns: tuple[tuple[str, str, bool], ...]          # (name, format_type, nullable)
    primary_key: tuple[str, ...]
    foreign_keys: tuple[tuple[str, str, str], ...]       # (column, parent table, parent column)
    unique: tuple[tuple[str, ...], ...]
    checks: tuple[tuple[str, str], ...]
    indexes: tuple[tuple[str, ...], ...]


def expected_tables() -> dict[str, ExpectedTable]:
    out = {}
    for name in LOAD_ORDER:
        spec = TABLES[name]
        cols = tuple((c, pg_type(c, d), c in NULLABLE[name]) for c, d in spec.columns)
        out[name] = ExpectedTable(name, cols, spec.primary_key, spec.foreign_keys,
                                  tuple(c for _, c in UNIQUE.get(name, [])),
                                  tuple(CHECKS.get(name, [])), tuple(INDEXES.get(name, [])))
    out[LOAD_RUN] = ExpectedTable(LOAD_RUN, LOAD_RUN_COLUMNS, ("load_id",), (), (), (), ())
    return out


def constraint_name(kind: str, table: str, suffix: str = "") -> str:
    return f"{kind}_{table}" + (f"_{suffix}" if suffix else "")


def build_metadata(schema: str = WAREHOUSE_SCHEMA) -> MetaData:
    """SQLAlchemy Core definitions of the nine warehouse tables in ``schema``."""
    md = MetaData(schema=schema)
    for t in expected_tables().values():
        items = [Column(c, _sa_type(pg), nullable=nullable) for c, pg, nullable in t.columns]
        items.append(PrimaryKeyConstraint(*t.primary_key, name=constraint_name("pk", t.name)))
        for col, parent, pcol in t.foreign_keys:
            items.append(ForeignKeyConstraint([col], [f"{schema}.{parent}.{pcol}"],
                                              name=constraint_name("fk", t.name, col)))
        for (suffix, cols) in UNIQUE.get(t.name, []):
            items.append(UniqueConstraint(*cols, name=constraint_name("uq", t.name, suffix)))
        for suffix, expr in t.checks:
            items.append(CheckConstraint(expr, name=constraint_name("ck", t.name, suffix)))
        table = Table(t.name, md, *items)
        for cols in t.indexes:
            Index(constraint_name("ix", t.name, "_".join(cols)), *[table.c[c] for c in cols])
    return md


# ----------------------------------------------------------------------------- create
def create_warehouse(engine: Engine | None = None, warehouse_schema: str = WAREHOUSE_SCHEMA,
                     analytics_schema: str = ANALYTICS_SCHEMA) -> dict[str, list[str]]:
    """Create the schemas and missing tables in one transaction. Returns what was created."""
    own = engine is None
    engine = engine or get_engine()
    md = build_metadata(warehouse_schema)
    try:
        with engine.begin() as conn:  # commit on success, roll back everything on any error
            existing_schemas = set(conn.execute(text("SELECT nspname FROM pg_namespace")).scalars())
            created_schemas = []
            for s in (warehouse_schema, analytics_schema):
                if s not in existing_schemas:
                    conn.execute(text(f"CREATE SCHEMA {conn.dialect.identifier_preparer.quote(s)}"))
                    created_schemas.append(s)
            existing = set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                                        {"s": warehouse_schema}).scalars())
            md.create_all(conn, checkfirst=True)  # creates missing tables (+ their indexes) in FK order
        return {"schemas_created": created_schemas,
                "tables_created": [t.name for t in md.sorted_tables if t.name not in existing]}
    finally:
        if own:
            engine.dispose()


# ----------------------------------------------------------------------------- verify
def _catalog(conn: Connection, schema: str) -> dict:
    q = lambda sql, **kw: conn.execute(text(sql), {"s": schema, **kw}).all()  # noqa: E731
    cols = q("""SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull
                FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY c.relname, a.attnum""")
    cons = q("""SELECT c.relname, k.conname, k.contype,
                       ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i)
                             JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n ORDER BY u.i),
                       f.relname,
                       ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY u(n, i)
                             JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n ORDER BY u.i),
                       pg_get_constraintdef(k.oid), k.convalidated
                FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace LEFT JOIN pg_class f ON f.oid = k.confrelid
                WHERE n.nspname = :s""")
    idx = q("""SELECT t.relname, i.relname, ix.indisunique, ix.indisprimary,
                      ARRAY(SELECT a.attname FROM unnest(ix.indkey) WITH ORDINALITY u(n, o)
                            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.n ORDER BY u.o)
               FROM pg_index ix JOIN pg_class i ON i.oid = ix.indexrelid JOIN pg_class t ON t.oid = ix.indrelid
               JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = :s""")
    return {"columns": cols, "constraints": cons, "indexes": idx}


def verify_warehouse(engine: Engine | None = None, warehouse_schema: str = WAREHOUSE_SCHEMA,
                     analytics_schema: str = ANALYTICS_SCHEMA, expect_empty: bool = True) -> dict:
    """Compare the live database with the design. ``problems`` is empty when everything matches."""
    own = engine is None
    engine = engine or get_engine()
    problems: list[str] = []
    try:
        with engine.connect() as conn:
            user = conn.execute(text("SELECT current_user")).scalar()
            owners = dict(conn.execute(text("SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace "
                                            "WHERE nspname IN (:w, :a)"), {"w": warehouse_schema,
                                                                           "a": analytics_schema}).all())
            for s in (warehouse_schema, analytics_schema):
                if owners.get(s) != user:
                    problems.append(f"schema {s}: owner {owners.get(s)!r}, expected {user!r}")
            n_analytics = conn.execute(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                            "WHERE n.nspname = :a"), {"a": analytics_schema}).scalar()
            if n_analytics:
                problems.append(f"schema {analytics_schema}: expected empty, has {n_analytics} relation(s)")
            cat = _catalog(conn, warehouse_schema)
            table_owner = dict(conn.execute(text("SELECT tablename, tableowner FROM pg_tables WHERE schemaname = :s"),
                                            {"s": warehouse_schema}).all())
            privs = {p: conn.execute(text("SELECT has_schema_privilege(:s, :p)"), {"s": warehouse_schema, "p": p}).scalar()
                     for p in ("USAGE", "CREATE")}
            rows = {}
            expected = expected_tables()
            prep = conn.dialect.identifier_preparer
            for name in expected:
                if name in table_owner:
                    rows[name] = conn.execute(text(f"SELECT count(*) FROM {prep.quote(warehouse_schema)}."
                                                   f"{prep.quote(name)}")).scalar()
    finally:
        if own:
            engine.dispose()

    if not all(privs.values()):
        problems.append(f"schema {warehouse_schema}: privileges {privs}")
    actual_tables = set(table_owner)
    for missing in sorted(set(expected) - actual_tables):
        problems.append(f"table {missing}: missing")
    for extra in sorted(actual_tables - set(expected)):
        problems.append(f"table {extra}: not in the design")
    for name, owner in table_owner.items():
        if owner != user:
            problems.append(f"table {name}: owner {owner!r}, expected {user!r}")

    cols = {}
    for t, c, typ, nullable in cat["columns"]:
        cols.setdefault(t, []).append((c, typ, nullable))
    cons = {}
    for t, cname, ctype, ccols, ftable, fcols, cdef, validated in cat["constraints"]:
        cons.setdefault(t, []).append((cname, ctype, tuple(ccols), ftable, tuple(fcols or ()), cdef, validated))
    idx = {}
    for t, iname, uniq, prim, icols in cat["indexes"]:
        idx.setdefault(t, []).append((iname, uniq, prim, tuple(icols)))

    for name, e in expected.items():
        if name not in actual_tables:
            continue
        if cols.get(name, []) != list(e.columns):
            exp, act = list(e.columns), cols.get(name, [])
            diffs = [f"{x} != {y}" for x, y in zip(exp, act) if x != y][:5]
            problems.append(f"table {name}: columns differ ({len(exp)} expected, {len(act)} actual) {diffs}")
        tc = cons.get(name, [])
        pk = [c for c in tc if c[1] == "p"]
        if len(pk) != 1 or pk[0][2] != e.primary_key:
            problems.append(f"table {name}: primary key {[c[2] for c in pk]}, expected {e.primary_key}")
        fks = {(c[2], c[3], c[4]) for c in tc if c[1] == "f"}
        exp_fks = {((col,), parent, (pcol,)) for col, parent, pcol in e.foreign_keys}
        if fks != exp_fks:
            problems.append(f"table {name}: foreign keys differ (missing {exp_fks - fks}, extra {fks - exp_fks})")
        uqs = {c[2] for c in tc if c[1] == "u"}
        if uqs != set(e.unique):
            problems.append(f"table {name}: unique constraints {uqs}, expected {set(e.unique)}")
        cks = {c[0] for c in tc if c[1] == "c"}
        exp_cks = {constraint_name("ck", name, s) for s, _ in e.checks}
        if cks != exp_cks:
            problems.append(f"table {name}: check constraints {cks}, expected {exp_cks}")
        if any(not c[6] for c in tc):
            problems.append(f"table {name}: some constraints are NOT VALID")
        plain = {i[3] for i in idx.get(name, []) if not i[1] and not i[2]}
        if plain != set(e.indexes):
            problems.append(f"table {name}: indexes {plain}, expected {set(e.indexes)}")
        if expect_empty and rows.get(name):
            problems.append(f"table {name}: expected empty, has {rows[name]} rows")

    summary = {
        "schemas": {s: owners.get(s) for s in (warehouse_schema, analytics_schema)},
        "schema_privileges": privs, "analytics_relations": n_analytics,
        "tables": len(actual_tables & set(expected)),
        "columns": sum(len(cols.get(n, [])) for n in expected),
        "primary_keys": sum(1 for v in cons.values() for c in v if c[1] == "p"),
        "foreign_keys": sum(1 for v in cons.values() for c in v if c[1] == "f"),
        "unique_constraints": sum(1 for v in cons.values() for c in v if c[1] == "u"),
        "check_constraints": sum(1 for v in cons.values() for c in v if c[1] == "c"),
        "indexes_non_key": sum(1 for v in idx.values() for i in v if not i[1] and not i[2]),
        "rows": rows,
    }
    return {"ok": not problems, "problems": problems, "summary": summary}
