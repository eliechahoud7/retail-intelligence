"""Tests for the staging layer (src/staging).

Run from the project root:   python -m unittest discover -s tests -t . -v

* Unit tests use small in-memory frames or temporary fixture files.
* Integration tests run the full staging pipeline on the real raw data into a temporary
  directory (data/staging/ is not touched) and are skipped if data/raw/ is absent.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.ingestion import DATASETS, DATASETS_BY_NAME, DEFAULT_RAW_DIR, ZIP_COLUMNS
from src.staging import (build_geolocation_points, build_geolocation_rows, build_product_metadata_flags,
                         group_equivalence, parse_datetime, read_lineage, read_parquet, run_staging,
                         stage_dataset, write_parquet)
from src.staging.cli import EXIT_FAILED, EXIT_OK, main
from src.staging.schema import BASELINE_PRODUCT_FLAGS, BASELINE_ROWS, DATE_COLUMNS
from src.staging.validation import BLOCKING, REGRESSION, failed
from src.staging.writer import roundtrip_differences


def s(values, dtype="string"):
    return pd.Series(values, dtype=dtype)


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# --------------------------------------------------------------------------- dates
class DateParsingTests(unittest.TestCase):
    def test_explicit_format_parses_timezone_naive(self):
        parsed, coerced = parse_datetime(s(["2017-10-02 10:56:33", "2018-01-18 00:00:00"]))
        self.assertTrue(pd.api.types.is_datetime64_dtype(parsed))
        self.assertIsNone(parsed.dt.tz)
        self.assertEqual(parsed.iloc[0], pd.Timestamp("2017-10-02 10:56:33"))
        self.assertEqual(coerced, 0)

    def test_missing_stays_missing_and_is_not_counted_as_coerced(self):
        parsed, coerced = parse_datetime(s(["2017-10-02 10:56:33", None]))
        self.assertTrue(pd.isna(parsed.iloc[1]))
        self.assertEqual(coerced, 0)

    def test_invalid_values_are_coerced_and_counted(self):
        parsed, coerced = parse_datetime(s(["2018-02-30 00:00:00", "02/10/2017 10:00:00", "2017-10-02 10:56:33"]))
        self.assertEqual(coerced, 2)
        self.assertTrue(parsed.iloc[:2].isna().all())

    def test_stage_dataset_parses_all_declared_date_columns(self):
        cols = list(DATASETS_BY_NAME["orders"].columns)
        row = ["a" * 32, "b" * 32, "delivered"] + ["2017-10-02 10:56:33"] * 5
        df = pd.DataFrame([row], columns=cols).astype("string")
        staged = stage_dataset("orders", df)
        for col in DATE_COLUMNS["orders"]:
            self.assertTrue(pd.api.types.is_datetime64_dtype(staged.frame[col]), col)
        self.assertEqual(set(staged.coerced_dates.values()), {0})


# --------------------------------------------------------------------------- structure
class StructuralTransformTests(unittest.TestCase):
    def test_only_approved_columns_are_renamed(self):
        cols = list(DATASETS_BY_NAME["products"].columns)
        df = pd.DataFrame([[None] * len(cols)], columns=cols)
        renamed = list(stage_dataset("products", df).frame.columns)
        approved = {"product_name_lenght": "product_name_length",
                    "product_description_lenght": "product_description_length"}
        self.assertEqual(renamed, [approved.get(c, c) for c in cols])  # same order, only 2 names changed
        for other in ("orders", "customers", "geolocation", "order_items", "payments", "reviews", "sellers", "translation"):
            ocols = list(DATASETS_BY_NAME[other].columns)
            odf = pd.DataFrame(columns=ocols).astype({c: "string" for c in ocols})
            self.assertEqual(list(stage_dataset(other, odf).frame.columns), ocols, other)

    def test_zip_and_identifiers_remain_unchanged_strings(self):
        df = pd.DataFrame({"customer_id": s(["06b8999e2fba1a1fbc88172c00ba8bc7"]),
                           "customer_unique_id": s(["861eff4711a542e4b93843c6dd7febb0"]),
                           "customer_zip_code_prefix": s(["01037"]), "customer_city": s(["franca"]),
                           "customer_state": s(["SP"])})
        out = stage_dataset("customers", df).frame
        self.assertEqual(out.loc[0, "customer_zip_code_prefix"], "01037")
        self.assertEqual(str(out["customer_zip_code_prefix"].dtype), "string")
        self.assertEqual(str(out["customer_id"].dtype), "string")
        self.assertEqual(out.loc[0, "customer_id"], "06b8999e2fba1a1fbc88172c00ba8bc7")
        self.assertEqual(str(out["customer_state"].dtype), "category")

    def test_missing_values_preserved_across_types(self):
        df = pd.DataFrame({"order_id": s(["x", "y"]), "payment_sequential": pd.array([1, None], dtype="Int64"),
                           "payment_type": s(["boleto", None]), "payment_installments": pd.array([None, 2], dtype="Int64"),
                           "payment_value": pd.Series([1.5, None], dtype="float64")})
        out = stage_dataset("payments", df).frame
        self.assertEqual(out.isna().sum().tolist(), df.isna().sum().tolist())

    def test_input_frame_is_not_mutated(self):
        df = pd.DataFrame({"order_id": s(["x"]), "order_item_id": pd.array([1], dtype="Int64"),
                           "product_id": s(["p"]), "seller_id": s(["s"]),
                           "shipping_limit_date": s(["2017-09-19 09:45:35"]), "price": [58.9], "freight_value": [13.29]})
        before = df.copy()
        stage_dataset("order_items", df)
        pd.testing.assert_frame_equal(df, before)


# --------------------------------------------------------------------------- product flags
class ProductFlagTests(unittest.TestCase):
    def frame(self):
        na = None
        rows = [  # category, name, desc, photos, weight, length, height, width
            ["cat", 40, 287, 1, 225, 16, 10, 14],    # complete
            [na, na, na, na, 225, 16, 10, 14],       # listing group missing
            ["cat", 40, 287, 1, na, na, na, na],     # physical group missing
            [na, na, na, na, na, na, na, na],        # everything missing
        ]
        cols = ["product_category_name", "product_name_length", "product_description_length", "product_photos_qty",
                "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]
        df = pd.DataFrame(rows, columns=cols)
        df.insert(0, "product_id", s(["p1", "p2", "p3", "p4"]))
        return df

    def test_field_and_row_level_flags(self):
        f = build_product_metadata_flags(self.frame())
        self.assertEqual(f["dq_missing_category"].tolist(), [False, True, False, True])
        self.assertEqual(f["dq_missing_weight_g"].tolist(), [False, False, True, True])
        self.assertEqual(f["dq_listing_metadata_missing"].tolist(), [False, True, False, True])
        self.assertEqual(f["dq_physical_attributes_missing"].tolist(), [False, False, True, True])
        self.assertEqual(f["dq_all_attributes_missing"].tolist(), [False, False, False, True])
        self.assertEqual(len(f.columns), 1 + len(BASELINE_PRODUCT_FLAGS))
        self.assertTrue(all(str(f[c].dtype) == "bool" for c in f.columns[1:]))

    def test_row_flag_requires_all_fields_and_equivalence_detects_partial_pattern(self):
        df = self.frame()
        df.loc[0, "product_photos_qty"] = None  # partial: only one listing field missing
        f = build_product_metadata_flags(df)
        self.assertFalse(f.loc[0, "dq_listing_metadata_missing"])
        any_n, all_n = group_equivalence(f)["listing"]
        self.assertNotEqual(any_n, all_n)


# --------------------------------------------------------------------------- geolocation
class GeolocationTests(unittest.TestCase):
    def frame(self):
        rows = [
            ("01037", -23.5, -46.6, "sao paulo", "SP"),
            ("01037", -23.5, -46.6, "sao paulo", "SP"),   # exact duplicate
            ("01037", -23.5, -46.6, "são paulo", "SP"),   # same point, other spelling
            ("01037", -23.6, -46.7, "sao paulo", "SP"),
            ("28165", 41.6, -8.4, "vila nova", "RJ"),      # outside Brazil
        ]
        df = pd.DataFrame(rows, columns=["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                                         "geolocation_city", "geolocation_state"])
        for c in ("geolocation_zip_code_prefix", "geolocation_city"):
            df[c] = df[c].astype("string")
        return stage_dataset("geolocation", df).frame

    def test_distinct_full_rows_with_occurrence_counts(self):
        rows = build_geolocation_rows(self.frame())
        self.assertEqual(len(rows), 4)
        self.assertEqual(int(rows["occurrence_count"].sum()), 5)
        dup = rows[(rows["geolocation_city"] == "sao paulo") & (rows["geolocation_lat"] == -23.5)]
        self.assertEqual(int(dup["occurrence_count"].iloc[0]), 2)
        self.assertEqual(rows["geolocation_zip_code_prefix"].tolist()[0], "01037")

    def test_distinct_points_count_each_coordinate_once(self):
        points = build_geolocation_points(build_geolocation_rows(self.frame()))
        self.assertEqual(len(points), 3)
        p = points[(points["geolocation_lat"] == -23.5)].iloc[0]
        self.assertEqual((p["raw_row_count"], p["n_full_rows"], p["n_city_spellings"], p["n_states"]), (3, 2, 2, 1))
        self.assertEqual(int(points["raw_row_count"].sum()), 5)
        self.assertEqual(points["dq_outside_brazil"].tolist(), [False, False, True])

    def test_no_centroid_columns(self):
        points = build_geolocation_points(build_geolocation_rows(self.frame()))
        self.assertFalse(any("centroid" in c for c in points.columns))


# --------------------------------------------------------------------------- parquet
class ParquetRoundTripTests(TempDir):
    def test_roundtrip_preserves_values_dtypes_and_lineage(self):
        df = pd.DataFrame({
            "zip": s(["01037", None]),
            "id": s(["06b8999e2fba1a1fbc88172c00ba8bc7", "x"]),
            "n": pd.array([1, None], dtype="Int64"),
            "amount": [58.9, None],
            "ts": pd.to_datetime(["2017-10-02 10:56:33", None], format="%Y-%m-%d %H:%M:%S"),
            "flag": [True, False],
        })
        df["state"] = stage_dataset("sellers", pd.DataFrame({"seller_id": s(["a", "b"]), "seller_zip_code_prefix": s(["01037", "01046"]),
                                                             "seller_city": s(["c", "d"]), "seller_state": s(["SP", None])})).frame["seller_state"]
        path = write_parquet(df, self.dir / "t.parquet", {"run_id": "abc", "sources": ["x"]})
        self.assertIsNone(roundtrip_differences(df, path))
        back = read_parquet(path)
        self.assertEqual(back.loc[0, "zip"], "01037")
        self.assertEqual(read_lineage(path)["run_id"], "abc")
        self.assertFalse((self.dir / "t.parquet.tmp").exists())

    def test_roundtrip_detects_difference(self):
        df = pd.DataFrame({"a": [1, 2]})
        path = write_parquet(df, self.dir / "t.parquet", {})
        self.assertIsNotNone(roundtrip_differences(pd.DataFrame({"a": [1, 3]}), path))


# --------------------------------------------------------------------------- pipeline on fixtures
VALID = {"string": "x", "Int64": "1", "float64": "1.5"}


def write_valid_fixture(directory: Path) -> None:
    """Small but type-valid raw files (2 rows each) so the pipeline can run end to end."""
    for spec in DATASETS:
        dates = set(DATE_COLUMNS.get(spec.name, ()))
        zip_col = ZIP_COLUMNS.get(spec.name)
        lines = [",".join(spec.columns)]
        for i in range(2):
            vals = []
            for col, dtype in spec.columns.items():
                if col in dates:
                    vals.append(f"2017-10-0{i + 1} 10:56:33")
                elif col == zip_col:
                    vals.append(f"0103{i}")
                elif col.endswith("_id") and col != "order_item_id":
                    vals.append(f"{i:032x}")
                elif col == "product_category_name":
                    vals.append(f"cat{i}")  # unique: it is the translation table's primary key
                elif col.endswith("_lat"):
                    vals.append("-23.5")
                elif col.endswith("_lng"):
                    vals.append("-46.6")
                elif dtype == "Int64":
                    vals.append(str(i + 1))
                else:
                    vals.append(VALID[dtype])
            lines.append(",".join(vals))
        (directory / spec.filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


class PipelineFixtureTests(TempDir):
    def test_run_writes_all_outputs_and_reports(self):
        raw = self.dir / "raw"
        raw.mkdir()
        write_valid_fixture(raw)
        result = run_staging(raw, self.dir / "out", self.dir / "rep", verify_baseline=False)
        self.assertTrue(result.success, [c for c in failed(result.checks)])
        self.assertEqual(len(list((self.dir / "out").glob("*.parquet"))), 12)
        report = json.loads((self.dir / "rep" / "staging_report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["outputs"]), 12)
        self.assertIn("commit", report["git"])
        self.assertFalse(any(c["check_class"] == REGRESSION for c in report["checks"]))

    def test_failed_blocking_check_prevents_writing(self):
        raw = self.dir / "raw"
        raw.mkdir()
        write_valid_fixture(raw)
        path = raw / DATASETS_BY_NAME["orders"].filename
        path.write_text(path.read_text(encoding="utf-8").replace("2017-10-01 10:56:33", "2017-13-01 10:56:33", 1), encoding="utf-8")
        result = run_staging(raw, self.dir / "out", self.dir / "rep", verify_baseline=False)
        self.assertFalse(result.success)
        self.assertTrue(any(c.check_id == "V07.orders" for c in failed(result.checks)))
        self.assertFalse((self.dir / "out").exists())
        self.assertTrue((self.dir / "rep" / "staging_report.md").exists())

    def test_cli_exit_codes(self):
        raw = self.dir / "raw"
        raw.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--raw-dir", str(raw), "--out-dir", str(self.dir / "o"),
                                   "--report-dir", str(self.dir / "r"), "--skip-baseline-check"]), EXIT_FAILED)
            write_valid_fixture(raw)
            self.assertEqual(main(["--raw-dir", str(raw), "--out-dir", str(self.dir / "o"),
                                   "--report-dir", str(self.dir / "r"), "--skip-baseline-check"]), EXIT_OK)
        self.assertIn("RESULT: SUCCESS", out.getvalue())


# --------------------------------------------------------------------------- real data
@unittest.skipUnless(all((DEFAULT_RAW_DIR / d.filename).is_file() for d in DATASETS), "raw data not present")
class RealDataStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "staging"
        cls.result = run_staging(DEFAULT_RAW_DIR, cls.out, Path(cls._tmp.name) / "reports")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_run_succeeds_with_all_checks_passing(self):
        self.assertTrue(self.result.success, [(c.check_id, c.observed) for c in failed(self.result.checks)])
        classes = {c.check_class for c in self.result.checks}
        self.assertTrue({BLOCKING, REGRESSION} <= classes)

    def test_all_twelve_outputs_exist_with_expected_rows(self):
        expected = {**BASELINE_ROWS, "geolocation_rows": 738_332, "geolocation_points": 720_154,
                    "products_metadata_flags": 32_951}
        for name, n in expected.items():
            path = self.out / f"{name}.parquet"
            self.assertTrue(path.is_file(), name)
            self.assertEqual(len(read_parquet(path)), n, name)

    def test_staged_parquet_preserves_zips_ids_and_dates(self):
        customers = read_parquet(self.out / "customers.parquet")
        self.assertEqual(int(customers["customer_zip_code_prefix"].str.startswith("0").sum()), 23_995)
        self.assertTrue(customers["customer_id"].str.fullmatch(r"[0-9a-f]{32}").all())
        orders = read_parquet(self.out / "orders.parquet")
        self.assertTrue(pd.api.types.is_datetime64_dtype(orders["order_purchase_timestamp"]))
        self.assertEqual(int(orders["order_delivered_customer_date"].isna().sum()), 2_965)

    def test_geolocation_occurrence_counts_reconstruct_raw(self):
        rows = read_parquet(self.out / "geolocation_rows.parquet")
        points = read_parquet(self.out / "geolocation_points.parquet")
        self.assertEqual(int(rows["occurrence_count"].sum()), 1_000_163)
        self.assertEqual(int(points["raw_row_count"].sum()), 1_000_163)
        self.assertEqual(int(points.loc[points["dq_outside_brazil"], "raw_row_count"].sum()), 42)

    def test_product_flags_match_baselines(self):
        flags = read_parquet(self.out / "products_metadata_flags.parquet")
        for flag, n in BASELINE_PRODUCT_FLAGS.items():
            self.assertEqual(int(flags[flag].sum()), n, flag)

    def test_lineage_metadata_embedded(self):
        lineage = read_lineage(self.out / "products.parquet")
        self.assertEqual(lineage["source_files"], ["olist_products_dataset.csv"])
        self.assertEqual(lineage["column_name_map"]["product_name_lenght"], "product_name_length")
        self.assertEqual(lineage["run_id"], self.result.run_id)


if __name__ == "__main__":
    unittest.main()
