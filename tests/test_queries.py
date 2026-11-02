"""Tests for the advanced SQL analytics queries (src/database/queries.py, sql/queries/; Task 5.3.1).

Correctness tests run every query on a small hand-calculable dataset in throwaway schemas (the
real warehouse is never touched); they are skipped if PostgreSQL is not configured or reachable.
"""

from __future__ import annotations

import datetime as dt
import re
import unittest
from decimal import Decimal

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.database import queries, views
from tests.test_loader import throwaway_warehouse
from tests.test_views import insert as _insert
from tests.test_warehouse import DB


def insert(conn, schema, table, **values):
    """tests.test_views.insert, with fact_orders delivery fields kept consistent with Gold.

    The generic dummy row has delivery_vs_estimate_days = 1.0 but is_late = False, which Gold can never
    produce (is_late = delay > 0). Unless a test sets them, orders get delay 0 (matching the dummy
    delivered and estimated dates) and is_late False, so the delivery reconciliation checks hold.
    """
    if table == "fact_orders":
        values.setdefault("delivery_vs_estimate_days", 0.0)
        values.setdefault("is_late", False)
    _insert(conn, schema, table, **values)

D = Decimal


class QueryDefinitionTests(unittest.TestCase):
    def test_every_query_has_a_complete_header_and_unique_name(self):
        qs = queries.load_queries()
        # every .sql file is loaded (none silently skipped by the NN_<name>.sql pattern), in the four groups
        self.assertEqual(len(qs), len(list(queries.QUERY_DIR.glob("*/*.sql"))))
        self.assertEqual({q.group for q in qs}, {"sales", "customers", "products_sellers", "delivery"})
        self.assertEqual(len({q.name for q in qs}), len(qs))
        for q in qs:
            self.assertTrue(q.grain and q.key and q.purpose and q.competencies, q.name)
            self.assertEqual(q.path.stem.split("_", 1)[1], q.name, "file name must match the declared name")

    def test_queries_are_read_only_and_schema_agnostic(self):
        for q in queries.load_queries():
            code = "\n".join(l for l in q.sql.splitlines() if not l.lstrip().startswith("--"))
            self.assertRegex(code.lstrip(), r"(?i)^(WITH|SELECT)\b", q.name)
            self.assertNotRegex(code, r"(?i)\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT)\b", q.name)
            self.assertNotRegex(code, r"\b(warehouse|analytics)\.", f"{q.name}: use {{w}}/{{a}} placeholders")

    def test_no_query_joins_order_lines_and_payments_at_row_level(self):
        for q in queries.load_queries():
            for part in re.split(r"\),\s*\w+ AS \(", q.sql):
                self.assertFalse("fact_order_lines" in part and "fact_payments" in part, q.name)


