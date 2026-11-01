"""Tests for the clean layer (src/clean).

Run from the project root:   python -m unittest discover -s tests -t . -v

* Unit tests use small in-memory frames.
* Pipeline tests build raw fixtures, run staging and then clean into temporary directories.
* Integration tests run staging and clean on the real raw data into a temporary directory
  (data/staging and data/processed are not touched); skipped if data/raw/ is absent.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.clean import (CategoryMapError, CleanInputError, build_category_map, build_zip_lookup, content_hash,
                       run_clean)
from src.clean import flags as fl
from src.clean import standardise as sd
from src.clean.cli import EXIT_FAILED, EXIT_OK, main
from src.clean.reference import NOTE_ADDED, NOTE_TWIN, UNCATEGORISED_LABEL
from src.clean.schema import (ADDED_COLUMNS, BASELINE_CLEAN_FLAGS, BASELINE_STRUCTURE, CLEAN_OUTPUTS,
                              SOURCE_TABLES, added_column_names)
from src.clean.transform import extend
from src.ingestion import DATASETS, DATASETS_BY_NAME, DEFAULT_RAW_DIR, checksum_snapshot
from src.staging import read_lineage, read_parquet, run_staging
from src.staging.schema import BASELINE_PRODUCT_FLAGS, BASELINE_ROWS
from src.staging.validation import BLOCKING, REGRESSION, failed
from tests.test_staging import write_valid_fixture


def s(values, dtype="string"):
    return pd.Series(values, dtype=dtype)


def ts(values):
    return pd.to_datetime(pd.Series(values), format="%Y-%m-%d %H:%M:%S")


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# --------------------------------------------------------------------------- standardisation
class StandardiseTests(unittest.TestCase):
    def test_city_key_removes_accents_lowercases_and_trims(self):
        out = sd.city_key(s([" São Paulo ", "sao paulo", "BRASÍLIA", None]))
        self.assertEqual(out.tolist()[:3], ["sao paulo", "sao paulo", "brasilia"])
        self.assertTrue(pd.isna(out.iloc[3]))
        self.assertEqual(str(out.dtype), "string")

    def test_city_key_does_not_decode_or_repair(self):
        self.assertEqual(sd.city_key(s(["lambari d%26apos%3boeste", "sao paulo - sp"])).tolist(),
                         ["lambari d%26apos%3boeste", "sao paulo - sp"])

    def test_decode_only_the_url_encoded_apostrophe(self):
        out = sd.decode_url_apostrophe(s(["são joão do pau d%26apos%3balho", "a%20b", "lambari"]))
        self.assertEqual(out.tolist(), ["são joão do pau d'alho", "a%20b", "lambari"])

    def test_malformed_city_pattern(self):
        bad = ["sao paulo - sp", "auriflama/sp", "04482255", "vendas@creditparts.com.br",
               "rio de janeiro \\rio de janeiro", "novo hamburgo, rio grande do sul, brasil"]
        good = ["sao paulo", "santa barbara d'oeste", "mogi-mirim", "são joão del rei"]
        self.assertTrue(sd.city_malformed(s(bad)).all())
        self.assertFalse(sd.city_malformed(s(good)).any())

    def test_state_std_uppercases_without_dropping_unknown_codes(self):
        out = sd.state_std(s([" sp", "RJ", "XX"]))
        self.assertEqual(out.astype("string").tolist(), ["SP", "RJ", "XX"])
        self.assertIsInstance(out.dtype, pd.CategoricalDtype)

    def test_lookup_keeps_rows_and_order_and_rejects_fan_out(self):
        left = pd.DataFrame({"k": s(["b", "a", "z", "b"])})
        right = pd.DataFrame({"k": s(["a", "b"]), "v": [1.0, 2.0]})
        self.assertEqual(sd.lookup(left, right, "k", "k", ["v"])["v"].tolist()[:2], [2.0, 1.0])
        self.assertTrue(pd.isna(sd.lookup(left, right, "k", "k", ["v"])["v"].iloc[2]))
        with self.assertRaises(pd.errors.MergeError):
            sd.lookup(left, pd.concat([right, right]), "k", "k", ["v"])


# --------------------------------------------------------------------------- flags
class OrderFlagTests(unittest.TestCase):
    def test_gap_chronology_status_and_same_second_flags(self):
        nat = None
        orders = pd.DataFrame({
            "order_id": s(["o1", "o2", "o3", "o4", "o5"]),
            "customer_id": s(["c1", "c2", "c3", "c4", "c5"]),
            "order_status": pd.Categorical(["delivered", "delivered", "canceled", "shipped", "delivered"]),
            "order_purchase_timestamp": ts(["2017-01-02 10:00:00", "2017-01-02 10:00:00", "2017-01-05 10:00:00",
                                            "2017-01-06 10:00:00", "2017-01-07 10:00:00"]),
            "order_approved_at": ts(["2017-01-02 11:00:00", nat, "2017-01-05 11:00:00", "2017-01-06 11:00:00",
                                     "2017-01-07 11:00:00"]),
            "order_delivered_carrier_date": ts(["2017-01-01 09:00:00", "2017-01-03 10:00:00", nat,
                                                "2017-01-06 10:30:00", "2017-01-08 10:00:00"]),
            "order_delivered_customer_date": ts(["2017-01-04 10:00:00", "2017-01-02 12:00:00", "2017-01-09 10:00:00",
                                                 nat, nat]),
        })
        customers = pd.DataFrame({"customer_id": s(["c5", "c4", "c3", "c2", "c1"]),
                                  "customer_unique_id": s(["p3", "p2", "p9", "p1", "p1"])})
        f = fl.order_flags(orders, customers)
        self.assertEqual(f["dq_delivered_missing_approval"].tolist(), [False, True, False, False, False])
        self.assertEqual(f["dq_delivered_missing_delivery_date"].tolist(), [False, False, False, False, True])
        self.assertEqual(f["dq_carrier_before_purchase"].tolist(), [True, False, False, False, False])
        self.assertEqual(f["dq_delivered_before_carrier"].tolist(), [False, True, False, False, False])
        self.assertEqual(f["dq_carrier_before_approval"].tolist(), [True, False, False, True, False])
        self.assertEqual(f["dq_delivered_before_approval"].tolist(), [False] * 5)  # o2 has no approval: not evaluated
        self.assertEqual(f["dq_status_canceled_with_date"].tolist(), [False, False, True, False, False])
        self.assertEqual(f["dq_same_person_same_second"].tolist(), [True, True, False, False, False])
        self.assertTrue(all(f[c].dtype == bool for c in f.columns))

    def test_input_frames_not_mutated(self):
        orders = pd.DataFrame({"order_id": s(["o1"]), "customer_id": s(["c1"]), "order_status": pd.Categorical(["delivered"]),
                               **{c: ts(["2017-01-02 10:00:00"]) for c in (fl.P, fl.A, fl.C, fl.D)}})
        customers = pd.DataFrame({"customer_id": s(["c1"]), "customer_unique_id": s(["p1"])})
        before = orders.copy()
        fl.order_flags(orders, customers)
        pd.testing.assert_frame_equal(orders, before)


class ItemPaymentReviewFlagTests(unittest.TestCase):
    def test_shipping_limit_more_than_one_year_after_last_purchase(self):
        orders = pd.DataFrame({fl.P: ts(["2017-01-01 00:00:00", "2018-10-17 17:30:18"])})
        items = pd.DataFrame({"shipping_limit_date": ts(["2019-10-17 17:30:18", "2019-10-17 17:30:19", "2020-04-09 22:35:08"])})
        self.assertEqual(fl.order_item_flags(items, orders)["dq_shipping_limit_out_of_range"].tolist(), [False, True, True])

    def test_payment_flags_keep_values_and_treat_missing_as_not_flagged(self):
        pay = pd.DataFrame({"payment_value": [0.0, 10.0, 5.0],
                            "payment_installments": pd.array([1, 0, None], dtype="Int64")})
        f = fl.payment_flags(pay)
        self.assertEqual(f["dq_payment_zero"].tolist(), [True, False, False])
        self.assertEqual(f["dq_installments_zero"].tolist(), [False, True, False])
        self.assertEqual(pay["payment_installments"].tolist()[1], 0)  # recorded zero kept (D-16)

    def test_review_flags(self):
        reviews = pd.DataFrame({
            "review_id": s(["r1", "r1", "r2", "r3", "r4"]),
            "order_id": s(["o1", "o2", "o1", "o1", "o2"]),
            "review_score": pd.array([5, 5, 3, 3, 1], dtype="Int64"),
            "review_comment_title": s([None, None, "t", "t", None]),
            "review_comment_message": s(["m", "m", None, None, None]),
            "review_creation_date": ts(["2017-01-02 00:00:00", "2017-01-02 00:00:00", "2017-01-03 00:00:00",
                                        "2017-01-03 00:00:00", "2017-01-01 23:00:00"]),
        })
        orders = pd.DataFrame({"order_id": s(["o1", "o2"]),
                               fl.P: ts(["2017-01-01 10:00:00", "2017-01-02 15:00:00"]),
                               fl.D: ts(["2017-01-05 10:00:00", "2017-01-02 16:00:00"])})
        f = fl.review_flags(reviews, orders)
        self.assertEqual(f["has_comment_title"].tolist(), [False, False, True, True, False])
        self.assertEqual(f["has_comment_message"].tolist(), [True, True, False, False, False])
        self.assertEqual(f["dq_review_id_shared"].tolist(), [True, True, False, False, False])
        self.assertEqual(f["dq_review_content_duplicate"].tolist(), [False, False, False, True, False])
        # day level: same-day review is not "before purchase"; 2017-01-01 23:00 < 2017-01-02 is
        self.assertEqual(f["dq_review_before_purchase"].tolist(), [False, False, False, False, True])
        self.assertEqual(f["dq_review_before_delivery"].tolist(), [True, False, True, True, True])


class ProductFlagTests(unittest.TestCase):
    def frame(self):
        na = None
        return pd.DataFrame({
            "product_id": s(["p1", "p2", "p3", "p4"]),
            "product_category_name": s(["cama", None, "pc_gamer", "cama"]),
            "product_weight_g": pd.array([0, 500, na, 1000], dtype="Int64"),
            "product_length_cm": pd.array([30, 16, 10, 2], dtype="Int64"),
            "product_height_cm": pd.array([25, 2, 10, 2], dtype="Int64"),
            "product_width_cm": pd.array([30, 11, 10, 2], dtype="Int64"),
        })

    def metadata(self, products):
        f = pd.DataFrame({"product_id": products["product_id"]})
        for c in ("dq_missing_category", "dq_listing_metadata_missing"):
            f[c] = [False, True, False, True]  # deliberately not derivable from this frame: proves reuse
        return f

    def test_d09_flags_and_valid_weight_column(self):
        p = self.frame()
        f = fl.product_flags(p, self.metadata(p))
        self.assertEqual(f["dq_weight_zero"].tolist(), [True, False, False, False])
        self.assertTrue(pd.isna(f["weight_g_valid"].iloc[0]))
        self.assertEqual(f["weight_g_valid"].iloc[1], 500)
        self.assertEqual(p["product_weight_g"].iloc[0], 0)  # original kept
        # densities: 0, 500/352 = 1.42, missing weight, 1000/8 = 125
        self.assertEqual(f["dq_density_suspicious"].tolist(), [False, False, False, True])
        self.assertEqual(f["dq_dims_16x2x11"].tolist(), [False, True, False, False])

    def test_metadata_flags_are_reused_not_recomputed(self):
        p = self.frame()
        f = fl.product_flags(p, self.metadata(p))
        self.assertEqual(f["dq_missing_category"].tolist(), [False, True, False, True])

    def test_category_columns_from_map(self):
        p = self.frame()
        cmap = pd.DataFrame({"product_category_name": s(["cama", "pc_gamer"]), "category_en_clean": s(["bed", "pc_gamer"])})
        out = fl.product_categories(p, cmap, UNCATEGORISED_LABEL)
        self.assertEqual(out["category_en_clean"].tolist()[0], "bed")
        self.assertTrue(pd.isna(out["category_en_clean"].iloc[1]))
        self.assertEqual(out["category_reporting"].tolist(), ["bed", UNCATEGORISED_LABEL, "pc_gamer", "bed"])
        self.assertTrue(pd.isna(p["product_category_name"].iloc[1]))  # raw value stays missing


# --------------------------------------------------------------------------- reference tables
class CategoryMapTests(unittest.TestCase):
    def translation(self, **override):
        rows = {"casa_conforto": "home_confort", "casa_conforto_2": "home_comfort_2",
                "construcao_ferramentas_jardim": "costruction_tools_garden",
                "construcao_ferramentas_ferramentas": "costruction_tools_tools",
                "fashion_roupa_feminina": "fashio_female_clothing", "cama_mesa_banho": "bed_bath_table", **override}
        return pd.DataFrame({"product_category_name": s(list(rows)), "product_category_name_english": s(list(rows.values()))})

    def products(self):
        return pd.DataFrame({"product_category_name": s(["cama_mesa_banho", "pc_gamer", None,
                                                         "portateis_cozinha_e_preparadores_de_alimentos"])})

    def test_corrections_additions_twins_and_unique_keys(self):
        cmap, notes = build_category_map(self.products(), self.translation())
        m = cmap.set_index("product_category_name")
        self.assertTrue(cmap["product_category_name"].is_unique)
        self.assertEqual(len(cmap), 8)
        self.assertEqual(m.loc["casa_conforto", "category_en_clean"], "home_comfort")
        self.assertEqual(m.loc["casa_conforto", "category_en_source"], "home_confort")
        self.assertEqual(m.loc["fashion_roupa_feminina", "category_en_clean"], "fashion_female_clothing")
        self.assertTrue(m.loc["pc_gamer", "is_added_by_project"])
        self.assertTrue(pd.isna(m.loc["pc_gamer", "category_en_source"]))
        self.assertEqual(m.loc["pc_gamer", "note"], NOTE_ADDED)
        self.assertEqual(m.loc["casa_conforto_2", "category_en_clean"], "home_comfort_2")
        self.assertEqual(m.loc["casa_conforto_2", "note"], NOTE_TWIN)
        self.assertFalse(m.loc["cama_mesa_banho", "is_added_by_project"])
        self.assertEqual(notes, [])

    def test_stale_overrides_are_rejected(self):
        with self.assertRaises(CategoryMapError):
            build_category_map(self.products(), self.translation(casa_conforto="home_comfort"))
        with self.assertRaises(CategoryMapError):
            build_category_map(self.products(), self.translation(pc_gamer="gaming_pc"))

    def test_override_for_absent_category_is_reported(self):
        tr = self.translation()
        tr = tr[tr["product_category_name"] != "fashion_roupa_feminina"]
        _, notes = build_category_map(self.products(), tr)
        self.assertEqual(len(notes), 1)
        self.assertIn("fashion_roupa_feminina", notes[0])


class ZipLookupTests(unittest.TestCase):
    def build(self):
        pts = [  # zip, lat, lng, raw_row_count, outside
            ("01001", -23.0, -46.0, 100, False),   # heavily repeated: must not pull the median
            ("01001", -23.2, -46.2, 1, False),
            ("01001", -23.4, -46.6, 1, False),
            ("01001", -23.8, -46.8, 1, False),
            ("01001", 40.0, -8.0, 3, True),          # outside Brazil: excluded
            ("02002", 41.0, -8.0, 2, True),          # only outside points
        ]
        points = pd.DataFrame(pts, columns=["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                                            "raw_row_count", "dq_outside_brazil"])
        points["geolocation_zip_code_prefix"] = points["geolocation_zip_code_prefix"].astype("string")
        rows = pd.DataFrame({"geolocation_zip_code_prefix": s(["01001", "01001", "02002"]),
                             "geolocation_state": pd.Categorical(["SP", "RJ", "SP"])})
        return build_zip_lookup(points, rows).set_index("geolocation_zip_code_prefix")

    def test_unweighted_component_wise_median_over_valid_points(self):
        z = self.build().loc["01001"]
        self.assertAlmostEqual(z["lat_centroid"], -23.3)   # mean of the two middle values (-23.2, -23.4)
        self.assertAlmostEqual(z["lng_centroid"], -46.4)
        self.assertEqual((z["n_points_used"], z["n_points_excluded_outside_brazil"], z["raw_rows_represented"]), (4, 1, 103))
        self.assertTrue(z["dq_zip_multi_state"])
        self.assertFalse(z["dq_zip_no_valid_point"])

    def test_zip_without_valid_point_is_kept_with_missing_centroid(self):
        z = self.build().loc["02002"]
        self.assertTrue(pd.isna(z["lat_centroid"]) and pd.isna(z["lng_centroid"]))
        self.assertTrue(z["dq_zip_no_valid_point"])
        self.assertEqual(z["n_points_used"], 0)

    def test_attach_geolocation_flags_absent_and_invalid_zips(self):
        zl = self.build().reset_index()
        cust = pd.DataFrame({"customer_zip_code_prefix": s(["01001", "02002", "99999", "01001"])})
        g = sd.attach_geolocation(cust, "customer_zip_code_prefix", zl)
        self.assertEqual(len(g), 4)
        self.assertEqual(g["dq_no_geolocation"].tolist(), [False, True, True, False])


class ExtendAndHashTests(unittest.TestCase):
    def test_extend_appends_declared_columns_only(self):
        base = pd.DataFrame({"payment_value": [0.0, 1.0]})
        added = pd.DataFrame({"dq_installments_zero": [False, True], "dq_payment_zero": [True, False]})
        out = extend(base, "payments", added)
        self.assertEqual(list(out.columns), ["payment_value", *added_column_names("payments")])
        with self.assertRaises(ValueError):
            extend(base, "payments", added.assign(extra=1))
        with self.assertRaises(ValueError):
            extend(base.assign(dq_payment_zero=True), "payments", added)

    def test_every_added_column_has_lineage(self):
        for table, cols in ADDED_COLUMNS.items():
            for name, spec in cols:
                self.assertTrue(spec.sources and spec.rule and spec.decision, f"{table}.{name}")

    def test_content_hash_is_deterministic_and_sensitive(self):
        a = pd.DataFrame({"x": s(["01037", None]), "y": [1.5, 2.0]})
        self.assertEqual(content_hash(a), content_hash(a.copy()))
        self.assertNotEqual(content_hash(a), content_hash(a.assign(y=[1.5, 2.5])))
        self.assertNotEqual(content_hash(a), content_hash(a.astype({"y": "float32"})))


# --------------------------------------------------------------------------- pipeline on fixtures
class PipelineFixtureTests(TempDir):
    def setUp(self):
        super().setUp()
        self.raw = self.dir / "raw"
        self.raw.mkdir()
        write_valid_fixture(self.raw)
        self.staging = self.dir / "staging"
        staged = run_staging(self.raw, self.staging, None, verify_baseline=False)
        self.assertTrue(staged.success)

    def run_clean(self, tag="a", **kw):
        return run_clean(self.raw, kw.get("staging", self.staging), self.dir / tag / "clean", self.dir / tag / "manifest",
                         self.dir / tag / "reports", verify_baseline=False)

    def test_run_writes_outputs_manifest_and_reports(self):
        before = checksum_snapshot(self.raw, DATASETS)
        result = self.run_clean()
        self.assertTrue(result.success, [(c.check_id, c.observed) for c in failed(result.checks)])
        self.assertEqual(sorted(p.stem for p in (self.dir / "a" / "clean").glob("*.parquet")), sorted(CLEAN_OUTPUTS))
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["run_id"], result.run_id)
        self.assertEqual(len(manifest["outputs"]), len(CLEAN_OUTPUTS))
        self.assertEqual(len(manifest["input_registry"]["raw"]), 9)
        self.assertEqual(len(manifest["input_registry"]["staging"]), 12)
        report = json.loads((self.dir / "a" / "reports" / "clean_report.json").read_text(encoding="utf-8"))
        self.assertFalse(any(c["check_class"] == REGRESSION for c in report["checks"]))
        self.assertTrue((self.dir / "a" / "reports" / "clean_report.md").exists())
        self.assertEqual(checksum_snapshot(self.raw, DATASETS), before)

    def test_source_columns_and_rows_identical_to_staging(self):
        self.assertTrue(self.run_clean().success)
        for t in SOURCE_TABLES:
            st = read_parquet(self.staging / f"{t}.parquet")
            cl = read_parquet(self.dir / "a" / "clean" / f"{t}.parquet")
            pd.testing.assert_frame_equal(cl[list(st.columns)], st, check_exact=True, obj=t)
            self.assertEqual(list(cl.columns), list(st.columns) + added_column_names(t))
        lineage = read_lineage(self.dir / "a" / "clean" / "orders.parquet")
        self.assertEqual(lineage["layer"], "clean")
        self.assertIn("dq_carrier_before_purchase", lineage["added_columns"])

    def test_repeated_runs_produce_identical_content(self):
        a, b = self.run_clean("a"), self.run_clean("b")
        self.assertTrue(a.success and b.success)
        self.assertNotEqual(a.run_id, b.run_id)
        self.assertEqual({r.name: r.content_sha256 for r in a.outputs}, {r.name: r.content_sha256 for r in b.outputs})

    def test_stale_staging_blocks_writing(self):
        path = self.raw / DATASETS_BY_NAME["sellers"].filename
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # raw changed after staging
        result = self.run_clean()
        self.assertFalse(result.success)
        self.assertIn("S01.sellers", [c.check_id for c in failed(result.checks)])
        self.assertFalse((self.dir / "a" / "clean").exists())
        self.assertIsNone(result.manifest_path)
        self.assertTrue((self.dir / "a" / "reports" / "clean_report.md").exists())

    def test_missing_staging_outputs_raise(self):
        (self.staging / "zip_lookup.parquet").unlink(missing_ok=True)
        (self.staging / "geolocation_points.parquet").unlink()
        with self.assertRaises(CleanInputError) as ctx:
            self.run_clean()
        self.assertIn("geolocation_points.parquet", str(ctx.exception))

    def test_cli_exit_codes(self):
        common = ["--raw-dir", str(self.raw), "--out-dir", str(self.dir / "o"), "--manifest-dir", str(self.dir / "m"),
                  "--report-dir", str(self.dir / "r"), "--skip-baseline-check"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(common + ["--staging-dir", str(self.staging)]), EXIT_OK)
            self.assertEqual(main(common + ["--staging-dir", str(self.dir / "nowhere")]), EXIT_FAILED)
        self.assertIn("RESULT: SUCCESS", out.getvalue())


# --------------------------------------------------------------------------- real data
@unittest.skipUnless(all((DEFAULT_RAW_DIR / d.filename).is_file() for d in DATASETS), "raw data not present")
class RealDataCleanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.raw_before = checksum_snapshot(DEFAULT_RAW_DIR, DATASETS)
        cls.staging = root / "staging"
        cls.staging_result = run_staging(DEFAULT_RAW_DIR, cls.staging, None)
        cls.out = root / "clean"
        cls.result = run_clean(DEFAULT_RAW_DIR, cls.staging, cls.out, root / "manifest", root / "reports")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def read(self, name):
        return read_parquet(self.out / f"{name}.parquet")

    def test_run_succeeds_with_blocking_and_regression_checks(self):
        self.assertTrue(self.staging_result.success)
        self.assertTrue(self.result.success, [(c.check_id, c.observed) for c in failed(self.result.checks)])
        classes = {c.check_class for c in self.result.checks}
        self.assertTrue({BLOCKING, REGRESSION} <= classes)

    def test_outputs_read_back_with_expected_rows(self):
        expected = {**BASELINE_ROWS, "category_map": 73, "zip_lookup": 19_015}
        for name, n in expected.items():
            self.assertEqual(len(self.read(name)), n, name)

    def test_flag_baselines_from_read_back_files(self):
        for (table, flag), n in BASELINE_CLEAN_FLAGS.items():
            self.assertEqual(int(self.read(table)[flag].sum()), n, flag)
        products = self.read("products")
        for flag, n in BASELINE_PRODUCT_FLAGS.items():
            self.assertEqual(int(products[flag].sum()), n, flag)

    def test_city_keys_and_geolocation_coverage(self):
        self.assertEqual(self.read("customers")["city_key"].nunique(), BASELINE_STRUCTURE["city_key_distinct_customers"])
        self.assertEqual(self.read("sellers")["city_key"].nunique(), BASELINE_STRUCTURE["city_key_distinct_sellers"])
        self.assertEqual(self.read("geolocation")["city_key"].nunique(), BASELINE_STRUCTURE["city_key_distinct_geolocation"])
        self.assertEqual(int(self.read("customers")["dq_no_geolocation"].sum()), 279)  # 278 absent zips + 1 without valid point
        self.assertEqual(int(self.read("sellers")["dq_no_geolocation"].sum()), 7)

    def test_monetary_totals_and_item_grain_unchanged(self):
        items = self.read("order_items")
        self.assertEqual(round(float(items["price"].sum()), 2), 13_591_643.70)
        self.assertEqual(round(float(self.read("payments")["payment_value"].sum()), 2), 16_008_872.12)
        self.assertFalse(items.duplicated(subset=["order_id", "order_item_id"]).any())

    def test_category_map_covers_all_products(self):
        products = self.read("products")
        self.assertEqual(int((products["product_category_name"].notna() & products["category_en_clean"].isna()).sum()), 0)
        self.assertEqual(int((products["category_reporting"] == UNCATEGORISED_LABEL).sum()), 610)

    def test_raw_files_unchanged(self):
        self.assertEqual(checksum_snapshot(DEFAULT_RAW_DIR, DATASETS), self.raw_before)
        self.assertEqual(self.raw_before, {d.name: d.sha256 for d in DATASETS})


if __name__ == "__main__":
    unittest.main()
