"""Tests for the raw data ingestion layer (src/ingestion).

Run from the project root:   python -m unittest discover -s tests -v

* Unit tests use small temporary CSV fixtures and never touch data/raw/.
* Integration tests read the real raw files (read-only) and compare the results with the
  baselines measured in notebooks 01-03. They are skipped if data/raw/ is absent.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.ingestion import (DATASETS, DATASETS_BY_NAME, DEFAULT_RAW_DIR, ZIP_COLUMNS,
                           DatasetLoadError, DatasetSpec, MissingRawFilesError,
                           RawDataIntegrityError, SchemaMismatchError, checksum_snapshot, ingest,
                           load_dataset, profile_dataframe, sha256_file, validate_raw_files)
from src.ingestion.cli import EXIT_INGESTION_ERROR, EXIT_OK, main
from src.ingestion.integrity import verify_unchanged

SAMPLE = {"string": "abc", "Int64": "7", "float64": "1.50"}


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    lines = [",".join(header)] + [",".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_fixture_set(directory: Path, specs=DATASETS) -> None:
    """Write one small valid CSV per spec (two rows, one with all values empty)."""
    for spec in specs:
        cols = list(spec.columns)
        full = [SAMPLE[spec.columns[c]] for c in cols]
        empty = [""] * len(cols)
        write_csv(directory / spec.filename, cols, [full, empty])


def spec(name="demo", columns=None) -> DatasetSpec:
    columns = columns or {"zip": "string", "label": "string", "qty": "Int64", "amount": "float64"}
    return DatasetSpec(name=name, filename=f"{name}.csv", columns=columns, sha256="0" * 64)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# --------------------------------------------------------------------------- schema
class SchemaTests(unittest.TestCase):
    def test_nine_datasets_declared_with_unique_names_and_files(self):
        self.assertEqual(len(DATASETS), 9)
        self.assertEqual(len({d.name for d in DATASETS}), 9)
        self.assertEqual(len({d.filename for d in DATASETS}), 9)

    def test_zip_columns_are_declared_as_string(self):
        for dataset, column in ZIP_COLUMNS.items():
            self.assertEqual(DATASETS_BY_NAME[dataset].columns[column], "string")

    def test_identifier_and_date_columns_are_declared_as_string(self):
        for d in DATASETS:
            for col, dtype in d.columns.items():
                if (col.endswith("_id") and col != "order_item_id") or "_date" in col \
                        or "_timestamp" in col or col.endswith("_at"):
                    self.assertEqual(dtype, "string", f"{d.name}.{col}")

    def test_source_column_names_are_kept_unchanged(self):
        cols = DATASETS_BY_NAME["products"].column_names
        self.assertIn("product_name_lenght", cols)
        self.assertIn("product_description_lenght", cols)


# --------------------------------------------------------------------------- file validation
class FileValidationTests(TempDirTestCase):
    def test_all_missing_files_are_reported_together(self):
        write_fixture_set(self.dir, DATASETS[:7])
        with self.assertRaises(MissingRawFilesError) as ctx:
            validate_raw_files(self.dir)
        self.assertEqual(sorted(ctx.exception.missing), sorted(d.filename for d in DATASETS[7:]))

    def test_missing_directory_is_reported(self):
        with self.assertRaises(MissingRawFilesError):
            validate_raw_files(self.dir / "does_not_exist")

    def test_complete_set_passes(self):
        write_fixture_set(self.dir)
        validate_raw_files(self.dir)  # no exception


# --------------------------------------------------------------------------- loading
class LoadDatasetTests(TempDirTestCase):
    def test_zip_leading_zeros_preserved(self):
        s = spec()
        write_csv(self.dir / s.filename, list(s.columns), [['"01037"', "x", "1", "2.5"], ["09790", "y", "2", "3.0"]])
        df = load_dataset(s, self.dir)
        self.assertEqual(df["zip"].tolist(), ["01037", "09790"])
        self.assertEqual(str(df["zip"].dtype), "string")

    def test_only_empty_fields_become_missing(self):
        s = spec()
        write_csv(self.dir / s.filename, list(s.columns),
                  [["00001", "NA", "1", "1.0"], ["00002", "null", "", ""], ["00003", "", "3", "3.0"]])
        df = load_dataset(s, self.dir)
        self.assertEqual(df.loc[0, "label"], "NA")
        self.assertEqual(df.loc[1, "label"], "null")
        self.assertTrue(pd.isna(df.loc[2, "label"]))
        self.assertTrue(pd.isna(df.loc[1, "qty"]))
        self.assertTrue(pd.isna(df.loc[1, "amount"]))

    def test_integer_column_with_missing_stays_integer(self):
        s = spec()
        write_csv(self.dir / s.filename, list(s.columns), [["00001", "a", "", "1.0"], ["00002", "b", "40", "1.0"]])
        df = load_dataset(s, self.dir)
        self.assertEqual(str(df["qty"].dtype), "Int64")
        self.assertEqual(df.loc[1, "qty"], 40)

    def test_header_mismatch_raises_schema_error(self):
        s = spec()
        write_csv(self.dir / s.filename, ["zip", "label", "quantity", "amount"], [["00001", "a", "1", "1.0"]])
        with self.assertRaises(SchemaMismatchError):
            load_dataset(s, self.dir)

    def test_column_order_change_raises_schema_error(self):
        s = spec()
        write_csv(self.dir / s.filename, ["label", "zip", "qty", "amount"], [["a", "00001", "1", "1.0"]])
        with self.assertRaises(SchemaMismatchError):
            load_dataset(s, self.dir)

    def test_unparseable_value_raises_load_error(self):
        s = spec()
        write_csv(self.dir / s.filename, list(s.columns), [["00001", "a", "seven", "1.0"]])
        with self.assertRaises(DatasetLoadError) as ctx:
            load_dataset(s, self.dir)
        self.assertIn(s.filename, str(ctx.exception))

    def test_empty_file_raises_schema_error(self):
        s = spec()
        (self.dir / s.filename).write_text("", encoding="utf-8")
        with self.assertRaises(SchemaMismatchError):
            load_dataset(s, self.dir)

    def test_missing_file_raises(self):
        with self.assertRaises(MissingRawFilesError):
            load_dataset(spec(), self.dir)

    def test_utf8_byte_order_mark_is_not_part_of_first_column_name(self):
        s = spec()
        content = ",".join(s.columns) + "\n00001,a,1,1.0\n"
        (self.dir / s.filename).write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
        df = load_dataset(s, self.dir)
        self.assertEqual(list(df.columns), list(s.columns))
        self.assertEqual(df.loc[0, "zip"], "00001")


# --------------------------------------------------------------------------- integrity
class IntegrityTests(TempDirTestCase):
    def test_sha256_known_value(self):
        p = self.dir / "f.txt"
        p.write_bytes(b"abc")
        self.assertEqual(sha256_file(p), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_baseline_mismatch_raises(self):
        write_fixture_set(self.dir)  # fixture checksums cannot match the real baseline
        with self.assertRaises(RawDataIntegrityError):
            ingest(self.dir, verify_baseline=True)

    def test_change_detection(self):
        with self.assertRaises(RawDataIntegrityError):
            verify_unchanged({"a": "1", "b": "2"}, {"a": "1", "b": "3"})
        verify_unchanged({"a": "1"}, {"a": "1"})  # no exception

    def test_ingest_does_not_modify_files(self):
        write_fixture_set(self.dir)
        before = checksum_snapshot(self.dir, DATASETS)
        result = ingest(self.dir, verify_baseline=False)
        self.assertEqual(before, checksum_snapshot(self.dir, DATASETS))
        self.assertEqual(result.checksums_before, result.checksums_after)


# --------------------------------------------------------------------------- ingest + profile
class IngestAndProfileTests(TempDirTestCase):
    def test_ingest_loads_every_dataset(self):
        write_fixture_set(self.dir)
        result = ingest(self.dir, verify_baseline=False)
        self.assertEqual(set(result.frames), {d.name for d in DATASETS})
        for d in DATASETS:
            self.assertEqual(list(result.frames[d.name].columns), list(d.column_names))
            self.assertEqual(len(result.frames[d.name]), 2)

    def test_all_failures_are_collected(self):
        write_fixture_set(self.dir)
        for name in ("orders", "products"):
            d = DATASETS_BY_NAME[name]
            write_csv(self.dir / d.filename, ["wrong"], [["x"]])
        with self.assertRaises(DatasetLoadError) as ctx:
            ingest(self.dir, verify_baseline=False)
        msg = str(ctx.exception)
        self.assertIn("2 dataset(s) failed", msg)
        self.assertIn("olist_orders_dataset.csv", msg)
        self.assertIn("olist_products_dataset.csv", msg)

    def test_profile_reports_structure_and_missing(self):
        df = pd.DataFrame({"a": pd.array(["x", None, "z"], dtype="string"), "b": pd.array([1, 2, None], dtype="Int64")})
        p = profile_dataframe("t", "t.csv", df, "f" * 64)
        self.assertEqual((p.rows, p.columns), (3, 2))
        self.assertEqual(p.column_names, ["a", "b"])
        self.assertEqual([c.dtype for c in p.column_profiles], ["string", "Int64"])
        self.assertEqual([c.missing for c in p.column_profiles], [1, 1])
        self.assertEqual(p.total_missing, 2)


# --------------------------------------------------------------------------- CLI
class CliTests(TempDirTestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_success_prints_report_and_writes_json(self):
        write_fixture_set(self.dir)
        report = self.dir / "out" / "report.json"
        code, out, _ = self.run_cli("--raw-dir", str(self.dir), "--skip-baseline-check", "--report-json", str(report))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("RAW DATA INGESTION REPORT", out)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(len(data["datasets"]), 9)
        self.assertTrue(data["raw_unchanged_after_load"])

    def test_missing_files_exit_code(self):
        code, _, err = self.run_cli("--raw-dir", str(self.dir), "--skip-baseline-check")
        self.assertEqual(code, EXIT_INGESTION_ERROR)
        self.assertIn("MissingRawFilesError", err)

    def test_baseline_mismatch_exit_code(self):
        write_fixture_set(self.dir)
        code, _, err = self.run_cli("--raw-dir", str(self.dir))
        self.assertEqual(code, EXIT_INGESTION_ERROR)
        self.assertIn("RawDataIntegrityError", err)


# --------------------------------------------------------------------------- real raw data
EXPECTED_ROWS = {  # notebook 01
    "customers": 99_441, "geolocation": 1_000_163, "orders": 99_441, "order_items": 112_650,
    "payments": 103_886, "reviews": 99_224, "products": 32_951, "sellers": 3_095, "translation": 71,
}
EXPECTED_MISSING = {  # notebooks 01 / 03 section 2.1 — every other column has 0 missing
    ("orders", "order_approved_at"): 160,
    ("orders", "order_delivered_carrier_date"): 1_783,
    ("orders", "order_delivered_customer_date"): 2_965,
    ("reviews", "review_comment_title"): 87_656,
    ("reviews", "review_comment_message"): 58_247,
    ("products", "product_category_name"): 610,
    ("products", "product_name_lenght"): 610,
    ("products", "product_description_lenght"): 610,
    ("products", "product_photos_qty"): 610,
    ("products", "product_weight_g"): 2,
    ("products", "product_length_cm"): 2,
    ("products", "product_height_cm"): 2,
    ("products", "product_width_cm"): 2,
}
EXPECTED_LEADING_ZERO_ZIPS = {"customers": 23_995, "sellers": 1_027, "geolocation": 245_733}  # notebook 03 §4.3


@unittest.skipUnless(all((DEFAULT_RAW_DIR / d.filename).is_file() for d in DATASETS), "raw data not present")
class RealRawDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = ingest()  # verifies the baseline checksums before and after loading

    def test_all_nine_loaded_with_expected_shape(self):
        for d in DATASETS:
            df = self.result.frames[d.name]
            self.assertEqual(len(df), EXPECTED_ROWS[d.name], d.name)
            self.assertEqual(list(df.columns), list(d.column_names), d.name)

    def test_declared_dtypes_applied(self):
        for d in DATASETS:
            df = self.result.frames[d.name]
            for col, dtype in d.columns.items():
                self.assertEqual(str(df[col].dtype), dtype, f"{d.name}.{col}")

    def test_missing_counts_match_notebook_baseline(self):
        for d in DATASETS:
            df = self.result.frames[d.name]
            for col in df.columns:
                self.assertEqual(int(df[col].isna().sum()), EXPECTED_MISSING.get((d.name, col), 0), f"{d.name}.{col}")

    def test_zip_codes_are_five_digit_text_with_leading_zeros(self):
        for name, col in ZIP_COLUMNS.items():
            s = self.result.frames[name][col]
            self.assertTrue(s.str.fullmatch(r"\d{5}").all(), name)
            self.assertEqual(int(s.str.startswith("0").sum()), EXPECTED_LEADING_ZERO_ZIPS[name], name)

    def test_identifiers_are_32_char_hex(self):
        for d in DATASETS:
            for col in d.columns:
                if col.endswith("_id") and col != "order_item_id":
                    s = self.result.frames[d.name][col]
                    self.assertTrue(s.str.fullmatch(r"[0-9a-f]{32}").all(), f"{d.name}.{col}")

    def test_raw_files_match_baseline_and_are_unchanged(self):
        self.assertEqual(self.result.checksums_before, {d.name: d.sha256 for d in DATASETS})
        self.assertEqual(self.result.checksums_after, self.result.checksums_before)


if __name__ == "__main__":
    unittest.main()