class SingleStatementSafetyTests(unittest.TestCase):
    """Only ONE statement may be executed read-only: a second one could end the READ ONLY transaction."""

    VALID = ["SELECT 1", "SELECT 1;", "SELECT 1 ;  \n  -- trailing comment ; with a semicolon\n",
             "WITH x AS (SELECT 1 AS a) SELECT a FROM x;",
             "SELECT ';' AS semicolon_in_a_string", "SELECT 'it''s; fine'", "SELECT E'a\\'; b'",
             'SELECT 1 AS "odd;name"', "SELECT $$a; b$$", "SELECT $tag$ ; COMMIT; $tag$",
             "SELECT 1 -- ; COMMIT", "/* ; /* nested ; */ still comment ; */ SELECT 1",
             "SELECT x::date FROM t WHERE d <= CAST(:reference_date AS date);"]
    INVALID = ["SELECT 1; COMMIT", "SELECT 1; COMMIT;", "SELECT 1;\nDELETE FROM warehouse.fact_orders;",
               "SELECT 1; SET TRANSACTION READ WRITE", "SELECT 1; ROLLBACK; DROP TABLE t",
               "SELECT 1;;", "SELECT 1; -- comment\nCOMMIT", "SELECT 1 /* c */ ; /* c */ END",
               "SELECT 'a'; BEGIN", "SELECT $$x$$; COMMIT", 'SELECT 1 AS "a"; COMMIT',
               "SELECT 1\n;\nselect 2",
               "", "   ", ";", "-- only a comment",
               "SELECT 'unterminated", "SELECT 1 /* unterminated", 'SELECT "unterminated', "SELECT $$unterminated"]

    def test_single_statements_are_accepted(self):
        for sql in self.VALID:
            with self.subTest(sql):
                queries.check_single_statement(sql)

    def test_appended_statements_and_malformed_sql_are_rejected(self):
        for sql in self.INVALID:
            with self.subTest(sql), self.assertRaises(queries.UnsafeQueryError):
                queries.check_single_statement(sql)

    def test_every_shipped_query_and_reconciliation_is_one_statement(self):
        for q in queries.load_queries():
            queries.check_single_statement(q.sql)
        for name, checks in queries.RECONCILIATIONS.items():
            for desc, _, sql in checks:
                with self.subTest(f"{name}: {desc}"):
                    queries.check_single_statement(sql)

    def test_read_only_execution_rejects_before_sending_anything(self):
        from unittest import mock
        conn = mock.MagicMock()
        with self.assertRaises(queries.UnsafeQueryError):
            queries._read_only(conn, "SELECT 1; COMMIT; DELETE FROM t")
        conn.begin.assert_not_called()
        conn.execute.assert_not_called()

    def test_a_query_file_with_two_statements_is_not_loaded(self):
        import tempfile
        from pathlib import Path
        header = "-- name: two\n-- grain: g\n-- key: k\n-- purpose: p\n-- competencies: c\n"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "g").mkdir()
            (Path(tmp) / "g" / "01_two.sql").write_text(header + "SELECT 1 AS k; COMMIT;\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "01_two.sql: multiple SQL statements"):
                queries.load_queries(Path(tmp))


class CliErrorHandlingTests(unittest.TestCase):
    """Only an unknown query name is a CONFIG ERROR; any other KeyError is a bug and is not hidden."""

    def main(self, argv, **patches):
        import contextlib
        import io
        from unittest import mock

        from src.database import __main__ as cli
        from src.database.connection import Settings
        settings = Settings(host="localhost", port=5432, user="retail_app", password="NOT-A-REAL-SECRET",
                            database="retail_intelligence")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(cli, "load_settings", return_value=settings))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(cli, name, value))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_unknown_query_name_is_a_config_error(self):
        code, _, err = self.main(["run-query", "--name", "no_such_query"])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("CONFIG ERROR: unknown query 'no_such_query'; available: ["), err)
        with self.assertRaises(KeyError):                                       # still a KeyError for callers
            queries.get_query("no_such_query")

    def test_unrelated_key_errors_are_not_reported_as_config_errors(self):
        from unittest import mock
        bug = mock.Mock(side_effect=KeyError("programming error"))
        for argv, target in ((["run-query", "--name", "sales_monthly_trend"], "run_query"),
                             (["verify-queries"], "verify_queries")):
            with self.subTest(argv[0]), self.assertRaises(KeyError) as ctx:
                self.main(argv, **{target: bug})
            self.assertNotIsInstance(ctx.exception, queries.UnknownQueryError)

    def test_verify_queries_exit_code_and_report(self):
        from unittest import mock
        entry = {"name": "sales_by_weekday", "group": "sales", "rows": 7, "checks": [{}]}
        ok = mock.Mock(return_value={"ok": True, "problems": [], "queries": [entry]})
        code, out, _ = self.main(["verify-queries"], verify_queries=ok)
        self.assertEqual(code, 0)
        self.assertIn("QUERIES: OK", out)
        failed = mock.Mock(return_value={"ok": False, "problems": ["sales_by_weekday: x: check failed with E: boom"],
                                         "queries": [entry]})
        code, out, _ = self.main(["verify-queries"], verify_queries=failed)
        self.assertEqual(code, 1)
        self.assertIn("FAIL sales", out)
        self.assertIn("PROBLEM: sales_by_weekday: x: check failed with E: boom", out)
        self.assertIn("QUERIES: FAILED", out)


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class SalesQueryTests(unittest.TestCase):
    """Jan-Mar 2017: o1 (Jan 5, p1/cat_a 100), o2 + o3 (Feb 10, p1 150 and p2/cat_b 2 x 100), o4 (Mar 15, p2 75)."""

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        with cls.engine.begin() as c:
            day = dt.date(2017, 1, 1)
            while day <= dt.date(2017, 3, 31):
                insert(c, cls.w, "dim_dates", date=day, year=day.year, month=day.month, year_month=f"{day:%Y-%m}",
                       day_of_week=day.isoweekday(), is_weekend=day.isoweekday() >= 6, in_candidate_window=True)
                day += dt.timedelta(days=1)
            insert(c, cls.w, "dim_customers", customer_unique_id="pA")
            insert(c, cls.w, "dim_products", product_id="p1", category_reporting="cat_a")
            insert(c, cls.w, "dim_products", product_id="p2", category_reporting="cat_b")
            insert(c, cls.w, "dim_sellers", seller_id="s1")
            orders = [("o1", dt.date(2017, 1, 5), [("p1", 1, D("100.00"))]),
                      ("o2", dt.date(2017, 2, 10), [("p1", 1, D("150.00"))]),
                      ("o3", dt.date(2017, 2, 10), [("p2", 2, D("100.00"))]),
                      ("o4", dt.date(2017, 3, 15), [("p2", 1, D("75.00"))])]
            for i, (oid, day, lines) in enumerate(orders):
                ts = dt.datetime(day.year, day.month, day.day, 10 + i)  # distinct, consistent purchase timestamps
                ctx = dict(customer_unique_id="pA", order_status="delivered", purchase_date=day, in_candidate_window=True,
                           order_purchase_timestamp=ts)
                order_total = sum(unit * qty for _, qty, unit in lines)
                insert(c, cls.w, "fact_orders", order_id=oid, customer_id=f"c{i}", purchase_year_month=f"{day:%Y-%m}",
                       has_items=True, has_payment=True, items_price_total=order_total, payment_total=order_total, **ctx)
                total = D(0)
                for n, (pid, qty, unit) in enumerate(lines, start=1):
                    insert(c, cls.w, "fact_order_lines", order_line_id=f"{oid}:{n}", order_id=oid, product_id=pid,
                           seller_id="s1", quantity=qty, unit_price=unit, line_price_total=unit * qty,
                           freight_total=D("0.00"), order_item_ids=list(range(1, qty + 1)), **ctx)
                    total += unit * qty
                insert(c, cls.w, "fact_payments", order_id=oid, payment_sequential=1, payment_value=total,
                       order_status="delivered", purchase_date=day, customer_unique_id="pA", in_candidate_window=True)
        views.create_views(cls.engine, cls.w, cls.a)

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def run_q(self, name):
        return queries.run_query(name, self.engine, self.w, self.a)

    def test_monthly_trend_lag_running_total_and_moving_average(self):
        m = self.run_q("sales_monthly_trend").set_index("year_month")
        self.assertEqual(list(m.index), ["2017-01", "2017-02", "2017-03"])
        self.assertEqual(list(m["items_price_total"]), [D("100.00"), D("350.00"), D("75.00")])
        self.assertEqual(list(m["payment_total"]), [D("100.00"), D("350.00"), D("75.00")])
        self.assertIsNone(m.loc["2017-01", "items_price_mom_growth_pct"])           # no previous month
        self.assertEqual(m.loc["2017-02", "items_price_mom_growth_pct"], D("250.00"))
        self.assertEqual(m.loc["2017-03", "items_price_mom_growth_pct"], D("-78.57"))
        self.assertEqual(m.loc["2017-03", "items_price_mom_change"], D("-275.00"))
        self.assertEqual(list(m["items_price_running_total"]), [D("100.00"), D("450.00"), D("525.00")])
        self.assertEqual(list(m["items_price_moving_avg_3m"]), [D("100.00"), D("225.00"), D("175.00")])
        self.assertTrue(m["items_price_yoy_growth_pct"].isna().all())               # no month 12 back
        self.assertEqual(list(m["mom_base_in_window"]), [False, True, True])
        # data runs Jan 5 .. Mar 15: only February is complete
        self.assertEqual(list(m["is_complete_month"]), [False, True, False])
        self.assertEqual(list(m["n_days_in_data"]), [27, 28, 15])

    def test_daily_moving_averages_use_calendar_days(self):
        d = self.run_q("sales_daily_moving_averages").set_index("purchase_date")
        self.assertEqual(len(d), (dt.date(2017, 3, 15) - dt.date(2017, 1, 5)).days + 1)
        first, seventh = d.loc[dt.date(2017, 1, 5)], d.loc[dt.date(2017, 1, 11)]
        self.assertEqual((first["n_orders_ma_7d"], first["ma_7d_complete"]), (D("1.00"), False))
        self.assertEqual((seventh["n_orders_ma_7d"], seventh["ma_7d_complete"]), (D("0.14"), True))  # 1 order / 7 days
        self.assertEqual(d.loc[dt.date(2017, 2, 10), "n_orders"], 2)

    def test_best_and_worst_months_rank_functions(self):
        r = self.run_q("sales_best_worst_months").set_index("year_month")
        self.assertEqual(r["rank_best"].to_dict(), {"2017-02": 1, "2017-01": 2, "2017-03": 3})
        self.assertEqual(r["rank_worst"].to_dict(), {"2017-03": 1, "2017-01": 2, "2017-02": 3})
        self.assertEqual(set(r["position"]), {"best and worst"})                   # only 3 months in the window
        # n_orders: Feb 2, Jan 1, Mar 1 -> DENSE_RANK 1, 2, 2
        self.assertEqual(r["dense_rank_by_orders"].to_dict(), {"2017-02": 1, "2017-01": 2, "2017-03": 2})

    def test_category_ranking_partitions_and_previous_rank(self):
        c = self.run_q("category_monthly_ranking").set_index(["year_month", "category_reporting"])
        self.assertEqual(c.loc[("2017-02", "cat_b"), "rank_in_month"], 1)
        self.assertEqual(c.loc[("2017-02", "cat_a"), "rank_in_month"], 2)
        self.assertEqual(c.loc[("2017-02", "cat_b"), "share_of_month_pct"], D("57.14"))
        self.assertEqual(c.loc[("2017-02", "cat_a"), "rank_prev_month"], 1)        # was 1st in January
        self.assertEqual(c.loc[("2017-02", "cat_a"), "rank_change"], -1)
        self.assertTrue(pd.isna(c.loc[("2017-02", "cat_b"), "rank_prev_month"]))   # NULL: no January sales
        self.assertEqual(c.loc[("2017-03", "cat_b"), "rank_prev_month"], 1)
        self.assertEqual(c.loc[("2017-01", "cat_a"), "share_of_month_pct"], D("100.00"))

    def test_weekday_shares_use_scalar_subqueries(self):
        w = self.run_q("sales_by_weekday").set_index("day_of_week")
        self.assertEqual(len(w), 7)
        self.assertEqual(w.loc[5, "items_price_total"], D("350.00"))               # 2017-02-10 is a Friday
        self.assertEqual(w.loc[5, "share_of_items_price_pct"], D("66.67"))
        self.assertEqual(sum(w["items_price_total"]), D("525.00"))

    def test_verify_queries_passes_and_reconciles(self):
        r = queries.verify_queries(self.engine, self.w, self.a)
        self.assertTrue(r["ok"], r["problems"])
        self.assertTrue(all(c["ok"] for q in r["queries"] for c in q["checks"]))

    def test_a_failing_check_is_reported_without_stopping_verification(self):
        from unittest import mock
        broken = [("raises in Python", lambda df: 1 / 0, "SELECT 0"),
                  ("raises in SQL", lambda df: 0, "SELECT no_such_column FROM {w}.fact_orders"),
                  ("runs after the failures", lambda df: len(df), "SELECT 7")]
        with mock.patch.dict(queries.RECONCILIATIONS, {"sales_by_weekday": broken}):
            r = queries.verify_queries(self.engine, self.w, self.a)
            n_checks = sum(len(v) for v in queries.RECONCILIATIONS.values())
        self.assertFalse(r["ok"])
        weekday = next(q for q in r["queries"] if q["name"] == "sales_by_weekday")
        self.assertEqual([c["ok"] for c in weekday["checks"]], [False, False, True])
        self.assertIn("ZeroDivisionError", weekday["checks"][0]["error"])
        self.assertIn("no_such_column", weekday["checks"][1]["error"])
        self.assertEqual(len(r["problems"]), 2)
        self.assertTrue(all(p.startswith("sales_by_weekday: raises in ") and "check failed with" in p
                            for p in r["problems"]), r["problems"])
        # every query was still executed and every other check still ran and passed
        self.assertEqual(len(r["queries"]), len(queries.load_queries()))
        self.assertEqual(sum(len(q["checks"]) for q in r["queries"]), n_checks)
        self.assertTrue(all(c["ok"] for q in r["queries"] if q["name"] != "sales_by_weekday" for c in q["checks"]))

    def test_read_only_transaction_blocks_writes(self):
        with self.engine.connect() as conn, self.assertRaises(DBAPIError) as ctx:
            queries._read_only(conn, f'DELETE FROM "{self.w}".fact_payments')
        self.assertEqual(type(ctx.exception.orig).__name__, "ReadOnlySqlTransaction")
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text(f'SELECT count(*) FROM "{self.w}".fact_payments')).scalar(), 4)


