"""Tests for the Gold -> PostgreSQL warehouse load (src/database/loader.py, compare.py; Task 5.2.4).

* Fixture tests build a tiny Gold layer with the real pipeline (raw fixture -> staging -> clean
  -> analytical, with its own manifest) and load it into throwaway schemas.
* One integration test loads the real Gold outputs into a throwaway schema.
The real ``warehouse`` schema is never touched. Database tests are skipped if PostgreSQL is
not configured or reachable.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.analytical import run_analytical
from src.analytical.pipeline import DEFAULT_ANALYTICAL_DIR
from src.clean import run_clean
from src.database import loader
from src.database.compare import validate_loaded
from src.database.loader import GoldSourceError, LoadValidationError, load_warehouse, locate_gold
from src.database.warehouse import LOAD_ORDER, create_warehouse
from src.staging import run_staging
from tests.test_staging import write_valid_fixture
from tests.test_warehouse import DB


@contextlib.contextmanager
def throwaway_warehouse(engine):
    tag = uuid.uuid4().hex[:8]
    w, a = f"test_wh_{tag}", f"test_an_{tag}"
    create_warehouse(engine, w, a)
    try:
        yield w
    finally:
        with engine.begin() as conn:
            for s in (w, a):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{s}" CASCADE'))


def scalar(engine, sql_text, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql_text), params).scalar()


def load_runs(engine, schema):
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT * FROM "{schema}".load_run ORDER BY started_at')).mappings().all()


# --------------------------------------------------------------------------- Gold verification (no database)
class GoldFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        raw = root / "raw"
        raw.mkdir()
        write_valid_fixture(raw)
        assert run_staging(raw, root / "staging", None, verify_baseline=False).success
        assert run_clean(raw, root / "staging", root / "clean", None, None, verify_baseline=False).success
        cls.gold_dir, cls.manifest_dir = root / "gold", root / "manifest"
        assert run_analytical(raw, root / "clean", cls.gold_dir, cls.manifest_dir, None, verify_baseline=False).success
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def gold(self):
        return locate_gold(self.gold_dir, self.manifest_dir)


class LocateGoldTests(GoldFixture):
    def test_locates_the_run_and_verifies_the_manifest(self):
        g = self.gold()
        self.assertEqual(set(g.frames), set(LOAD_ORDER))
        self.assertEqual(g.manifest_path.name, f"{g.run_id}.json")
        self.assertEqual(len(g.manifest_sha256), 64)
        self.assertEqual(set(g.content_hashes), set(LOAD_ORDER))

    def test_rejects_a_file_that_does_not_match_the_manifest(self):
        copy = self.root / "gold_tampered"
        shutil.copytree(self.gold_dir, copy)
        p = copy / "dim_sellers.parquet"
        df = pd.read_parquet(p)
        df.loc[0, "seller_city"] = "changed"
        from src.staging import read_lineage, write_parquet
        write_parquet(df, p, read_lineage(self.gold_dir / "dim_sellers.parquet"))  # same lineage, other content
        with self.assertRaises(GoldSourceError) as ctx:
            locate_gold(copy, self.manifest_dir)
        self.assertIn("dim_sellers", str(ctx.exception))

    def test_rejects_missing_manifest_and_sub_cent_money(self):
        with self.assertRaises(GoldSourceError):
            locate_gold(self.gold_dir, self.root / "no_manifests")
        frames = dict(self.gold().frames)
        frames["fact_payments"] = frames["fact_payments"].assign(payment_value=1.005)
        self.assertTrue(any("finer than one cent" in p for p in loader._schema_problems(frames)))

    def test_missing_values_become_sql_null(self):
        s = pd.Series([1.5, np.nan], dtype="float64")
        self.assertEqual(loader.column_values(s, "double precision"), [1.5, None])
        self.assertEqual(loader.column_values(pd.Series([True, pd.NA], dtype="boolean"), "boolean"), [True, None])
        self.assertEqual(loader.column_values(pd.Series([1, pd.NA], dtype="Int64"), "integer"), [1, None])
        self.assertEqual(loader.column_values(pd.Series([1794.9600000000003, np.nan]), "numeric(12,2)")[0].as_tuple().exponent, -2)
        ts = pd.Series(pd.to_datetime(["2017-01-01 10:00:00", None]))
        self.assertIsNone(loader.column_values(ts, "timestamp(0) without time zone")[1])


# --------------------------------------------------------------------------- loading (database)
@unittest.skipUnless(DB, "PostgreSQL connection not configured or not reachable")
class LoadTests(GoldFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from src.database import get_engine
        cls.engine = get_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        super().tearDownClass()

    def test_initial_load_matches_gold_and_records_lineage(self):
        g = self.gold()
        with throwaway_warehouse(self.engine) as w:
            r = load_warehouse(g, self.engine, schema=w, check_baselines=False)
            self.assertTrue(r["validation"]["ok"], r["validation"]["problems"])
            self.assertEqual(r["rows_loaded"], g.row_counts)
            post = validate_loaded(self.engine, g.frames, w, check_baselines=False)  # fresh connection
            self.assertTrue(post["ok"], post["problems"])
            runs = load_runs(self.engine, w)
            self.assertEqual(len(runs), 1)
            run = runs[0]
            self.assertEqual((run["load_id"], run["gold_run_id"], run["gold_manifest_sha256"]),
                             (r["load_id"], g.run_id, g.manifest_sha256))
            self.assertTrue(run["gold_manifest_path"].endswith(f"{g.run_id}.json"))
            self.assertEqual(run["row_counts"], g.row_counts)
            self.assertEqual(run["content_hashes"], g.content_hashes)
            self.assertLessEqual(run["started_at"], run["finished_at"])

    def test_repeated_refresh_replaces_data_without_duplicates(self):
        g = self.gold()
        with throwaway_warehouse(self.engine) as w:
            first = load_warehouse(g, self.engine, schema=w, check_baselines=False)
            second = load_warehouse(g, self.engine, schema=w, check_baselines=False)
            self.assertEqual(first["rows_loaded"], second["rows_loaded"])
            for t in LOAD_ORDER:
                self.assertEqual(scalar(self.engine, f'SELECT count(*) FROM "{w}"."{t}"'), len(g.frames[t]), t)
            self.assertEqual([x["load_id"] for x in load_runs(self.engine, w)], [first["load_id"], second["load_id"]])
            self.assertTrue(validate_loaded(self.engine, g.frames, w, check_baselines=False)["ok"])

    def test_failure_mid_load_rolls_back_everything(self):
        g = self.gold()
        with throwaway_warehouse(self.engine) as w:
            good = load_warehouse(g, self.engine, schema=w, check_baselines=False)
            real_copy = loader.copy_table

            def failing_copy(raw, schema, table, df):
                if table == "fact_reviews":  # the last table: everything before it was already copied
                    raise RuntimeError("simulated failure")
                return real_copy(raw, schema, table, df)

            with mock.patch.object(loader, "copy_table", failing_copy), self.assertRaises(RuntimeError):
                load_warehouse(g, self.engine, schema=w, check_baselines=False)
            self.assertTrue(validate_loaded(self.engine, g.frames, w, check_baselines=False)["ok"])  # previous data intact
            self.assertEqual([x["load_id"] for x in load_runs(self.engine, w)], [good["load_id"]])  # no new history row

    def test_failed_validation_or_constraint_leaves_no_partial_load(self):
        g = self.gold()
        with throwaway_warehouse(self.engine) as w:
            bad = {"ok": False, "problems": ["simulated mismatch"], "tables": {}}
            with mock.patch.object(loader, "compare_with_gold", return_value=bad), self.assertRaises(LoadValidationError):
                load_warehouse(g, self.engine, schema=w, check_baselines=False)
            frames = dict(g.frames)
            frames["fact_payments"] = frames["fact_payments"].assign(order_id="f" * 32)  # FK violation
            broken = loader.GoldSource(g.run_id, g.manifest_path, g.manifest_sha256, frames, g.content_hashes)
            with self.assertRaises(Exception):
                load_warehouse(broken, self.engine, schema=w, check_baselines=False)
            for t in (*LOAD_ORDER, "load_run"):
                self.assertEqual(scalar(self.engine, f'SELECT count(*) FROM "{w}"."{t}"'), 0, t)

    def test_nulls_are_stored_as_sql_null_not_nan(self):
        g = self.gold()
        frames = dict(g.frames)
        fo = frames["fact_orders"].copy()
        fo.loc[0, ["customer_lat_centroid", "approval_hours"]] = np.nan
        fo.loc[0, "order_approved_at"] = pd.NaT
        fo["is_late"] = fo["is_late"].astype("boolean")
        fo.loc[0, "is_late"] = pd.NA
        frames["fact_orders"] = fo
        g2 = loader.GoldSource(g.run_id, g.manifest_path, g.manifest_sha256, frames, g.content_hashes)
        with throwaway_warehouse(self.engine) as w:
            r = load_warehouse(g2, self.engine, schema=w, check_baselines=False)
            self.assertTrue(r["validation"]["ok"], r["validation"]["problems"])
            oid = fo.loc[0, "order_id"]
            q = f'SELECT customer_lat_centroid IS NULL, approval_hours IS NULL, order_approved_at IS NULL, ' \
                f'is_late IS NULL FROM "{w}".fact_orders WHERE order_id = :o'
            with self.engine.connect() as conn:
                self.assertEqual(tuple(conn.execute(text(q), {"o": oid}).one()), (True, True, True, True))
            nan = scalar(self.engine, f"SELECT count(*) FROM \"{w}\".fact_orders WHERE customer_lat_centroid = 'NaN' "
                                      "OR approval_hours = 'NaN'")
            self.assertEqual(nan, 0)


@unittest.skipUnless(DB and (DEFAULT_ANALYTICAL_DIR / "fact_orders.parquet").is_file(), "PostgreSQL or Gold not available")
class RealGoldLoadTests(unittest.TestCase):
    def test_real_gold_loads_and_equals_gold_with_baseline_totals(self):
        from src.database import get_engine
        engine = get_engine()
        try:
            g = locate_gold()
            with throwaway_warehouse(engine) as w:
                r = load_warehouse(g, engine, schema=w, check_baselines=True)
                self.assertTrue(r["validation"]["ok"], r["validation"]["problems"])
                self.assertEqual(sum(r["rows_loaded"].values()), 538_432)
                self.assertEqual(r["validation"]["tables"]["fact_payments"]["money_totals"]["payment_value"], "16008872.12")
                self.assertEqual(scalar(engine, f'SELECT count(*) FROM "{w}".fact_orders WHERE items_price_total IS NULL'), 775)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
