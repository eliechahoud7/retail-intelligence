"""Tests for the PostgreSQL warehouse schema (src/database/warehouse.py, Task 5.2.3).

* Unit tests check the generated design against the Gold contract (no database needed).
* Database tests run against the configured PostgreSQL as the application role, inside
  randomly named throwaway schemas that are dropped afterwards; the real ``warehouse`` and
  ``analytics`` schemas are never touched. They are skipped if no connection is configured.
"""

from __future__ import annotations

import datetime as dt
import unittest
import uuid
from decimal import Decimal
from unittest import mock

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.schema import CreateTable

from src.analytical.pipeline import DEFAULT_ANALYTICAL_DIR
from src.analytical.schema import TABLES
from src.database import warehouse as wh


def _db_available() -> bool:
    try:
        from src.database import check_connection
        check_connection()
        return True
    except Exception:  # missing .env, server down, wrong password: skip, never fail
        return False


DB = _db_available()


# --------------------------------------------------------------------------- design (no database)
class DesignTests(unittest.TestCase):
    def test_tables_and_columns_mirror_gold_exactly(self):
        exp = wh.expected_tables()
        self.assertEqual(set(exp), set(TABLES) | {wh.LOAD_RUN})
        for name, spec in TABLES.items():
            self.assertEqual([c for c, _, _ in exp[name].columns], [c for c, _ in spec.columns], name)
            self.assertEqual(exp[name].primary_key, spec.primary_key, name)
            self.assertEqual(exp[name].foreign_keys, spec.foreign_keys, name)
        self.assertEqual(sum(len(exp[n].columns) for n in TABLES), 167)

    def test_design_lists_reference_real_columns(self):
        gold = {n: {c for c, _ in s.columns} for n, s in TABLES.items()}
        all_cols = set().union(*gold.values())
        self.assertLessEqual(wh.MONEY_COLUMNS, all_cols)
        for registry in (wh.NULLABLE, wh.INDEXES):
            for t, items in registry.items():
                for item in items:
                    self.assertLessEqual(set(item) if isinstance(item, tuple) else {item}, gold[t], (t, item))
        for t, items in wh.UNIQUE.items():
            for _, cols in items:
                self.assertLessEqual(set(cols), gold[t])

    def test_types_follow_the_approved_mapping(self):
        exp = {n: {c: t for c, t, _ in e.columns} for n, e in wh.expected_tables().items()}
        self.assertEqual(exp["fact_order_lines"]["unit_price"], "numeric(12,2)")
        self.assertEqual(exp["fact_orders"]["payment_minus_items"], "numeric(12,2)")
        self.assertEqual(exp["fact_orders"]["customer_lat_centroid"], "double precision")
        self.assertEqual(exp["fact_orders"]["purchase_date"], "date")
        self.assertEqual(exp["dim_dates"]["date"], "date")
        self.assertEqual(exp["fact_orders"]["order_estimated_delivery_date"], "timestamp(0) without time zone")  # Q2
        self.assertEqual(exp["fact_reviews"]["review_creation_date"], "timestamp(0) without time zone")
        self.assertEqual(exp["fact_order_lines"]["order_item_ids"], "integer[]")
        self.assertEqual(exp["fact_orders"]["order_status"], "text")
        self.assertEqual(exp["load_run"]["row_counts"], "jsonb")
        # every float64 money-named Gold column is numeric; no other float column is
        for n, spec in TABLES.items():
            for c, d in spec.columns:
                if d == "float64":
                    self.assertEqual(exp[n][c] == "numeric(12,2)", c in wh.MONEY_COLUMNS, (n, c))

    def test_foreign_keys_reference_primary_keys(self):
        exp = wh.expected_tables()
        for e in exp.values():
            for _, parent, pcol in e.foreign_keys:
                self.assertEqual(exp[parent].primary_key, (pcol,))

    def test_ddl_compiles_with_named_constraints(self):
        md = wh.build_metadata("warehouse")
        ddl = str(CreateTable(md.tables["warehouse.fact_order_lines"]).compile(dialect=postgresql.dialect()))
        for fragment in ("NUMERIC(12, 2)", "TIMESTAMP(0) WITHOUT TIME ZONE", "INTEGER[]", "pk_fact_order_lines",
                         "uq_fact_order_lines_grain", "ck_fact_order_lines_line_total",
                         "fk_fact_order_lines_order_id", "REFERENCES warehouse.fact_orders (order_id)"):
            self.assertIn(fragment, ddl)
        self.assertEqual(sum(len(v) for v in wh.CHECKS.values()), 5)   # 4 rules; the zip rule on 2 tables
        self.assertEqual(sum(len(v) for v in wh.INDEXES.values()), 11)

    @unittest.skipUnless((DEFAULT_ANALYTICAL_DIR / "fact_orders.parquet").is_file(), "Gold outputs not present")
    def test_nullability_matches_gold_data(self):
        exp = wh.expected_tables()
        for name in TABLES:
            df = pd.read_parquet(DEFAULT_ANALYTICAL_DIR / f"{name}.parquet")
            with_nulls = {c for c in df.columns if df[c].isna().any()}
            declared = {c for c, _, nullable in exp[name].columns if nullable}
            self.assertEqual(declared, with_nulls, name)
            for c in wh.DATE_KEY_COLUMNS & set(df.columns):
                self.assertTrue((df[c] == df[c].dt.normalize()).all(), (name, c))