class QueryParameterTests(unittest.TestCase):
    """Parameters are declared, validated and bound - never formatted into SQL."""

    def setUp(self):
        self.rfm = queries.get_query("customer_rfm")

    def test_declared_parameters(self):
        specs = {p.name: p for p in self.rfm.params}
        self.assertTrue(specs["reference_date"].required)
        self.assertEqual((specs["order_scope"].choices, specs["order_scope"].default), (("delivered", "all"), "delivered"))
        self.assertEqual(self.rfm.bind({"reference_date": "2018-08-29"}),
                         {"reference_date": dt.date(2018, 8, 29), "order_scope": "delivered"})
        self.assertEqual(queries.get_query("sales_monthly_trend").bind(), {})  # parameter-free queries unchanged

    def test_invalid_or_missing_parameters_are_rejected(self):
        with self.assertRaises(queries.QueryParameterError):
            self.rfm.bind({})                                                   # no silent CURRENT_DATE
        for bad in ("2018-13-01", "yesterday", "2018-08-29'; DROP TABLE warehouse.fact_orders; --"):
            with self.assertRaises(queries.QueryParameterError):
                self.rfm.bind({"reference_date": bad})
        with self.assertRaises(queries.QueryParameterError):
            self.rfm.bind({"reference_date": "2018-08-29", "order_scope": "canceled"})
        with self.assertRaises(queries.QueryParameterError):
            self.rfm.bind({"reference_date": "2018-08-29", "limit": "5"})

    def test_cli_reports_parameter_errors_cleanly(self):
        import contextlib
        import io
        from unittest import mock

        from src.database import __main__ as cli
        from src.database.connection import Settings
        settings = Settings(host="localhost", port=5432, user="retail_app", password="NOT-A-REAL-SECRET",
                            database="retail_intelligence")
        err = io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["run-query", "--name", "customer_rfm"]), 1)
            self.assertEqual(cli.main(["run-query", "--name", "customer_rfm", "--param", "reference_date=bad"]), 1)
        self.assertIn("CONFIG ERROR: customer_rfm: required parameter 'reference_date' missing", err.getvalue())
        self.assertIn("is not a valid date", err.getvalue())


