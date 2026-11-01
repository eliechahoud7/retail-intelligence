"""Tests for the PostgreSQL analytics views (src/database/views.py, sql/views/*.sql; Task 5.2.5).

All database tests run in throwaway schemas (the real warehouse/analytics schemas are never
touched) and are skipped if PostgreSQL is not configured or reachable.
"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from sqlalchemy import text

from src.database import views
from src.database.loader import load_warehouse
from tests.test_loader import GoldFixture, throwaway_warehouse
from tests.test_warehouse import DB, dummy_row

EXPECTED_VIEWS = ["sales_daily", "product_performance", "category_performance", "customer_performance",
                  "seller_performance", "delivery_monthly", "delivery_by_state"]


class ViewDefinitionTests(unittest.TestCase):
    def test_files_define_the_expected_views_idempotently_with_comments(self):
        self.assertEqual(views.view_names(), EXPECTED_VIEWS)
        for path, name in zip(views.view_files(), EXPECTED_VIEWS):
            sql = path.read_text(encoding="utf-8")
            self.assertIn(f"CREATE OR REPLACE VIEW {{a}}.{name} AS", sql, path.name)
            self.assertIn(f"COMMENT ON VIEW {{a}}.{name} IS", sql, path.name)
            code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
            self.assertNotRegex(code, r"(?i)\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT|MATERIALIZED)\b", path.name)
            self.assertNotRegex(code, r"\b(warehouse|analytics)\.", "schemas must come from {w}/{a} placeholders")

    def test_no_view_joins_order_lines_and_payments_at_row_level(self):
        for path in views.view_files():
            sql = path.read_text(encoding="utf-8")
            for stmt in re.split(r"\),\s*\w+ AS \(", sql):  # each CTE body separately
                self.assertFalse("fact_order_lines" in stmt and "fact_payments" in stmt, path.name)


def insert(conn, schema, table, **values):
    row = dummy_row(table, **values)
    cols = ", ".join(f'"{c}"' for c in row)
    conn.execute(text(f'INSERT INTO "{schema}"."{table}" ({cols}) VALUES ({", ".join(":" + c for c in row)})'), row)


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class ViewCorrectnessTests(unittest.TestCase):
    """Hand-built data with a fan-out trap: order o1 has 2 lines and 2 payments."""

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def build(self, w):
        d1, d2, d3 = dt.date(2017, 3, 1), dt.date(2017, 3, 2), dt.date(2017, 3, 3)
        D = Decimal
        with self.engine.begin() as c:
            for d in (d1, d2, d3):
                insert(c, w, "dim_dates", date=d)
            insert(c, w, "dim_customers", customer_unique_id="pA", n_orders=2, n_items=4, items_price_total=D("35.00"),
                   items_freight_total=D("6.00"), payment_total=D("40.00"), n_orders_delivered=1,
                   first_purchase_ts=dt.datetime(2017, 3, 1, 10), last_purchase_ts=dt.datetime(2017, 3, 3, 10))
            insert(c, w, "dim_products", product_id="p1", category_reporting="cat_a")
            insert(c, w, "dim_products", product_id="p2", category_reporting="cat_b")
            insert(c, w, "dim_products", product_id="p3", category_reporting="cat_b")  # never sold
            insert(c, w, "dim_sellers", seller_id="s1")
            insert(c, w, "dim_sellers", seller_id="s2")
            common = dict(customer_unique_id="pA", purchase_year_month="2017-03", customer_state_std="SP")
            insert(c, w, "fact_orders", order_id="o1", customer_id="c1", order_status="delivered", purchase_date=d1,
                   order_delivered_customer_date=dt.datetime(2017, 3, 9), purchase_to_delivery_days=8.0,
                   carrier_to_customer_days=5.0, is_late=True, has_items=True, has_payment=True, **common)
            insert(c, w, "fact_orders", order_id="o2", customer_id="c2", order_status="canceled", purchase_date=d3,
                   order_delivered_customer_date=dt.datetime(2017, 3, 8), dq_status_canceled_with_date=True,
                   purchase_to_delivery_days=5.0, is_late=False, has_items=True, has_payment=True, **common)
            line = dict(customer_unique_id="pA")
            insert(c, w, "fact_order_lines", order_line_id="o1:1", order_id="o1", product_id="p1", seller_id="s1",
                   quantity=2, unit_price=D("10.00"), line_price_total=D("20.00"), freight_total=D("4.00"),
                   order_item_ids=[1, 2], order_status="delivered", purchase_date=d1, **line)
            insert(c, w, "fact_order_lines", order_line_id="o1:3", order_id="o1", product_id="p2", seller_id="s2",
                   quantity=1, unit_price=D("5.00"), line_price_total=D("5.00"), freight_total=D("1.00"),
                   order_item_ids=[3], order_status="delivered", purchase_date=d1, **line)
            insert(c, w, "fact_order_lines", order_line_id="o2:1", order_id="o2", product_id="p1", seller_id="s1",
                   quantity=1, unit_price=D("10.00"), line_price_total=D("10.00"), freight_total=D("1.00"),
                   order_item_ids=[1], order_status="canceled", purchase_date=d3, **line)
            insert(c, w, "fact_payments", order_id="o1", payment_sequential=1, payment_value=D("15.00"),
                   order_status="delivered", purchase_date=d1, customer_unique_id="pA")
            insert(c, w, "fact_payments", order_id="o1", payment_sequential=2, payment_value=D("15.00"),
                   order_status="delivered", purchase_date=d1, customer_unique_id="pA")
            insert(c, w, "fact_payments", order_id="o2", payment_sequential=1, payment_value=D("10.00"),
                   order_status="canceled", purchase_date=d3, customer_unique_id="pA")
        return d1, d2, d3

    def q(self, sql, **kw):
        with self.engine.connect() as c:
            return c.execute(text(sql), kw).mappings().all()

    def test_aggregations_are_exact_without_fan_out(self):
        with throwaway_warehouse(self.engine) as w:
            a = w.replace("test_wh_", "test_an_")
            d1, d2, d3 = self.build(w)
            views.create_views(self.engine, w, a)
            daily = {r["purchase_date"]: r for r in self.q(f'SELECT * FROM "{a}".sales_daily')}
            self.assertEqual(set(daily), {d1, d2, d3})                       # gap day d2 present
            r1 = daily[d1]                                                   # naive join would give 50 / 60
            self.assertEqual((r1["n_orders"], r1["units"], r1["items_price_total"], r1["items_freight_total"],
                              r1["n_payments"], r1["payment_total"]), (1, 3, Decimal("25.00"), Decimal("5.00"), 2, Decimal("30.00")))
            self.assertEqual((daily[d2]["n_orders"], daily[d2]["items_price_total"], daily[d2]["payment_total"]), (0, 0, 0))
            self.assertEqual((daily[d3]["n_orders_canceled"], daily[d3]["items_price_total_delivered"]), (1, 0))

            prod = {r["product_id"]: r for r in self.q(f'SELECT * FROM "{a}".product_performance')}
            self.assertEqual((prod["p1"]["units"], prod["p1"]["n_orders"], prod["p1"]["units_delivered"],
                              prod["p1"]["items_price_total"], prod["p1"]["avg_unit_price"]), (3, 2, 2, Decimal("30.00"), Decimal("10.00")))
            self.assertEqual((prod["p3"]["units"], prod["p3"]["avg_unit_price"]), (0, None))    # never sold

            cat = {r["category_reporting"]: r for r in self.q(f'SELECT * FROM "{a}".category_performance')}
            self.assertEqual((cat["cat_b"]["n_products"], cat["cat_b"]["n_products_sold"], cat["cat_b"]["units"]), (2, 1, 1))

            sel = {r["seller_id"]: r for r in self.q(f'SELECT * FROM "{a}".seller_performance')}
            self.assertEqual((sel["s1"]["units"], sel["s1"]["n_orders"], sel["s2"]["items_price_total"]), (3, 2, Decimal("5.00")))

            cust = self.q(f'SELECT * FROM "{a}".customer_performance')[0]
            self.assertEqual((cust["n_orders"], cust["is_repeat_customer"], cust["n_orders_with_items"],
                              cust["avg_items_price_per_order"], cust["avg_payment_per_order"],
                              cust["days_first_to_last_purchase"]), (2, True, 2, Decimal("17.50"), Decimal("20.00"), Decimal("2.00")))

            dm = self.q(f'SELECT * FROM "{a}".delivery_monthly')[0]          # o2 is canceled: not eligible
            self.assertEqual((dm["n_orders"], dm["n_delivery_eligible"], dm["n_canceled_with_delivery_date"], dm["n_late"],
                              dm["late_rate"], dm["avg_purchase_to_delivery_days"]), (2, 1, 1, 1, Decimal("1.0000"), Decimal("8.00")))
            ds = self.q(f'SELECT * FROM "{a}".delivery_by_state')[0]
            self.assertEqual((ds["customer_state_std"], ds["n_delivery_eligible"]), ("SP", 1))

            result = views.verify_views(self.engine, w, a)
            self.assertTrue(result["ok"], result["problems"])

    def test_failed_creation_creates_no_view(self):
        with throwaway_warehouse(self.engine) as w, tempfile.TemporaryDirectory() as tmp:
            a = w.replace("test_wh_", "test_an_")
            bad = Path(tmp) / "99_broken.sql"
            bad.write_text("CREATE OR REPLACE VIEW {a}.broken AS SELECT no_such_column FROM {w}.fact_orders;", encoding="utf-8")
            with mock.patch.object(views, "view_files", lambda: [*sorted(views.SQL_DIR.glob("[0-9][0-9]_*.sql")), bad]):
                with self.assertRaises(Exception):
                    views.create_views(self.engine, w, a)
            self.assertEqual(self.q("SELECT count(*) AS n FROM pg_views WHERE schemaname = :a", a=a)[0]["n"], 0)


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class ViewsOnGoldFixtureTests(GoldFixture):
    def test_views_reconcile_with_loaded_gold_and_rerun_is_safe(self):
        from src.database import get_engine
        engine = get_engine()
        try:
            with throwaway_warehouse(engine) as w:
                a = w.replace("test_wh_", "test_an_")
                load_warehouse(self.gold(), engine, schema=w, check_baselines=False)
                with engine.connect() as c:
                    before = c.execute(text(f'SELECT count(*), max(load_id) FROM "{w}".load_run')).one()
                for _ in range(2):                                   # second run = CREATE OR REPLACE again
                    self.assertEqual(views.create_views(engine, w, a), EXPECTED_VIEWS)
                    result = views.verify_views(engine, w, a)
                    self.assertTrue(result["ok"], result["problems"])
                with engine.connect() as c:
                    self.assertEqual(c.execute(text(f'SELECT count(*), max(load_id) FROM "{w}".load_run')).one(), before)
                self.assertEqual(len(result["checks"]), 12)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