# --------------------------------------------------------------------------- database (throwaway schemas)
def dummy_row(table: str, **overrides) -> dict:
    """A type-valid row for ``table`` (values meaningless; used only inside rolled-back transactions)."""
    vals = {"text": "x", "integer": 1, "numeric(12,2)": Decimal("1.00"), "double precision": 1.0, "boolean": False,
            "date": dt.date(2017, 1, 1), "timestamp(0) without time zone": dt.datetime(2017, 1, 1),
            "integer[]": [1], "jsonb": "{}", "timestamp with time zone": dt.datetime(2017, 1, 1, tzinfo=dt.timezone.utc)}
    row = {c: vals[t] for c, t, _ in wh.expected_tables()[table].columns}
    for c in row:
        if c.endswith("zip_code_prefix"):
            row[c] = "01001"
    row.update(overrides)
    return row


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class WarehouseDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        tag = uuid.uuid4().hex[:8]
        cls.wh_schema, cls.an_schema = f"test_wh_{tag}", f"test_an_{tag}"
        cls.first = wh.create_warehouse(cls.engine, cls.wh_schema, cls.an_schema)

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as conn:
            for s in (cls.wh_schema, cls.an_schema):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{s}" CASCADE'))
        cls.engine.dispose()

    def verify(self):
        return wh.verify_warehouse(self.engine, self.wh_schema, self.an_schema)

    def test_first_run_creates_everything_and_matches_design(self):
        self.assertEqual(set(self.first["schemas_created"]), {self.wh_schema, self.an_schema})
        self.assertEqual(len(self.first["tables_created"]), 9)
        result = self.verify()
        self.assertTrue(result["ok"], result["problems"])
        s = result["summary"]
        self.assertEqual((s["tables"], s["columns"], s["primary_keys"], s["foreign_keys"]), (9, 175, 9, 13))
        self.assertEqual((s["unique_constraints"], s["check_constraints"], s["indexes_non_key"]), (2, 5, 11))
        self.assertEqual(s["analytics_relations"], 0)
        self.assertEqual(set(s["rows"].values()), {0})

    def test_second_run_is_a_no_op(self):
        again = wh.create_warehouse(self.engine, self.wh_schema, self.an_schema)
        self.assertEqual(again, {"schemas_created": [], "tables_created": []})
        self.assertTrue(self.verify()["ok"])

    def insert(self, conn, table, row):
        conn.execute(text(f'INSERT INTO "{self.wh_schema}"."{table}" ({", ".join(chr(34) + c + chr(34) for c in row)}) '
                          f'VALUES ({", ".join(":" + c for c in row)})'), row)

    def assert_violation(self, table, row, error_name):
        with self.engine.connect() as conn:
            trans = conn.begin()
            try:
                with self.assertRaises(IntegrityError) as ctx:
                    self.insert(conn, table, row)
                self.assertEqual(type(ctx.exception.orig).__name__, error_name)
            finally:
                trans.rollback()

    def test_application_role_can_write_and_rollback_leaves_tables_empty(self):
        with self.engine.connect() as conn:
            trans = conn.begin()
            self.insert(conn, "dim_dates", dummy_row("dim_dates"))
            self.assertEqual(conn.execute(text(f'SELECT count(*) FROM "{self.wh_schema}".dim_dates')).scalar(), 1)
            trans.rollback()
        self.assertEqual(set(self.verify()["summary"]["rows"].values()), {0})

    def test_constraints_are_enforced(self):
        self.assert_violation("fact_order_lines", dummy_row("fact_order_lines", quantity=0, order_item_ids=[]),
                              "CheckViolation")
        self.assert_violation("fact_order_lines", dummy_row("fact_order_lines", quantity=2, order_item_ids=[1, 2]),
                              "CheckViolation")                   # line_price_total 1.00 != 1.00 * 2
        self.assert_violation("dim_sellers", dummy_row("dim_sellers", seller_zip_code_prefix="1001"), "CheckViolation")
        self.assert_violation("fact_payments", dummy_row("fact_payments"), "ForeignKeyViolation")
        self.assert_violation("dim_products", dummy_row("dim_products", product_id=None), "NotNullViolation")
        self.assert_violation("fact_orders", dummy_row("fact_orders", order_id=None), "NotNullViolation")

    def test_primary_key_rejects_duplicates(self):
        with self.engine.connect() as conn:
            trans = conn.begin()
            try:
                self.insert(conn, "dim_dates", dummy_row("dim_dates"))
                with self.assertRaises(IntegrityError) as ctx:
                    self.insert(conn, "dim_dates", dummy_row("dim_dates"))
                self.assertEqual(type(ctx.exception.orig).__name__, "UniqueViolation")
            finally:
                trans.rollback()

    def test_failed_creation_rolls_back_completely(self):
        tag = uuid.uuid4().hex[:8]
        w, a = f"test_wh_fail_{tag}", f"test_an_fail_{tag}"
        broken = {**wh.CHECKS, "dim_dates": [("broken", "this is not sql")]}
        with mock.patch.dict(wh.CHECKS, broken):
            with self.assertRaises(SQLAlchemyError):
                wh.create_warehouse(self.engine, w, a)
        with self.engine.connect() as conn:
            left = conn.execute(text("SELECT count(*) FROM pg_namespace WHERE nspname IN (:w, :a)"), {"w": w, "a": a}).scalar()
        self.assertEqual(left, 0)


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class WarehouseVerificationScopeTests(unittest.TestCase):
    """verify_warehouse checks the warehouse schema; the analytics schema only for existence and owner."""

    def setUp(self):
        from src.database import get_engine
        self.engine = get_engine()
        tag = uuid.uuid4().hex[:8]
        self.w, self.a = f"test_wh_{tag}", f"test_an_{tag}"
        wh.create_warehouse(self.engine, self.w, self.a)

    def tearDown(self):
        with self.engine.begin() as conn:
            for s in (self.w, self.a):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{s}" CASCADE'))
        self.engine.dispose()

    def problems(self, **kw):
        return wh.verify_warehouse(self.engine, kw.get("w", self.w), kw.get("a", self.a))["problems"]

    def test_empty_analytics_schema_passes(self):
        self.assertEqual(self.problems(), [])

    def test_analytics_schema_with_the_views_passes(self):
        from src.database import views
        views.create_views(self.engine, self.w, self.a)
        result = wh.verify_warehouse(self.engine, self.w, self.a)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["summary"]["analytics_relations"], len(views.view_names()))

    def test_wrong_owner_or_missing_analytics_schema_fails(self):
        # "public" exists but is owned by pg_database_owner, not by the application role
        self.assertTrue(any("schema public: owner" in p for p in self.problems(a="public")))
        self.assertTrue(any("owner None" in p for p in self.problems(a=f"{self.a}_missing")))

    def test_missing_warehouse_objects_still_fail(self):
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP INDEX "{self.w}".ix_fact_orders_purchase_date'))
        self.assertTrue(any("fact_orders: indexes" in p for p in self.problems()))
        with self.engine.begin() as conn:
            conn.execute(text(f'DROP TABLE "{self.w}".fact_reviews'))
        self.assertIn("table fact_reviews: missing", self.problems())