class ParetoThresholdTests(unittest.TestCase):
    """0 < a_threshold_pct < b_threshold_pct <= 100, enforced by Query.bind (and so by the CLI)."""

    def setUp(self):
        self.q = queries.get_query("product_sales_pareto")

    def test_valid_pairs(self):
        self.assertEqual(self.q.bind(), {"a_threshold_pct": 80, "b_threshold_pct": 95})          # defaults
        self.assertEqual(self.q.bind({"a_threshold_pct": "80", "b_threshold_pct": "95"}), {"a_threshold_pct": 80, "b_threshold_pct": 95})
        self.assertEqual(self.q.bind({"a_threshold_pct": 1, "b_threshold_pct": 100}), {"a_threshold_pct": 1, "b_threshold_pct": 100})

    def test_invalid_pairs_are_rejected(self):
        invalid = {"negative": (-5, 95), "zero": (0, 95), "equal": (80, 80), "reversed": (95, 80),
                   "above 100": (80, 101), "a alone above default b": (96, None)}
        for label, (a, b) in invalid.items():
            values = {"a_threshold_pct": a} | ({"b_threshold_pct": b} if b is not None else {})
            with self.subTest(label), self.assertRaises(queries.QueryParameterError) as ctx:
                self.q.bind(values)
            self.assertIn("0 < a_threshold_pct < b_threshold_pct <= 100", str(ctx.exception))

    def test_cli_reports_invalid_thresholds_as_config_error(self):
        import contextlib
        import io
        from unittest import mock

        from src.database import __main__ as cli
        from src.database.connection import Settings
        settings = Settings(host="localhost", port=5432, user="retail_app", password="NOT-A-REAL-SECRET",
                            database="retail_intelligence")
        err = io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(["run-query", "--name", "product_sales_pareto",
                             "--param", "a_threshold_pct=95", "--param", "b_threshold_pct=80"])
        self.assertEqual(code, 1)
        self.assertTrue(err.getvalue().startswith("CONFIG ERROR: product_sales_pareto: invalid parameters"))


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class TiedMonthOrderTests(unittest.TestCase):
    """Jan and Feb 2017 both sell 100 (tied best month), Mar sells 50."""

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        with cls.engine.begin() as c:
            day = dt.date(2017, 1, 1)
            while day <= dt.date(2017, 3, 31):
                insert(c, cls.w, "dim_dates", date=day, year_month=f"{day:%Y-%m}", day_of_week=day.isoweekday(),
                       in_candidate_window=True)
                day += dt.timedelta(days=1)
            insert(c, cls.w, "dim_customers", customer_unique_id="pA")
            insert(c, cls.w, "dim_products", product_id="p1")
            insert(c, cls.w, "dim_sellers", seller_id="s1")
            for i, (day, price) in enumerate(((dt.date(2017, 1, 1), D("100.00")), (dt.date(2017, 2, 1), D("100.00")),
                                              (dt.date(2017, 3, 31), D("50.00")))):
                ctx = dict(customer_unique_id="pA", order_status="delivered", purchase_date=day, in_candidate_window=True,
                           order_purchase_timestamp=dt.datetime(day.year, day.month, day.day, 10))
                insert(c, cls.w, "fact_orders", order_id=f"o{i}", customer_id=f"c{i}", purchase_year_month=f"{day:%Y-%m}",
                       has_items=True, items_price_total=price, **ctx)
                insert(c, cls.w, "fact_order_lines", order_line_id=f"o{i}:1", order_id=f"o{i}", product_id="p1",
                       seller_id="s1", quantity=1, unit_price=price, line_price_total=price, freight_total=D("0.00"),
                       order_item_ids=[1], **ctx)
        views.create_views(cls.engine, cls.w, cls.a)

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def test_tied_months_are_ordered_deterministically_without_changing_ranks(self):
        runs = [queries.run_query("sales_best_worst_months", self.engine, self.w, self.a) for _ in range(3)]
        for r in runs:
            self.assertEqual(list(r["year_month"]), ["2017-01", "2017-02", "2017-03"])           # tie broken by month
            self.assertEqual(list(r["rank_best"]), [1, 1, 3])                                     # ranks unchanged
            self.assertEqual(list(r["rank_worst"]), [2, 2, 1])
            self.assertEqual(list(r["row_number_by_sales"]), [1, 2, 3])
        result = queries.verify_queries(self.engine, self.w, self.a)
        best_worst = next(q for q in result["queries"] if q["name"] == "sales_best_worst_months")
        self.assertEqual(len(best_worst["checks"]), 2)
        self.assertTrue(all(c["ok"] for c in best_worst["checks"]), best_worst["checks"])


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class RfmAsOfTests(unittest.TestCase):
    """Reference date 2017-04-30. All orders delivered, with items, purchased at 10:00:
    qA: bought 03-01 (100), delivered 03-10
    qB: bought 04-25 (70),  delivered 05-05  <- purchased before, delivered AFTER the reference date
    qC: bought 02-01 (50),  delivered 02-05; bought 04-28 (80), delivered 05-02 (after the reference date)
    """

    ORDERS = [("a1", "qA", "2017-03-01", "2017-03-10", D("100.00")), ("b1", "qB", "2017-04-25", "2017-05-05", D("70.00")),
              ("c1", "qC", "2017-02-01", "2017-02-05", D("50.00")), ("c2", "qC", "2017-04-28", "2017-05-02", D("80.00"))]

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        with cls.engine.begin() as c:
            day = dt.date(2017, 1, 1)
            while day <= dt.date(2017, 5, 31):
                insert(c, cls.w, "dim_dates", date=day, year_month=f"{day:%Y-%m}")
                day += dt.timedelta(days=1)
            for person in ("qA", "qB", "qC"):
                insert(c, cls.w, "dim_customers", customer_unique_id=person)
            for i, (oid, person, bought, delivered, price) in enumerate(cls.ORDERS):
                b, d = dt.date.fromisoformat(bought), dt.date.fromisoformat(delivered)
                insert(c, cls.w, "fact_orders", order_id=oid, customer_id=f"c{i}", customer_unique_id=person,
                       order_status="delivered", purchase_date=b, purchase_year_month=bought[:7], has_items=True,
                       items_price_total=price, order_purchase_timestamp=dt.datetime(b.year, b.month, b.day, 10),
                       order_delivered_customer_date=dt.datetime(d.year, d.month, d.day, 15))

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def rfm(self, **params):
        return queries.run_query("customer_rfm", self.engine, self.w, self.a,
                                 params={"reference_date": "2017-04-30", **params}).set_index("customer_unique_id")

    def test_delivered_scope_excludes_orders_delivered_after_the_reference_date(self):
        f = self.rfm()
        self.assertEqual(sorted(f.index), ["qA", "qB", "qC"])          # all purchased by the reference date
        self.assertFalse(f.loc["qB", "has_eligible_purchase"])         # its only order was still in transit
        self.assertTrue(all(pd.isna(f.loc["qB", c]) for c in ("recency_days", "frequency", "monetary", "rfm_cell")))
        # qA: 1 order, 100, last 03-01 -> recency 60; qC: only c1 counts -> 1 order, 50, last 02-01 -> recency 88
        self.assertEqual((f.loc["qA", "recency_days"], f.loc["qA", "frequency"], f.loc["qA", "monetary"]), (60, 1, D("100.00")))
        self.assertEqual((f.loc["qC", "recency_days"], f.loc["qC", "frequency"], f.loc["qC", "monetary"]), (88, 1, D("50.00")))
        # 2 eligible persons: R desc (88, 60) -> 1, 5; F tied (1, 1) -> 1, 1; M (50, 100) -> 1, 5
        self.assertEqual((f.loc["qA", "rfm_cell"], f.loc["qC", "rfm_cell"]), ("515", "111"))

    def test_all_scope_applies_the_purchase_date_cutoff_only(self):
        f = self.rfm(order_scope="all")
        self.assertTrue(f["has_eligible_purchase"].all())
        self.assertEqual((f.loc["qB", "recency_days"], f.loc["qB", "frequency"], f.loc["qB", "monetary"]), (5, 1, D("70.00")))
        self.assertEqual((f.loc["qC", "recency_days"], f.loc["qC", "frequency"], f.loc["qC", "monetary"]), (2, 2, D("130.00")))
        self.assertEqual((f.loc["qA", "recency_days"], f.loc["qA", "frequency"], f.loc["qA", "monetary"]), (60, 1, D("100.00")))
        # 3 eligible: R desc (60, 5, 2) -> 1, 3, 5; F (1, 1, 2) -> 1, 1, 5; M (70, 100, 130) -> 1, 3, 5
        self.assertEqual(f["rfm_cell"].to_dict(), {"qA": "113", "qB": "311", "qC": "555"})

    def test_later_reference_date_counts_the_late_deliveries(self):
        f = queries.run_query("customer_rfm", self.engine, self.w, self.a,
                              params={"reference_date": "2017-05-05"}).set_index("customer_unique_id")
        self.assertTrue(f["has_eligible_purchase"].all())                # b1 and c2 delivered by 05-05
        self.assertEqual((f.loc["qC", "frequency"], f.loc["qC", "monetary"]), (2, D("130.00")))


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class RfmEligibilityEdgeTests(unittest.TestCase):
    """Reference date 2017-04-30; one order per person, bought at 10:00, each with order lines unless stated:
    rA delivered, bought 04-20 (100), delivered 04-30 23:59:59  <- delivered ON the reference date (late evening)
    rB delivered, bought 04-10 (60),  NO delivery date          <- status delivered, delivery date missing
    rC delivered, bought 04-20 (40),  delivered 05-01 00:00:00  <- first instant AFTER the reference date
    rD canceled,  bought 04-15 (30),  no delivery date          <- counts only in scope 'all'
    rE delivered, bought 04-01, delivered 04-05, NO items       <- never eligible (items required in both scopes)
    """

    ORDERS = [("ra", "rA", "delivered", "2017-04-20", "2017-04-30 23:59:59", D("100.00")),
              ("rb", "rB", "delivered", "2017-04-10", None, D("60.00")),
              ("rc", "rC", "delivered", "2017-04-20", "2017-05-01 00:00:00", D("40.00")),
              ("rd", "rD", "canceled", "2017-04-15", None, D("30.00")),
              ("re", "rE", "delivered", "2017-04-01", "2017-04-05 10:00:00", None)]

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        with cls.engine.begin() as c:
            day = dt.date(2017, 4, 1)
            while day <= dt.date(2017, 5, 31):
                insert(c, cls.w, "dim_dates", date=day, year_month=f"{day:%Y-%m}", day_of_week=day.isoweekday(),
                       in_candidate_window=True)
                day += dt.timedelta(days=1)
            insert(c, cls.w, "dim_products", product_id="p1", category_reporting="cat_a")
            insert(c, cls.w, "dim_sellers", seller_id="s1")
            for i, (oid, person, status, bought, delivered, price) in enumerate(cls.ORDERS):
                insert(c, cls.w, "dim_customers", customer_unique_id=person)
                b = dt.date.fromisoformat(bought)
                ctx = dict(customer_unique_id=person, order_status=status, purchase_date=b, in_candidate_window=True,
                           order_purchase_timestamp=dt.datetime(b.year, b.month, b.day, 10))
                insert(c, cls.w, "fact_orders", order_id=oid, customer_id=f"c{i}", purchase_year_month=bought[:7],
                       has_items=price is not None, items_price_total=price, item_count=1 if price else 0,
                       order_delivered_customer_date=dt.datetime.fromisoformat(delivered) if delivered else None,
                       dq_delivered_missing_delivery_date=status == "delivered" and delivered is None, **ctx)
                if price is not None:
                    insert(c, cls.w, "fact_order_lines", order_line_id=f"{oid}:1", order_id=oid, product_id="p1",
                           seller_id="s1", quantity=1, unit_price=price, line_price_total=price,
                           freight_total=D("0.00"), order_item_ids=[1], **ctx)

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def rfm(self, reference_date="2017-04-30", **params):
        """customer_rfm result (indexed by person) and its reconciliation checks on the same parameters."""
        q = queries.get_query("customer_rfm")
        params = {"reference_date": reference_date, **params}
        df = queries.run_query("customer_rfm", self.engine, self.w, self.a, params=params)
        self.assert_reconciles(df, q.bind(params))
        return df.set_index("customer_unique_id")

    def run_rfm_checks(self, df, bound):
        q = queries.get_query("customer_rfm")
        with self.engine.connect() as conn:
            quote = conn.dialect.identifier_preparer.quote
            return queries.run_checks(conn, q, df, bound, quote(self.w), quote(self.a))

    def assert_reconciles(self, df, bound):
        checks, problems = self.run_rfm_checks(df, bound)
        self.assertEqual(len(checks), 4)
        self.assertEqual(problems, [], bound)

    def test_delivered_scope_missing_date_and_reference_day_boundary(self):
        f = self.rfm()
        self.assertEqual(sorted(f.index), ["rA", "rB", "rC", "rD", "rE"])       # all purchased by the date
        self.assertEqual(f["has_eligible_purchase"].to_dict(),
                         {"rA": True, "rB": False, "rC": False, "rD": False, "rE": False})
        # rA: delivered at 23:59:59 ON the reference date -> eligible; 1 eligible person -> all scores 1
        self.assertEqual((f.loc["rA", "recency_days"], f.loc["rA", "frequency"], f.loc["rA", "monetary"],
                          f.loc["rA", "rfm_cell"]), (10, 1, D("100.00"), "111"))
        # rB: status 'delivered' but no delivery date -> excluded, kept with NULL R, F, M and scores
        self.assertTrue(all(pd.isna(f.loc["rB", c]) for c in ("recency_days", "frequency", "monetary",
                                                              "r_score", "f_score", "m_score", "rfm_cell")))
        self.assertEqual(f.loc["rB", "n_orders_to_date"], 1)

    def test_delivery_exactly_on_a_later_reference_date_counts(self):
        f = self.rfm("2017-05-01")
        # rC delivered 05-01 00:00:00 = on the reference date -> now eligible; rB's missing date never counts
        self.assertEqual(f["has_eligible_purchase"].to_dict(),
                         {"rA": True, "rB": False, "rC": True, "rD": False, "rE": False})
        # recency 11 / 11 (tie -> both R 1), frequency 1 / 1, monetary 40 < 100
        self.assertEqual(f.loc[["rA", "rC"], "rfm_cell"].to_dict(), {"rA": "115", "rC": "111"})
        g = self.rfm("2017-04-29")
        # the day before: rA (delivered 04-30) is not delivered yet
        self.assertFalse(g["has_eligible_purchase"].any())

    def test_all_scope_needs_items_and_purchase_cutoff_only(self):
        f = self.rfm(order_scope="all")
        # rB (no delivery date), rC (delivered later) and rD (canceled) count; rE (no items) never does
        self.assertEqual(f["has_eligible_purchase"].to_dict(),
                         {"rA": True, "rB": True, "rC": True, "rD": True, "rE": False})
        # R desc (rB 20, rD 15, rA 10, rC 10) -> 1, 2, 4, 4; F all 1 -> 1; M asc (30, 40, 60, 100) -> 1, 2, 4, 5
        self.assertEqual(f["rfm_cell"].dropna().to_dict(), {"rA": "415", "rB": "114", "rC": "412", "rD": "211"})
        self.assertEqual(int(f["frequency"].sum()), 4)
        self.assertEqual(sum(f["monetary"].dropna()), D("230.00"))

    def test_reconciliation_detects_wrong_eligibility(self):
        # the 'all'-scope result checked against the delivered-scope rule must fail (4 vs 1 eligible persons)
        q = queries.get_query("customer_rfm")
        wrong = queries.run_query("customer_rfm", self.engine, self.w, self.a,
                                  params={"reference_date": "2017-04-30", "order_scope": "all"})
        checks, problems = self.run_rfm_checks(wrong, q.bind({"reference_date": "2017-04-30"}))
        self.assertEqual([c["ok"] for c in checks], [True, False, False, False])
        self.assertTrue(any("eligible persons" in p and "query 4 != tables 1" in p for p in problems), problems)


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class CustomerQueryTests(unittest.TestCase):
    """Five persons (all orders at 10:00:00 unless stated):
    pA: 3 delivered orders - 2017-01-10 (100), 2017-02-09 (50), 2017-04-10 (150)
    pB: 1 delivered order  - 2017-03-01 (300)
    pC: 2 delivered orders in the SAME second, 2017-02-01 (40 and 60) - one purchase event
    pD: 1 canceled order without items, 2017-03-05 (no spending)
    pE: 1 delivered order  - 2017-05-20 (80), after the RFM reference date 2017-04-30
    """

    ORDERS = [("oA1", "pA", "2017-01-10", "delivered", D("100.00")), ("oA2", "pA", "2017-02-09", "delivered", D("50.00")),
              ("oA3", "pA", "2017-04-10", "delivered", D("150.00")), ("oB1", "pB", "2017-03-01", "delivered", D("300.00")),
              ("oC1", "pC", "2017-02-01", "delivered", D("40.00")), ("oC2", "pC", "2017-02-01", "delivered", D("60.00")),
              ("oD1", "pD", "2017-03-05", "canceled", None), ("oE1", "pE", "2017-05-20", "delivered", D("80.00"))]

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        with cls.engine.begin() as c:
            day = dt.date(2017, 1, 1)
            while day <= dt.date(2017, 5, 31):
                insert(c, cls.w, "dim_dates", date=day, year_month=f"{day:%Y-%m}", day_of_week=day.isoweekday(),
                       in_candidate_window=True)
                day += dt.timedelta(days=1)
            for person in "ABCDE":
                insert(c, cls.w, "dim_customers", customer_unique_id=f"p{person}")
            insert(c, cls.w, "dim_products", product_id="p1", category_reporting="cat_a")
            insert(c, cls.w, "dim_sellers", seller_id="s1")
            for i, (oid, person, day, status, price) in enumerate(cls.ORDERS):
                d = dt.date.fromisoformat(day)
                ctx = dict(customer_unique_id=person, order_status=status, purchase_date=d,
                           order_purchase_timestamp=dt.datetime(d.year, d.month, d.day, 10), in_candidate_window=True)
                insert(c, cls.w, "fact_orders", order_id=oid, customer_id=f"c{i}", purchase_year_month=day[:7],
                       has_items=price is not None, items_price_total=price, item_count=1 if price else 0, **ctx)
                if price is not None:
                    insert(c, cls.w, "fact_order_lines", order_line_id=f"{oid}:1", order_id=oid, product_id="p1",
                           seller_id="s1", quantity=1, unit_price=price, line_price_total=price,
                           freight_total=D("0.00"), order_item_ids=[1], **ctx)
        views.create_views(cls.engine, cls.w, cls.a)

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def run_q(self, name, **params):
        return queries.run_query(name, self.engine, self.w, self.a, params=params or None)

    def test_purchase_summary_first_last_and_missing_items(self):
        s = self.run_q("customer_purchase_summary").set_index("customer_unique_id")
        self.assertEqual(len(s), 5)
        a = s.loc["pA"]
        self.assertEqual((a["first_purchase_date"], a["last_purchase_date"]), (dt.date(2017, 1, 10), dt.date(2017, 4, 10)))
        self.assertEqual((a["n_orders"], a["n_customer_ids"], a["n_orders_with_items"], a["items_price_total"],
                          a["avg_items_price_per_order_with_items"], a["is_repeat_customer"]),
                         (3, 3, 3, D("300.00"), D("100.00"), True))
        self.assertEqual((s.loc["pC", "n_orders"], s.loc["pC", "is_repeat_customer"]), (2, True))  # same-second orders
        d = s.loc["pD"]
        self.assertEqual((d["n_orders"], d["n_orders_with_items"], d["is_repeat_customer"]), (1, 0, False))
        self.assertTrue(pd.isna(d["items_price_total"]) and pd.isna(d["avg_items_price_per_order_with_items"]))

    def test_repeat_analysis_counts_shares_and_denominators(self):
        r = self.run_q("repeat_customer_analysis")
        row = lambda seg, n=None: r[(r.customer_segment == seg) & (r.n_orders.isna() if n is None else r.n_orders == n)].iloc[0]
        one, rep, total = row("one_time"), row("repeat"), row("all")
        self.assertEqual((total["n_customers"], total["n_orders_total"], total["items_price_total"]), (5, 8, D("780.00")))
        self.assertEqual((one["n_customers"], one["pct_of_customers"], one["items_price_total"], one["pct_of_items_price"],
                          one["n_customers_without_items"]), (3, D("60.00"), D("380.00"), D("48.72"), 1))
        self.assertEqual((rep["n_customers"], rep["pct_of_customers"], rep["n_orders_total"], rep["pct_of_items_price"]),
                         (2, D("40.00"), 5, D("51.28")))
        self.assertEqual((row("repeat", 3)["n_customers"], row("repeat", 3)["items_price_total"]), (1, D("300.00")))

    def test_spending_ranking_ties_and_cumulative_share(self):
        s = self.run_q("customer_spending_ranking").set_index("customer_unique_id")
        self.assertNotIn("pD", s.index)                                        # no item spending: not ranked
        self.assertEqual(s["spending_rank"].to_dict(), {"pA": 1, "pB": 1, "pC": 3, "pE": 4})
        self.assertEqual(s["spending_dense_rank"].to_dict(), {"pA": 1, "pB": 1, "pC": 2, "pE": 3})
        self.assertEqual(s["cumulative_share_pct"].to_dict(),
                         {"pA": D("38.4615"), "pB": D("76.9231"), "pC": D("89.7436"), "pE": D("100.0000")})
        self.assertEqual(s.loc["pE", "share_of_items_price_pct"], D("10.2564"))
        self.assertEqual(set(s["n_ranked_customers"]), {4})

    def test_interpurchase_intervals_lag_and_same_second_events(self):
        i = self.run_q("interpurchase_intervals")
        self.assertEqual(list(i["customer_unique_id"]), ["pA", "pA"])            # pC's same-second orders = 1 event
        self.assertEqual(list(i["purchase_seq"]), [2, 3])
        self.assertEqual(list(i["days_since_prev_purchase"]), [D("30.00"), D("60.00")])
        self.assertEqual(i["prev_purchase_ts"].iloc[1], dt.datetime(2017, 2, 9, 10))
        self.assertEqual((i["customer_avg_interval_days"].iloc[0], i["overall_avg_interval_days"].iloc[0],
                          i["overall_median_interval_days"].iloc[0]), (D("45.00"), D("45.00"), D("45.00")))
        self.assertTrue(pd.isna(i["next_purchase_ts"].iloc[1]))                  # LEAD: no later purchase

    def test_rfm_scores_with_fixed_reference_date(self):
        f = self.run_q("customer_rfm", reference_date="2017-04-30").set_index("customer_unique_id")
        self.assertEqual(sorted(f.index), ["pA", "pB", "pC", "pD"])               # pE's only order is after the date
        expected = {"pA": (20, 3, D("300.00"), 5, 5, 3, "553"), "pB": (60, 1, D("300.00"), 3, 1, 3, "313"),
                    "pC": (88, 2, D("100.00"), 1, 3, 1, "131")}
        for person, values in expected.items():
            row = f.loc[person]
            self.assertEqual((row["recency_days"], row["frequency"], row["monetary"], row["r_score"], row["f_score"],
                              row["m_score"], row["rfm_cell"]), values, person)
        d = f.loc["pD"]                                                           # canceled, no items: no eligible order
        self.assertFalse(d["has_eligible_purchase"])
        self.assertEqual(d["n_orders_to_date"], 1)
        self.assertTrue(all(pd.isna(d[c]) for c in ("recency_days", "frequency", "monetary", "r_score", "rfm_cell")))
        self.assertEqual(set(f["reference_date"]), {dt.date(2017, 4, 30)})

    def test_rfm_reference_date_is_a_true_cutoff(self):
        f = self.run_q("customer_rfm", reference_date="2017-02-15").set_index("customer_unique_id")
        self.assertEqual(sorted(f.index), ["pA", "pC"])
        self.assertEqual((f.loc["pA", "frequency"], f.loc["pA", "monetary"], f.loc["pA", "recency_days"]),
                         (2, D("150.00"), 6))                                     # the April order is not counted

    def test_rfm_order_scope_parameter(self):
        delivered = self.run_q("customer_rfm", reference_date="2017-04-30")
        everything = self.run_q("customer_rfm", reference_date="2017-04-30", order_scope="all")
        self.assertEqual(set(delivered["order_scope"]), {"delivered"})
        self.assertEqual(set(everything["order_scope"]), {"all"})
        # pD's canceled order has no items, so it is not eligible under either scope
        self.assertEqual(int(everything["has_eligible_purchase"].sum()), 3)

    def test_verify_queries_passes_on_customer_data(self):
        r = queries.verify_queries(self.engine, self.w, self.a)
        self.assertTrue(r["ok"], r["problems"])
        rfm = next(q for q in r["queries"] if q["name"] == "customer_rfm")
        self.assertEqual(rfm["params"], {"reference_date": "2017-05-20", "order_scope": "delivered"})


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class ProductSellerQueryTests(unittest.TestCase):
    """Products p1, p2 (cat_a), p3, p4 (cat_b; p4 never sold); sellers s1, s2, s3 (s3 no sales).
    o1 (Jan 10): p1/s1 2 x 50 = 100 and p3/s2 1 x 100 = 100  -> two sellers, two categories
    o2 (Jan 20): p1/s1 1 x 50 = 50  and p2/s1 2 x 15 = 30   -> one seller, two products
    o3 (Feb 05): p1/s2 1 x 60 = 60                           -> p1 is sold by two sellers
    o4 (Feb 06): p3/s2 1 x 160 = 160                         total item-based sales = 500
    """

    LINES = [("o1", "2017-01-10", "p1", "s1", 2, D("50.00")), ("o1", "2017-01-10", "p3", "s2", 1, D("100.00")),
             ("o2", "2017-01-20", "p1", "s1", 1, D("50.00")), ("o2", "2017-01-20", "p2", "s1", 2, D("15.00")),
             ("o3", "2017-02-05", "p1", "s2", 1, D("60.00")), ("o4", "2017-02-06", "p3", "s2", 1, D("160.00"))]

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        with cls.engine.begin() as c:
            day = dt.date(2017, 1, 1)
            while day <= dt.date(2017, 2, 28):
                insert(c, cls.w, "dim_dates", date=day, year_month=f"{day:%Y-%m}", day_of_week=day.isoweekday(),
                       in_candidate_window=True)
                day += dt.timedelta(days=1)
            insert(c, cls.w, "dim_customers", customer_unique_id="pA")
            for pid, cat in (("p1", "cat_a"), ("p2", "cat_a"), ("p3", "cat_b"), ("p4", "cat_b")):
                insert(c, cls.w, "dim_products", product_id=pid, category_reporting=cat)
            for sid in ("s1", "s2", "s3"):
                insert(c, cls.w, "dim_sellers", seller_id=sid)
            orders = {}
            for oid, day, *_ in cls.LINES:
                orders.setdefault(oid, day)
            for i, (oid, day) in enumerate(orders.items()):
                d = dt.date.fromisoformat(day)
                total = sum(q * u for o, _, _, _, q, u in cls.LINES if o == oid)
                insert(c, cls.w, "fact_orders", order_id=oid, customer_id=f"c{i}", customer_unique_id="pA",
                       order_status="delivered", purchase_date=d, purchase_year_month=day[:7], has_items=True,
                       items_price_total=total, order_purchase_timestamp=dt.datetime(d.year, d.month, d.day, 10),
                       in_candidate_window=True)
            for n, (oid, day, pid, sid, qty, unit) in enumerate(cls.LINES):
                d = dt.date.fromisoformat(day)
                insert(c, cls.w, "fact_order_lines", order_line_id=f"{oid}:{n}", order_id=oid, product_id=pid,
                       seller_id=sid, quantity=qty, unit_price=unit, line_price_total=unit * qty,
                       freight_total=D("1.00"), order_item_ids=list(range(1, qty + 1)), customer_unique_id="pA",
                       order_status="delivered", purchase_date=d,
                       order_purchase_timestamp=dt.datetime(d.year, d.month, d.day, 10), in_candidate_window=True)
        views.create_views(cls.engine, cls.w, cls.a)

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def run_q(self, name, **params):
        return queries.run_query(name, self.engine, self.w, self.a, params=params or None)

    def test_top_sellers_multi_seller_orders_and_zero_sales(self):
        s = self.run_q("top_sellers_by_sales").set_index("seller_id")
        self.assertEqual(s["sales_rank"].to_dict(), {"s2": 1, "s1": 2, "s3": 3})
        self.assertEqual((s.loc["s1", "n_orders"], s.loc["s1", "units"], s.loc["s1", "items_price_total"],
                          s.loc["s1", "n_products"], s.loc["s1", "n_orders_shared_with_other_sellers"]),
                         (2, 5, D("180.00"), 2, 1))
        self.assertEqual((s.loc["s2", "n_orders"], s.loc["s2", "items_freight_total"],
                          s.loc["s2", "n_orders_shared_with_other_sellers"]), (3, D("3.00"), 1))
        self.assertEqual(s["share_of_items_price_pct"].to_dict(), {"s2": D("64.0000"), "s1": D("36.0000"), "s3": D("0.0000")})
        self.assertEqual(s["cumulative_share_pct"].to_dict(), {"s2": D("64.0000"), "s1": D("100.0000"), "s3": D("100.0000")})
        self.assertEqual((s.loc["s3", "n_orders"], s.loc["s3", "items_price_total"]), (0, D("0")))
        self.assertTrue(pd.isna(s.loc["s3", "avg_unit_price"]))

    def test_best_selling_products_ties_and_never_sold(self):
        p = self.run_q("best_selling_products").set_index("product_id")
        self.assertEqual(p["units_rank"].to_dict(), {"p1": 1, "p3": 2, "p2": 2, "p4": 4})      # tie p3 / p2
        self.assertEqual(p["units_dense_rank"].to_dict(), {"p1": 1, "p3": 2, "p2": 2, "p4": 3})
        self.assertEqual(p["sales_rank"].to_dict(), {"p3": 1, "p1": 2, "p2": 3, "p4": 4})
        self.assertEqual(p["sales_rank_in_category"].to_dict(), {"p1": 1, "p2": 2, "p3": 1, "p4": 2})
        self.assertEqual((p.loc["p1", "units"], p.loc["p1", "n_orders"], p.loc["p1", "n_sellers"],
                          p.loc["p1", "items_price_total"], p.loc["p1", "avg_unit_price"]), (4, 3, 2, D("210.00"), D("52.50")))
        self.assertTrue(p.loc["p4", "never_sold"])
        self.assertTrue(pd.isna(p.loc["p4", "avg_unit_price"]) and pd.isna(p.loc["p4", "first_sale_date"]))
        self.assertEqual(p.loc["p1", "first_sale_date"], dt.date(2017, 1, 10))

    def test_category_ranking_shares_and_non_additive_orders(self):
        c = self.run_q("category_performance_ranking").set_index("category_reporting")
        self.assertEqual(c["sales_rank"].to_dict(), {"cat_b": 1, "cat_a": 2})
        self.assertEqual(c["units_rank"].to_dict(), {"cat_a": 1, "cat_b": 2})
        self.assertEqual((c.loc["cat_b", "n_products"], c.loc["cat_b", "n_products_sold"], c.loc["cat_b", "n_orders"]), (2, 1, 2))
        self.assertEqual((c.loc["cat_a", "n_orders"], c.loc["cat_a", "units"], c.loc["cat_a", "items_price_total"]),
                         (3, 6, D("240.00")))
        self.assertEqual(int(c["n_orders"].sum()), 5)                            # 4 distinct orders: o1 is in both
        self.assertEqual(c["share_of_items_price_pct"].to_dict(), {"cat_b": D("52.0000"), "cat_a": D("48.0000")})
        self.assertEqual(c.loc["cat_a", "cumulative_share_pct"], D("100.0000"))

    def test_pareto_shares_cumulative_and_abc_classes(self):
        pa = self.run_q("product_sales_pareto").set_index("product_id")
        self.assertEqual(list(pa.index), ["p3", "p1", "p2"])                    # p4 (never sold) excluded
        self.assertEqual(pa["share_of_items_price_pct"].to_dict(), {"p3": D("52.0000"), "p1": D("42.0000"), "p2": D("6.0000")})
        self.assertEqual(pa["cumulative_share_pct"].to_dict(), {"p3": D("52.0000"), "p1": D("94.0000"), "p2": D("100.0000")})
        self.assertEqual(pa["cumulative_pct_of_products"].to_dict(),
                         {"p3": D("33.3333"), "p1": D("66.6667"), "p2": D("100.0000")})
        # 80 / 95: p1 starts at 52% (< 80) -> A although it crosses 80; p2 starts at 94% (< 95) -> B
        self.assertEqual(pa["abc_class"].to_dict(), {"p3": "A", "p1": "A", "p2": "B"})
        self.assertEqual(set(pa["n_products_in_class_a"]), {2})
        other = self.run_q("product_sales_pareto", a_threshold_pct=50, b_threshold_pct=90).set_index("product_id")
        self.assertEqual(other["abc_class"].to_dict(), {"p3": "A", "p1": "B", "p2": "C"})

    def test_seller_product_mix_partitions(self):
        m = self.run_q("seller_product_mix").set_index(["seller_id", "product_id"])
        self.assertEqual(len(m), 4)
        s1p1 = m.loc[("s1", "p1")]
        self.assertEqual((s1p1["units"], s1p1["n_orders"], s1p1["items_price_total"], s1p1["avg_unit_price"],
                          s1p1["product_rank_in_seller"], s1p1["product_share_of_seller_pct"]),
                         (3, 2, D("150.00"), D("50.00"), 1, D("83.3333")))
        self.assertEqual((m.loc[("s2", "p3"), "product_share_of_seller_pct"], m.loc[("s2", "p1"), "product_rank_in_seller"]),
                         (D("81.2500"), 2))
        self.assertEqual((s1p1["seller_rank_for_product"], s1p1["n_sellers_of_product"], s1p1["seller_share_of_product_pct"]),
                         (1, 2, D("71.4286")))
        self.assertEqual(m.loc[("s2", "p1"), "seller_share_of_product_pct"], D("28.5714"))
        self.assertEqual(m.loc[("s1", "p2"), "n_products_of_seller"], 2)

    def test_verify_queries_passes_on_product_seller_data(self):
        r = queries.verify_queries(self.engine, self.w, self.a)
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(len(r["queries"]), len(queries.load_queries()))


