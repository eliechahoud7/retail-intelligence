"""Tests for the analytical layer (src/analytical).

Run from the project root:   python -m unittest discover -s tests -t . -v

* Unit tests use small in-memory frames shaped like the clean outputs.
* Pipeline tests run raw fixtures through staging, clean and analytical in temporary directories.
* Integration tests run the full chain on the real raw data into a temporary directory
  (data/staging and data/processed are not touched); skipped if data/raw/ is absent.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.analytical import AnalyticalInputError, TABLES, build_order_lines, run_analytical
from src.analytical import dims, facts
from src.analytical.cli import EXIT_FAILED, EXIT_OK, main
from src.analytical.joins import JoinLog
from src.analytical.pipeline import content_hash
from src.analytical.schema import BASELINE_ANALYTICAL
from src.analytical.validation import financial_checks, order_line_checks, schema_and_key_checks
from src.clean import run_clean
from src.ingestion import DATASETS, DATASETS_BY_NAME, DEFAULT_RAW_DIR, checksum_snapshot, sha256_file
from src.staging import read_lineage, read_parquet, run_staging
from src.staging.validation import BLOCKING, REGRESSION, failed
from tests.test_staging import write_valid_fixture

ORDER_FLAGS = [c for c, d in TABLES["fact_orders"].columns if c.startswith("dq_") and c != "dq_payment_items_mismatch"
               and c != "dq_customer_no_geolocation"]


def s(values, dtype="string"):
    return pd.Series(values, dtype=dtype)


def ts(values):
    return pd.to_datetime(pd.Series(values), format="%Y-%m-%d %H:%M:%S").astype("datetime64[us]")


def items_frame(rows):
    """rows: (order_id, order_item_id, product_id, seller_id, shipping_limit_date, price, freight)."""
    df = pd.DataFrame(rows, columns=["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date",
                                     "price", "freight_value"])
    for c in ("order_id", "product_id", "seller_id"):
        df[c] = df[c].astype("string")
    df["order_item_id"] = df["order_item_id"].astype("Int64")
    df["shipping_limit_date"] = ts(df["shipping_limit_date"])
    df["dq_shipping_limit_out_of_range"] = False
    return df


def small_world():
    """Two persons, three orders: o1 (2 lines, 2 payments), o2 (no items), o3 (canceled, 1 line)."""
    orders = pd.DataFrame({
        "order_id": s(["o1", "o2", "o3"]), "customer_id": s(["c1", "c2", "c3"]),
        "order_status": pd.Categorical(["delivered", "unavailable", "canceled"]),
        "order_purchase_timestamp": ts(["2017-03-01 10:00:00", "2017-03-01 10:00:00", "2016-10-05 08:00:00"]),
        "order_approved_at": ts(["2017-03-01 11:00:00", None, "2016-10-05 09:00:00"]),
        "order_delivered_carrier_date": ts(["2017-03-02 10:00:00", None, None]),
        "order_delivered_customer_date": ts(["2017-03-09 15:00:00", None, None]),
        "order_estimated_delivery_date": ts(["2017-03-09 00:00:00", "2017-03-20 00:00:00", "2016-10-30 00:00:00"]),
    })
    for f in ORDER_FLAGS:
        orders[f] = False
    customers = pd.DataFrame({
        "customer_id": s(["c3", "c2", "c1"]), "customer_unique_id": s(["pB", "pA", "pA"]),
        "customer_zip_code_prefix": s(["01001", "02002", "01001"]), "city_key": s(["x", "y", "x"]),
        "customer_state_std": pd.Categorical(["SP", "RJ", "SP"]), "lat_centroid": [-23.5, np.nan, -23.5],
        "lng_centroid": [-46.6, np.nan, -46.6], "dq_no_geolocation": [False, True, False]})
    items = items_frame([
        ("o1", 1, "p1", "s1", "2017-03-05 00:00:00", 10.0, 2.0),
        ("o1", 2, "p2", "s1", "2017-03-05 00:00:00", 5.5, 1.0),
        ("o1", 3, "p1", "s1", "2017-03-05 00:00:00", 10.0, 2.0),
        ("o3", 1, "p2", "s2", "2016-10-10 00:00:00", 7.0, 0.0),
    ])
    payments = pd.DataFrame({"order_id": s(["o1", "o1", "o3"]), "payment_sequential": pd.array([1, 2, 1], dtype="Int64"),
                             "payment_type": pd.Categorical(["credit_card", "voucher", "credit_card"]),
                             "payment_installments": pd.array([3, 0, 1], dtype="Int64"),
                             "payment_value": [25.0, 5.5, 7.004], "dq_payment_zero": False,
                             "dq_installments_zero": [False, True, False]})
    reviews = pd.DataFrame({"review_id": s(["r1", "r2"]), "order_id": s(["o1", "o1"])})
    return orders, customers, items, payments, reviews


def build_small():
    orders, customers, items, payments, reviews = small_world()
    j = JoinLog()
    ctx = facts.order_context(orders, customers, j)
    lines = facts.fact_order_lines(items, ctx, j)
    fo = facts.fact_orders(orders, customers, items, payments, reviews, lines, j)
    return lines, fo, j


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# --------------------------------------------------------------------------- D-15
class OrderLineTests(unittest.TestCase):
    def test_six_attribute_grain_is_lossless(self):
        items = items_frame([
            ("o1", 1, "p1", "s1", "2017-01-01 00:00:00", 10.0, 2.0),
            ("o1", 2, "p2", "s1", "2017-01-01 00:00:00", 3.0, 1.0),
            ("o1", 3, "p1", "s1", "2017-01-01 00:00:00", 10.0, 2.0),    # same line as item 1 (non-contiguous ids)
            ("o1", 4, "p1", "s1", "2017-01-01 00:00:00", 10.0, 2.5),    # other freight: separate line
            ("o1", 5, "p1", "s1", "2017-01-02 00:00:00", 10.0, 2.0),    # other deadline: separate line
        ])
        lines = build_order_lines(items).set_index("order_line_id")
        self.assertEqual(len(lines), 4)
        l1 = lines.loc["o1:1"]
        self.assertEqual(l1["quantity"], 2)
        self.assertEqual(l1["order_item_ids"].tolist(), [1, 3])
        self.assertEqual((l1["line_price_total"], l1["freight_total"], l1["unit_price"]), (20.0, 4.0, 10.0))
        self.assertEqual(int(lines["quantity"].sum()), len(items))
        self.assertEqual(lines.loc["o1:4", "freight_value_per_row"], 2.5)

    def test_v13_detects_a_lost_or_duplicated_item(self):
        orders, customers, items, *_ = small_world()
        lines, _, _ = build_small()
        frames = {"fact_order_lines": lines}
        self.assertFalse(failed(order_line_checks({"order_items": items}, frames)))
        broken = lines.copy()
        broken.at[0, "order_item_ids"] = np.array([1, 1], dtype="int64")
        self.assertTrue(failed(order_line_checks({"order_items": items}, {"fact_order_lines": broken})))


# --------------------------------------------------------------------------- D-13
class OrderSummaryTests(unittest.TestCase):
    def test_payments_and_items_aggregated_independently(self):
        _, fo, _ = build_small()
        o1 = fo.set_index("order_id").loc["o1"]
        # 3 item rows x 2 payment rows would give 6 rows in a naive join; totals must not inflate
        self.assertEqual((o1["item_count"], o1["order_line_count"], o1["distinct_products"]), (3, 2, 2))
        self.assertEqual((o1["items_price_total"], o1["items_freight_total"], o1["payment_total"]), (25.5, 5.0, 30.5))
        self.assertEqual((o1["payment_rows"], o1["payment_types"], o1["has_voucher"]), (2, "credit_card|voucher", True))
        self.assertEqual(o1["max_installments"], 3)            # zero-instalment row ignored (D-16)
        self.assertEqual(o1["payment_minus_items"], 0.0)
        self.assertFalse(o1["dq_payment_items_mismatch"])
        self.assertEqual(o1["review_count"], 2)

    def test_orders_without_items_keep_counts_zero_and_totals_missing(self):
        _, fo, _ = build_small()
        o2 = fo.set_index("order_id").loc["o2"]
        self.assertEqual((o2["item_count"], o2["payment_rows"], o2["review_count"]), (0, 0, 0))
        self.assertFalse(o2["has_items"] or o2["has_payment"])
        self.assertTrue(pd.isna(o2["items_price_total"]) and pd.isna(o2["payment_total"]) and pd.isna(o2["payment_minus_items"]))
        self.assertFalse(o2["dq_payment_items_mismatch"])

    def test_mismatch_uses_rounded_difference_and_tolerance(self):
        _, fo, _ = build_small()
        o3 = fo.set_index("order_id").loc["o3"]
        self.assertEqual(o3["payment_minus_items"], 0.0)          # 7.004 - 7.0 rounds to 0.00
        self.assertFalse(o3["dq_payment_items_mismatch"])
        orders, customers, items, payments, reviews = small_world()
        payments.loc[2, "payment_value"] = 7.02
        j = JoinLog()
        lines = facts.fact_order_lines(items, facts.order_context(orders, customers, j), j)
        fo2 = facts.fact_orders(orders, customers, items, payments, reviews, lines, j).set_index("order_id")
        self.assertTrue(fo2.loc["o3", "dq_payment_items_mismatch"])

    def test_every_join_is_validated_and_row_preserving(self):
        _, _, joins = build_small()
        self.assertTrue(joins.records)
        self.assertFalse(failed(joins.checks()))
        self.assertTrue(all(r["validate"] in ("one_to_one", "many_to_one") for r in joins.records))

    def test_join_log_rejects_fan_out_and_unsafe_joins(self):
        left = pd.DataFrame({"k": s(["a", "b"])})
        right = pd.DataFrame({"k": s(["a", "a"]), "v": [1, 2]})
        j = JoinLog()
        with self.assertRaises(pd.errors.MergeError):
            j.merge("dup", left, right, on="k", validate="many_to_one")
        with self.assertRaises(ValueError):
            j.merge("m2m", left, right, on="k", validate="many_to_many")
        with self.assertRaises(ValueError):
            j.merge("inner", left, right, on="k", validate="one_to_one", how="inner")

    def test_financial_check_detects_inflation(self):
        orders, customers, items, payments, reviews = small_world()
        lines, fo, _ = build_small()
        frames = {"fact_order_lines": lines, "fact_orders": fo, "fact_payments": payments,
                  "dim_customers": dims.dim_customers(fo)}
        clean = {"order_items": items, "payments": payments}
        self.assertFalse(failed(financial_checks(clean, frames)))
        inflated = fo.copy()
        inflated["payment_total"] = inflated["payment_total"] * 2
        frames["fact_orders"] = inflated
        self.assertIn("V09.fact_orders.payment_total", [c.check_id for c in failed(financial_checks(clean, frames))])


# --------------------------------------------------------------------------- durations / sequence
class DurationAndSequenceTests(unittest.TestCase):
    def test_durations_day_level_lateness_and_flag_guard(self):
        _, fo, _ = build_small()
        o1 = fo.set_index("order_id").loc["o1"]
        self.assertAlmostEqual(o1["approval_hours"], 1.0)
        self.assertAlmostEqual(o1["carrier_to_customer_days"], 7 + 5 / 24)
        self.assertEqual(o1["delivery_vs_estimate_days"], 0.0)  # delivered on the estimated day: not late
        self.assertFalse(o1["is_late"])
        self.assertTrue(pd.isna(fo.set_index("order_id").loc["o2", "is_late"]))
        orders = small_world()[0]
        orders.loc[0, "dq_delivered_before_carrier"] = True
        self.assertTrue(pd.isna(facts.durations(orders).loc[0, "carrier_to_customer_days"]))
        self.assertFalse(pd.isna(facts.durations(orders).loc[0, "purchase_to_delivery_days"]))

    def test_customer_order_sequence_breaks_ties_by_order_id(self):
        _, fo, _ = build_small()
        seq = fo.set_index("order_id")["customer_order_seq"]
        self.assertEqual((seq["o1"], seq["o2"], seq["o3"]), (1, 2, 1))  # o1/o2: same person, same second
        self.assertEqual(int(fo["is_first_order"].sum()), 2)

    def test_candidate_window_is_a_flag(self):
        _, fo, _ = build_small()
        self.assertEqual(len(fo), 3)
        self.assertEqual(fo.set_index("order_id")["in_candidate_window"].to_dict(), {"o1": True, "o2": True, "o3": False})


# --------------------------------------------------------------------------- dimensions
class DimensionTests(unittest.TestCase):
    def test_dim_customers_all_and_delivered_variants(self):
        _, fo, _ = build_small()
        dc = dims.dim_customers(fo).set_index("customer_unique_id")
        a, b = dc.loc["pA"], dc.loc["pB"]
        self.assertEqual((a["n_orders"], a["n_customer_ids"], a["n_items"], a["n_orders_delivered"]), (2, 2, 3, 1))
        self.assertEqual((a["n_distinct_zips"], a["n_distinct_states"], a["cohort_month"]), (2, 2, "2017-03"))
        self.assertEqual(a["payment_total"], 30.5)
        self.assertEqual(b["n_orders_delivered"], 0)
        self.assertTrue(pd.isna(b["payment_total_delivered"]) and pd.isna(b["first_delivered_purchase_ts"]))
        self.assertEqual(b["payment_total"], 7.004)
        self.assertNotIn("customer_state_std", dc.columns)          # no chosen address

    def test_dim_dates_is_contiguous(self):
        d = dims.dim_dates([ts(["2017-12-30 10:00:00", "2018-01-02 23:00:00"])])
        self.assertEqual(d["date"].dt.strftime("%Y-%m-%d").tolist(), ["2017-12-30", "2017-12-31", "2018-01-01", "2018-01-02"])
        self.assertEqual(d["is_weekend"].tolist(), [True, True, False, False])
        self.assertEqual(d["iso_week"].tolist()[2], 1)


class HashTests(unittest.TestCase):
    def test_content_hash_handles_list_columns(self):
        df = pd.DataFrame({"ids": pd.Series([np.array([1, 3]), np.array([2])], dtype=object), "x": [1.0, 2.0]})
        self.assertEqual(content_hash(df), content_hash(df.copy()))
        other = df.copy()
        other.at[0, "ids"] = np.array([1, 4])
        self.assertNotEqual(content_hash(df), content_hash(other))


# --------------------------------------------------------------------------- pipeline on fixtures
class PipelineFixtureTests(TempDir):
    def setUp(self):
        super().setUp()
        self.raw = self.dir / "raw"
        self.raw.mkdir()
        write_valid_fixture(self.raw)
        self.assertTrue(run_staging(self.raw, self.dir / "staging", None, verify_baseline=False).success)
        self.clean = self.dir / "clean"
        self.assertTrue(run_clean(self.raw, self.dir / "staging", self.clean, None, None, verify_baseline=False).success)

    def run_a(self, tag="a"):
        return run_analytical(self.raw, self.clean, self.dir / tag / "out", self.dir / tag / "manifest",
                              self.dir / tag / "reports", verify_baseline=False)

    def test_run_writes_tables_manifest_reports_and_leaves_inputs_unchanged(self):
        raw_before = checksum_snapshot(self.raw, DATASETS)
        clean_before = {p.name: sha256_file(p) for p in self.clean.glob("*.parquet")}
        result = self.run_a()
        self.assertTrue(result.success, [(c.check_id, c.observed) for c in failed(result.checks)])
        self.assertEqual(sorted(p.stem for p in (self.dir / "a" / "out").glob("*.parquet")), sorted(TABLES))
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual((manifest["layer"], len(manifest["outputs"])), ("analytical", len(TABLES)))
        self.assertTrue(all(o["grain"] and o["primary_key"] for o in manifest["outputs"]))
        report = json.loads((self.dir / "a" / "reports" / "analytical_report.json").read_text(encoding="utf-8"))
        self.assertFalse(any(c["check_class"] == REGRESSION for c in report["checks"]))
        self.assertEqual(read_lineage(self.dir / "a" / "out" / "fact_orders.parquet")["layer"], "analytical")
        self.assertEqual(checksum_snapshot(self.raw, DATASETS), raw_before)
        self.assertEqual({p.name: sha256_file(p) for p in self.clean.glob("*.parquet")}, clean_before)

    def test_null_foreign_key_counts_as_orphan(self):
        self.assertTrue(self.run_a().success)
        frames = {n: read_parquet(self.dir / "a" / "out" / f"{n}.parquet") for n in TABLES}
        self.assertFalse(failed(schema_and_key_checks(frames)))
        frames["fact_orders"].loc[0, "customer_unique_id"] = pd.NA
        self.assertIn("FK.fact_orders.customer_unique_id", [c.check_id for c in failed(schema_and_key_checks(frames))])

    def test_repeated_runs_produce_identical_content(self):
        a, b = self.run_a("a"), self.run_a("b")
        self.assertTrue(a.success and b.success)
        self.assertEqual({r.name: r.content_sha256 for r in a.outputs}, {r.name: r.content_sha256 for r in b.outputs})

    def test_stale_clean_blocks_writing(self):
        path = self.raw / DATASETS_BY_NAME["payments"].filename
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        result = self.run_a()
        self.assertFalse(result.success)
        self.assertIn("C01.payments", [c.check_id for c in failed(result.checks)])
        self.assertFalse((self.dir / "a" / "out").exists())
        self.assertTrue((self.dir / "a" / "reports" / "analytical_report.md").exists())

    def test_missing_clean_outputs_raise_and_cli_exit_codes(self):
        common = ["--raw-dir", str(self.raw), "--out-dir", str(self.dir / "o"), "--manifest-dir", str(self.dir / "m"),
                  "--report-dir", str(self.dir / "r"), "--skip-baseline-check"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(common + ["--clean-dir", str(self.clean)]), EXIT_OK)
            self.assertEqual(main(common + ["--clean-dir", str(self.dir / "nowhere")]), EXIT_FAILED)
        self.assertIn("RESULT: SUCCESS", out.getvalue())
        with self.assertRaises(AnalyticalInputError):
            run_analytical(self.raw, self.dir / "nowhere", self.dir / "x", None, None, verify_baseline=False)


# --------------------------------------------------------------------------- real data
@unittest.skipUnless(all((DEFAULT_RAW_DIR / d.filename).is_file() for d in DATASETS), "raw data not present")
class RealDataAnalyticalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.raw_before = checksum_snapshot(DEFAULT_RAW_DIR, DATASETS)
        run_staging(DEFAULT_RAW_DIR, root / "staging", None)
        cls.clean = run_clean(DEFAULT_RAW_DIR, root / "staging", root / "clean", None, None)
        cls.out = root / "analytical"
        cls.result = run_analytical(DEFAULT_RAW_DIR, root / "clean", cls.out, root / "manifest", root / "reports")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def read(self, name):
        return read_parquet(self.out / f"{name}.parquet")

    def test_run_succeeds_with_blocking_and_regression_checks(self):
        self.assertTrue(self.clean.success)
        self.assertTrue(self.result.success, [(c.check_id, c.observed) for c in failed(self.result.checks)])
        self.assertTrue({BLOCKING, REGRESSION} <= {c.check_class for c in self.result.checks})

    def test_tables_read_back_with_unique_grain(self):
        expected = {"fact_order_lines": 102_425, "fact_orders": 99_441, "fact_payments": 103_886, "fact_reviews": 99_224,
                    "dim_customers": 96_096, "dim_products": 32_951, "dim_sellers": 3_095}
        for name, spec in TABLES.items():
            df = self.read(name)
            if name in expected:
                self.assertEqual(len(df), expected[name], name)
            self.assertFalse(df.duplicated(subset=list(spec.primary_key)).any(), name)

    def test_financial_reconciliation_from_read_back_files(self):
        lines, fo, fp = self.read("fact_order_lines"), self.read("fact_orders"), self.read("fact_payments")
        self.assertEqual(round(float(lines["line_price_total"].sum()), 2), 13_591_643.70)
        self.assertEqual(round(float(lines["freight_total"].sum()), 2), 2_251_909.54)
        self.assertEqual(round(float(fo["payment_total"].sum()), 2), 16_008_872.12)
        self.assertEqual(round(float(fp["payment_value"].sum()), 2), 16_008_872.12)
        self.assertEqual(int(fo["dq_payment_items_mismatch"].sum()), BASELINE_ANALYTICAL["dq_payment_items_mismatch"])
        self.assertEqual(int(lines["quantity"].sum()), 112_650)
        self.assertLessEqual(int(lines["quantity"].max()), 20)

    def test_raw_files_unchanged(self):
        self.assertEqual(checksum_snapshot(DEFAULT_RAW_DIR, DATASETS), self.raw_before)
        self.assertEqual(self.raw_before, {d.name: d.sha256 for d in DATASETS})


if __name__ == "__main__":
    unittest.main()