class CliErrorHandlingTests(unittest.TestCase):
    """A psycopg error (raised by SQL on the raw connection) gives a clean message and exit code 1."""

    def run_cli(self, argv, patch_target, error):
        import contextlib
        import io

        import psycopg

        from src.database import __main__ as cli
        from src.database.connection import Settings
        settings = Settings(host="localhost", port=5432, user="retail_app", password="NOT-A-REAL-SECRET",
                            database="retail_intelligence", admin_user="postgres", admin_password="ALSO-NOT-REAL")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                mock.patch.object(cli, patch_target, side_effect=error(psycopg)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_psycopg_error_from_create_views_is_reported_cleanly(self):
        code, out, err = self.run_cli(["create-views"], "create_views",
                                      lambda pg: pg.errors.UndefinedColumn('column "no_such_column" does not exist\nLINE 1'))
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'DATABASE ERROR: UndefinedColumn: column "no_such_column" does not exist')
        self.assertNotIn("Traceback", err)
        for secret in ("NOT-A-REAL-SECRET", "ALSO-NOT-REAL"):
            self.assertNotIn(secret, out + err)

    def test_psycopg_error_from_load_warehouse_is_reported_cleanly(self):
        with mock.patch("src.database.__main__.locate_gold") as locate:
            locate.return_value.run_id, locate.return_value.git = "r", {}
            locate.return_value.row_counts, locate.return_value.frames = {"t": 1}, {"t": None}
            code, _, err = self.run_cli(["load-warehouse"], "load_warehouse",
                                        lambda pg: pg.errors.ForeignKeyViolation("insert violates foreign key"))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("DATABASE ERROR: ForeignKeyViolation: insert violates foreign key"))


if __name__ == "__main__":
    unittest.main()