@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class DeliveryQueryTests(unittest.TestCase):
    """Eligible (delivered with a delivery date), times 10:00 unless stated:
    d1 SP  bought 01-02, estimated 01-12, delivered 01-08       -> 6.0 days,  est 10, delay -4  (early)
    d2 SP  bought 01-05, estimated 01-15, delivered 01-15 22:00 -> 10.5 days, est 10, delay 0   (on the day)
    d3 RJ  bought 01-10, estimated 01-20, delivered 02-03       -> 24.0 days, est 10, delay 14  (late)
    d4 BA  bought 02-01, estimated 02-11, delivered 02-14       -> 13.0 days, est 10, delay 3   (late)
    d5 BA  bought 03-20, estimated 04-30, delivered 04-15       -> 26.0 days, est 41, delay -15 (early)
    d6 XX  bought 03-30, estimated 04-04, delivered 04-19       -> 20.0 days, est 5,  delay 15  (late, unknown state)
    Not eligible: n1 SP canceled WITH a delivery date (flagged late), n2 RJ delivered WITHOUT a date,
    n3 AM shipped (AM has no eligible order). March has no delivery. d1 has 2 order lines and 2 payments.
    """

    ELIGIBLE = [("d1", "SP", "2017-01-02 10:00", "2017-01-12", "2017-01-08 10:00", 6.0, -4),
                ("d2", "SP", "2017-01-05 10:00", "2017-01-15", "2017-01-15 22:00", 10.5, 0),
                ("d3", "RJ", "2017-01-10 10:00", "2017-01-20", "2017-02-03 10:00", 24.0, 14),
                ("d4", "BA", "2017-02-01 10:00", "2017-02-11", "2017-02-14 10:00", 13.0, 3),
                ("d5", "BA", "2017-03-20 10:00", "2017-04-30", "2017-04-15 10:00", 26.0, -15),
                ("d6", "XX", "2017-03-30 10:00", "2017-04-04", "2017-04-19 10:00", 20.0, 15)]
    OTHERS = [("n1", "SP", "canceled", "2017-01-03 10:00", "2017-01-13", "2017-01-20 10:00", 17.0, 7),
              ("n2", "RJ", "delivered", "2017-01-04 10:00", "2017-01-14", None, None, None),
              ("n3", "AM", "shipped", "2017-01-06 10:00", "2017-01-30", None, None, None)]

    @classmethod
    def setUpClass(cls):
        from src.database import get_engine
        cls.engine = get_engine()
        cls._ctx = throwaway_warehouse(cls.engine)
        cls.w = cls._ctx.__enter__()
        cls.a = cls.w.replace("test_wh_", "test_an_")
        ts = lambda s: dt.datetime.fromisoformat(s) if s else None  # noqa: E731
        with cls.engine.begin() as c:
            day = dt.date(2017, 1, 1)
            while day <= dt.date(2017, 4, 30):
                insert(c, cls.w, "dim_dates", date=day, year_month=f"{day:%Y-%m}", day_of_week=day.isoweekday(),
                       in_candidate_window=True)
                day += dt.timedelta(days=1)
            insert(c, cls.w, "dim_customers", customer_unique_id="pA")
            insert(c, cls.w, "dim_products", product_id="p1", category_reporting="cat_a")
            insert(c, cls.w, "dim_sellers", seller_id="s1")
            rows = [(oid, st, "delivered", b, e, dl, dur, delay) for oid, st, b, e, dl, dur, delay in cls.ELIGIBLE] + cls.OTHERS
            for i, (oid, state, status, bought, est, delivered, duration, delay) in enumerate(rows):
                p = ts(bought)
                items = D("30.00") if oid == "d1" else None
                insert(c, cls.w, "fact_orders", order_id=oid, customer_id=f"c{i}", customer_unique_id="pA",
                       customer_state_std=state, order_status=status, order_purchase_timestamp=p,
                       purchase_date=p.date(), purchase_year_month=f"{p:%Y-%m}", in_candidate_window=True,
                       order_estimated_delivery_date=ts(est + " 00:00"), order_delivered_customer_date=ts(delivered),
                       purchase_to_delivery_days=duration, delivery_vs_estimate_days=delay,
                       is_late=None if delay is None else delay > 0, has_items=items is not None,
                       items_price_total=items, dq_status_canceled_with_date=oid == "n1",
                       dq_delivered_missing_delivery_date=oid == "n2")
            d1 = dict(customer_unique_id="pA", order_status="delivered", purchase_date=dt.date(2017, 1, 2),
                      order_purchase_timestamp=ts("2017-01-02 10:00"), in_candidate_window=True)
            for n, price in ((1, D("10.00")), (2, D("20.00"))):  # fan-out trap: 2 lines ...
                insert(c, cls.w, "fact_order_lines", order_line_id=f"d1:{n}", order_id="d1", product_id="p1",
                       seller_id="s1", quantity=1, unit_price=price, line_price_total=price, freight_total=D("0.00"),
                       order_item_ids=[n], shipping_limit_date=ts(f"2017-01-0{n + 2} 10:00"), **d1)
                insert(c, cls.w, "fact_payments", order_id="d1", payment_sequential=n, payment_value=D("15.00"), **d1)  # ... and 2 payments
        views.create_views(cls.engine, cls.w, cls.a)

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)
        cls.engine.dispose()

    def run_q(self, name):
        return queries.run_query(name, self.engine, self.w, self.a)

    def test_monthly_performance_d06_zero_month_and_lag(self):
        m = self.run_q("delivery_monthly_performance").set_index("delivery_month")
        self.assertEqual(list(m.index), ["2017-01", "2017-02", "2017-03", "2017-04"])       # March: gap-free spine
        self.assertEqual(int(m["n_delivered"].sum()), 6)                                      # n1, n2, n3 excluded
        jan, feb, mar, apr = (m.loc[k] for k in m.index)
        self.assertEqual((jan["n_delivered"], jan["n_early"], jan["n_on_estimated_day"], jan["n_late"]), (2, 1, 1, 0))
        self.assertEqual((jan["on_time_rate"], jan["late_rate"], jan["avg_delivery_days"], jan["median_delivery_days"],
                          jan["p90_delivery_days"], jan["avg_estimated_days"], jan["avg_delay_days"]),
                         (D("1.0000"), D("0.0000"), D("8.25"), D("8.25"), D("10.05"), D("10.00"), D("-2.00")))
        self.assertEqual((feb["late_rate"], feb["avg_delivery_days"], feb["p90_delivery_days"], feb["avg_delay_days"],
                          feb["avg_delivery_days_change"], feb["late_rate_change"]),
                         (D("1.0000"), D("18.50"), D("22.90"), D("8.50"), D("10.25"), D("1.0000")))
        self.assertEqual(mar["n_delivered"], 0)                                               # zero denominator
        self.assertTrue(all(pd.isna(mar[c]) for c in ("on_time_rate", "late_rate", "avg_delivery_days", "median_delivery_days")))
        self.assertEqual((apr["on_time_rate"], apr["avg_delay_days"], apr["avg_estimated_days"]), (D("0.5000"), D("0.00"), D("23.00")))
        self.assertTrue(pd.isna(apr["avg_delivery_days_change"]))                              # previous month has no value

    def test_state_performance_rankings_and_states_without_deliveries(self):
        s = self.run_q("delivery_state_performance").set_index("customer_state_std")
        self.assertEqual(sorted(s.index), ["AM", "BA", "RJ", "SP", "XX"])
        self.assertEqual(s["n_delivered"].to_dict(), {"SP": 2, "BA": 2, "XX": 1, "RJ": 1, "AM": 0})  # n1 / n2 not counted
        self.assertEqual((s.loc["SP", "median_delivery_days"], s.loc["SP", "p90_delivery_days"], s.loc["SP", "on_time_rate"]),
                         (D("8.25"), D("10.05"), D("1.0000")))
        self.assertEqual((s.loc["BA", "median_delivery_days"], s.loc["BA", "late_rate"], s.loc["BA", "avg_delay_days"]),
                         (D("19.50"), D("0.5000"), D("-6.00")))
        self.assertEqual({k: int(v) for k, v in s["speed_rank"].dropna().items()}, {"SP": 1, "BA": 2, "XX": 3, "RJ": 4})
        self.assertEqual({k: int(v) for k, v in s["punctuality_rank"].dropna().items()}, {"SP": 1, "BA": 2, "RJ": 3, "XX": 3})
        self.assertTrue(pd.isna(s.loc["AM", "speed_rank"]) and pd.isna(s.loc["AM", "on_time_rate"]))

    def test_region_mapping_unknown_and_order_weighting(self):
        r = self.run_q("delivery_region_ranking").set_index("region")
        self.assertEqual(set(r.index), {"Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul", "Unknown"})
        se = r.loc["Sudeste"]                                        # SP d1, d2 + RJ d3
        self.assertEqual((se["n_delivered"], se["n_states"], se["states"], se["late_rate"], se["avg_delivery_days"],
                          se["median_delivery_days"], se["share_of_deliveries_pct"]),
                         (3, 2, "RJ, SP", D("0.3333"), D("13.50"), D("10.50"), D("50.00")))
        # order-weighted: (6 + 10.5 + 24) / 3 = 13.50, not the mean of the state averages (8.25 + 24) / 2 = 16.125
        self.assertNotEqual(se["avg_delivery_days"], D("16.13"))
        unk = r.loc["Unknown"]
        self.assertEqual((unk["n_delivered"], unk["states"], unk["avg_delivery_days"]), (1, "XX", D("20.00")))
        self.assertTrue(pd.isna(unk["speed_rank"]))                  # reported, not ranked
        self.assertEqual((r.loc["Norte", "n_delivered"], r.loc["Sul", "n_delivered"]), (0, 0))  # AM: no eligible order
        self.assertTrue(pd.isna(r.loc["Norte", "on_time_rate"]) and pd.isna(r.loc["Norte", "speed_rank"]))
        self.assertEqual((int(r.loc["Sudeste", "speed_rank"]), int(r.loc["Nordeste", "speed_rank"])), (1, 2))
        self.assertEqual(int(r.loc["Nordeste", "punctuality_rank"]), 2)

    def test_estimated_vs_actual_definitions_and_error_metrics(self):
        e = self.run_q("estimated_vs_actual_delivery").set_index("order_id")
        self.assertEqual(sorted(e.index), ["d1", "d2", "d3", "d4", "d5", "d6"])               # one row per eligible order
        d2 = e.loc["d2"]
        self.assertEqual((d2["estimated_days"], d2["actual_days"], d2["actual_elapsed_days"], d2["delay_days"],
                          d2["delivery_class"]), (10, 10, D("10.50"), 0, "on_estimated_day"))
        d6 = e.loc["d6"]
        self.assertEqual((d6["estimated_days"], d6["actual_days"], d6["delay_days"], d6["abs_error_days"], d6["delivery_class"]),
                         (5, 20, 15, 15, "late"))
        self.assertTrue((e["delay_days"] == e["actual_days"] - e["estimated_days"]).all())
        # bias = (-4 + 0 + 14 + 3 - 15 + 15) / 6 = 2.1667; MAE = (4 + 0 + 14 + 3 + 15 + 15) / 6 = 8.5
        self.assertEqual((e["mean_delay_days"].iloc[0], e["mean_abs_error_days"].iloc[0], e["population_late_rate"].iloc[0]),
                         (D("2.1667"), D("8.5000"), D("0.5000")))
        self.assertEqual(e["delay_percent_rank"].to_dict(), {"d1": D("0.2000"), "d2": D("0.4000"), "d3": D("0.8000"),
                                                             "d4": D("0.6000"), "d5": D("0.0000"), "d6": D("1.0000")})

    def test_delay_distribution_boundaries_and_cumulative(self):
        d = self.run_q("delivery_delay_distribution").set_index("delay_bucket")
        self.assertEqual(len(d), 9)
        expected = {"early_15_plus_days": 1, "early_8_14_days": 0, "early_4_7_days": 1, "early_1_3_days": 0,
                    "on_estimated_day": 1, "late_1_3_days": 1, "late_4_7_days": 0, "late_8_14_days": 1, "late_15_plus_days": 1}
        self.assertEqual(d["n_orders"].to_dict(), expected)          # -15, -4, 0, 3, 14, 15 land in their buckets
        self.assertEqual(list(d["cumulative_pct"]), [D("16.67"), D("16.67"), D("33.33"), D("33.33"), D("50.00"),
                                                     D("66.67"), D("66.67"), D("83.33"), D("100.00")])
        self.assertEqual(d.loc["early_8_14_days", "pct_of_orders"], D("0.00"))
        # bounds are contiguous integers: each bucket starts one day after the previous one ends
        bounds = list(zip(d["min_days"], d["max_days"]))
        for (lo1, hi1), (lo2, _) in zip(bounds, bounds[1:]):
            self.assertEqual(int(lo2), int(hi1) + 1)
        self.assertTrue(pd.isna(bounds[0][0]) and pd.isna(bounds[-1][1]))

    def test_verify_queries_passes_on_delivery_data(self):
        r = queries.verify_queries(self.engine, self.w, self.a)
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(len(r["queries"]), 20)


if __name__ == "__main__":
    unittest.main()
