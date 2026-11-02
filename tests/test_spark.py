"""Tests for the PySpark workloads (Phase 6).

The workload tests need PySpark and Java: run them in the Spark environment (WSL2),
``~/venvs/retail-spark/bin/python -m unittest tests.test_spark``; elsewhere they are skipped. They use
tiny hand-made inputs to pin the business rules; correctness on the real data is shown by
``python -m src.spark.reconcile`` against PostgreSQL. The comparison test runs in the project environment.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import unittest
from decimal import Decimal

HAS_SPARK = importlib.util.find_spec("pyspark") is not None
HAS_PROJECT_ENV = importlib.util.find_spec("sqlalchemy") is not None   # reconcile runs in the project env


@unittest.skipUnless(HAS_PROJECT_ENV, "project environment (sqlalchemy) not available")
class CompareTests(unittest.TestCase):
    def test_exact_tolerance_and_missing_keys(self):
        from src.spark.reconcile import compare
        self.assertTrue(compare("x", {("a",): (1, 2.004)}, {("a",): (1, Decimal("2.00"))}, (None, Decimal("0.005")))["ok"])
        self.assertFalse(compare("x", {("a",): (1, 2.006)}, {("a",): (1, Decimal("2.00"))}, (None, Decimal("0.005")))["ok"])
        self.assertFalse(compare("x", {("a",): (Decimal("1.00"),)}, {("a",): (Decimal("1.01"),)})["ok"])
        self.assertFalse(compare("x", {}, {("a",): (1,)})["ok"])


@unittest.skipUnless(HAS_SPARK, "PySpark not installed (run in the Spark environment)")
class SparkWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.spark import jobs
        cls.jobs = jobs
        cls.spark = jobs.build_session("retail-intelligence-tests")
        d1, d2 = dt.datetime(2018, 1, 5), dt.datetime(2018, 2, 3)
        cls.dates = cls.spark.createDataFrame(
            [(d1, "2018-01", True), (d2, "2018-02", True)], "date timestamp_ntz, year_month string, in_candidate_window boolean")
        cls.d1, cls.d2 = d1, d2

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_category_sales_counts_a_multi_category_order_in_each_category_without_fan_out(self):
        lines = self.spark.createDataFrame(
            [("o1", "p1", 2, 10.005, 1.0, "delivered", self.d1),   # 10.005 -> 10.01 (cent rounding, loader rule)
             ("o1", "p2", 1, 5.0, 2.0, "delivered", self.d1),
             ("o2", "p1", 1, 7.5, 0.5, "canceled", self.d2)],
            "order_id string, product_id string, quantity long, line_price_total double, freight_total double, "
            "order_status string, purchase_date timestamp_ntz")
        products = self.spark.createDataFrame([("p1", "toys"), ("p2", "books")], "product_id string, category_reporting string")
        rows = {(r.year_month, r.category_reporting): r for r in
                self.jobs.category_sales_monthly(lines, products, self.dates).collect()}
        self.assertEqual(sum(r.n_order_lines for r in rows.values()), 3)                      # no line lost or duplicated
        self.assertEqual(sum(r.items_price_total for r in rows.values()), Decimal("22.51"))
        self.assertEqual((rows[("2018-01", "toys")].n_orders, rows[("2018-01", "books")].n_orders), (1, 1))
        self.assertEqual((rows[("2018-02", "toys")].n_orders_delivered, rows[("2018-02", "toys")].units_delivered), (0, 0))

    def test_delivery_uses_the_d06_population_and_survives_months_without_eligible_orders(self):
        cols = ("order_status string, order_delivered_customer_date timestamp_ntz, purchase_year_month string, "
                "dq_delivered_missing_delivery_date boolean, dq_status_canceled_with_date boolean, is_late boolean, "
                "purchase_to_delivery_days double, carrier_to_customer_days double, approval_hours double, "
                "delivery_vs_estimate_days double")
        orders = self.spark.createDataFrame([
            ("delivered", self.d1, "2018-01", False, False, True, 10.0, 8.0, 1.0, 2.0),     # eligible, late
            ("delivered", self.d1, "2018-01", False, False, False, 4.0, None, 2.0, -3.0),   # eligible, no valid transit
            ("delivered", None, "2018-01", True, False, None, None, None, 5.0, None),       # Q-02: not eligible
            ("canceled", self.d1, "2018-01", False, True, False, 99.0, 99.0, 99.0, 99.0),   # Q-01: not eligible
            ("shipped", None, "2018-02", False, False, None, None, None, 3.0, None),        # no eligible order
        ], cols)
        r = {x.purchase_year_month: x for x in self.jobs.delivery_monthly(self.spark, orders).collect()}
        jan = r["2018-01"]
        self.assertEqual((jan.n_orders, jan.n_orders_delivered, jan.n_delivery_eligible, jan.n_late), (4, 3, 2, 1))
        self.assertEqual((jan.n_delivered_missing_date, jan.n_canceled_with_delivery_date, jan.n_eligible_without_valid_transit), (1, 1, 1))
        self.assertEqual((jan.late_rate, jan.avg_purchase_to_delivery_days, jan.median_purchase_to_delivery_days), (0.5, 7.0, 7.0))
        self.assertEqual((jan.avg_carrier_to_customer_days, jan.avg_approval_hours), (8.0, 1.5))   # eligible only
        self.assertIsNone(r["2018-02"].late_rate)                                                 # no division error

    def test_payments_keep_every_transaction_and_exclude_zero_instalments_from_the_average(self):
        payments = self.spark.createDataFrame([
            ("o1", "credit_card", 3, 30.0, False, False, True, self.d1),
            ("o1", "voucher", 1, 0.0, True, False, True, self.d1),          # zero value: kept
            ("o2", "credit_card", 0, 10.0, False, True, True, self.d1),     # zero instalments: kept, not averaged
            ("o3", "credit_card", 2, 20.0, False, False, True, self.d1),
        ], "order_id string, payment_type string, payment_installments long, payment_value double, "
           "dq_payment_zero boolean, dq_installments_zero boolean, in_candidate_window boolean, purchase_date timestamp_ntz")
        r = {x.payment_type: x for x in self.jobs.payment_methods_monthly(payments, self.dates).collect()}
        cc = r["credit_card"]
        self.assertEqual((cc.n_transactions, cc.n_orders, cc.payment_value_total), (3, 3, Decimal("60.00")))
        self.assertEqual((cc.avg_installments, cc.n_zero_installment_transactions), (Decimal("2.50"), 1))
        self.assertEqual((r["voucher"].n_zero_value_transactions, r["voucher"].payment_value_share_pct), (1, Decimal("0.0000")))
        self.assertEqual(cc.payment_value_share_pct, Decimal("100.0000"))


if __name__ == "__main__":
    unittest.main()
