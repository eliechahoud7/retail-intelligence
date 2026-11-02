"""Reconcile the PySpark results with PostgreSQL (Phase 6, Milestone A). Windows / project environment.

    .venv\\Scripts\\python.exe -m src.spark.reconcile [--results DIR]

Reads the Parquet results written by ``src.spark.jobs`` and compares them with trusted outputs that
were built independently of Spark (read-only queries; nothing is created or changed):

* category_sales_monthly  - per category vs ``analytics.category_performance``; per month vs
  ``analytics.sales_daily``; per cell vs the existing ``category_monthly_ranking`` query (top 5 per
  month in the candidate window);
* delivery_monthly        - per month vs ``analytics.delivery_monthly`` (counts exact; rates and
  durations within the view's rounding: 4 dp / 2 dp);
* payment_methods_monthly - per cell vs the dbt mart ``dbt_marts.mart_payment_methods_monthly`` (exact).

First checks that the warehouse holds the Gold run on disk that Spark read (``warehouse.load_run``).
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from src.analytical.pipeline import DEFAULT_ANALYTICAL_DIR
from src.clean.pipeline import DEFAULT_MANIFEST_DIR
from src.database.connection import get_engine
from src.database.loader import locate_gold
from src.database.queries import run_query

RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "processed" / "spark" / "results"


def _records(df: pd.DataFrame, key: list[str], cols: list[str]) -> dict:
    return {tuple(r[k] for k in key): tuple(r[c] for c in cols) for r in df.to_dict("records")}


def _num(v):
    """Normalise a value for comparison: NULL -> None, bool stays bool, numbers -> Decimal."""
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (bool, Decimal)) or type(v).__name__ == "bool_":
        return bool(v) if not isinstance(v, Decimal) else v
    return Decimal(str(v))


def compare(label: str, spark: dict, trusted: dict, tolerances: tuple | None = None) -> dict:
    """Exact (or per-column tolerance) comparison of two {key: values} mappings."""
    problems = []
    if set(spark) != set(trusted):
        problems.append(f"keys: only Spark {sorted(set(spark) - set(trusted))[:5]}, only trusted {sorted(set(trusted) - set(spark))[:5]}")
    for k in sorted(set(spark) & set(trusted)):
        for i, (s, t) in enumerate(zip(spark[k], trusted[k])):
            s, t = _num(s), _num(t)
            tol = tolerances[i] if tolerances else None
            ok = s == t if (tol is None or s is None or t is None) else abs(s - t) <= tol
            if not ok:
                problems.append(f"{k} col {i}: spark={s} trusted={t}")
    return {"check": label, "rows": len(trusted), "ok": not problems, "problems": problems[:10]}


def reconcile(results_dir: Path = RESULTS_DIR) -> list[dict]:
    read = lambda name: pd.read_parquet(results_dir / name)  # noqa: E731
    cat, dlv, pay = read("category_sales_monthly"), read("delivery_monthly"), read("payment_methods_monthly")
    gold = locate_gold(DEFAULT_ANALYTICAL_DIR, DEFAULT_MANIFEST_DIR)
    engine = get_engine()
    checks = []
    try:
        with engine.connect() as c:
            q = lambda sql: pd.read_sql(text(sql), c)  # noqa: E731
            loaded = c.execute(text("SELECT gold_run_id FROM warehouse.load_run ORDER BY finished_at DESC LIMIT 1")).scalar()
            checks.append({"check": "warehouse holds the Gold run Spark read", "rows": 1, "ok": loaded == gold.run_id,
                           "problems": [] if loaded == gold.run_id else [f"Gold on disk {gold.run_id}, warehouse {loaded}"]})

            # 1. category sales: per category (n_orders is additive across months within a category)
            cols = ["n_orders", "units", "items_price_total", "items_freight_total", "n_orders_delivered",
                    "units_delivered", "items_price_total_delivered"]
            per_cat = cat.groupby("category_reporting", as_index=False)[cols].sum()
            view = q(f"SELECT category_reporting, {', '.join(cols)} FROM analytics.category_performance WHERE units > 0")
            checks.append(compare("category_sales_monthly vs analytics.category_performance (per category)",
                                  _records(per_cat, ["category_reporting"], cols), _records(view, ["category_reporting"], cols)))
            # per month (units and totals are additive across categories)
            mcols = ["units", "items_price_total", "items_freight_total", "units_delivered", "items_price_total_delivered"]
            per_month = cat.groupby("year_month", as_index=False)[mcols].sum()
            daily = q(f"SELECT year_month, {', '.join(f'sum({m}) AS {m}' for m in mcols)} FROM analytics.sales_daily "
                      "GROUP BY year_month HAVING sum(units) > 0")
            checks.append(compare("category_sales_monthly vs analytics.sales_daily (per month)",
                                  _records(per_month, ["year_month"], mcols), _records(daily, ["year_month"], mcols)))
        # per cell: the existing ranking query (top 5 categories per month, candidate window only)
        ranking = run_query("category_monthly_ranking", engine=engine)
        rcols = ["n_orders", "units", "items_price_total"]
        spark_cells = _records(cat[cat["in_candidate_window"]], ["year_month", "category_reporting"], rcols)
        trusted_cells = _records(ranking, ["year_month", "category_reporting"], rcols)
        checks.append(compare("category_sales_monthly vs query category_monthly_ranking (top-5 cells)",
                              {k: v for k, v in spark_cells.items() if k in trusted_cells}, trusted_cells))

        with engine.connect() as c:
            # 2. delivery: counts exact; late_rate within 4 dp, durations within 2 dp (view rounding)
            dcols = ["n_orders", "n_orders_delivered", "n_delivery_eligible", "n_delivered_missing_date",
                     "n_canceled_with_delivery_date", "n_late", "n_eligible_without_valid_transit", "late_rate",
                     "avg_purchase_to_delivery_days", "median_purchase_to_delivery_days", "avg_carrier_to_customer_days",
                     "avg_approval_hours", "avg_delivery_vs_estimate_days"]
            tol = (None,) * 7 + (Decimal("0.00005"),) + (Decimal("0.005"),) * 5
            view = pd.read_sql(text(f"SELECT purchase_year_month, {', '.join(dcols)} FROM analytics.delivery_monthly"), c)
            checks.append(compare("delivery_monthly vs analytics.delivery_monthly (Spark SQL)",
                                  _records(dlv, ["purchase_year_month"], dcols), _records(view, ["purchase_year_month"], dcols), tol))

            # 3. payments: every column exact
            pcols = ["in_candidate_window", "n_transactions", "n_orders", "payment_value_total", "month_payment_value_total",
                     "payment_value_share_pct", "avg_installments", "n_zero_installment_transactions", "n_zero_value_transactions"]
            mart = pd.read_sql(text(f"SELECT purchase_year_month, payment_type, {', '.join(pcols)} "
                                    "FROM dbt_marts.mart_payment_methods_monthly"), c)
            key = ["purchase_year_month", "payment_type"]
            checks.append(compare("payment_methods_monthly vs dbt mart_payment_methods_monthly",
                                  _records(pay, key, pcols), _records(mart, key, pcols)))
    finally:
        engine.dispose()
    return checks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.spark.reconcile", description=__doc__.splitlines()[0])
    p.add_argument("--results", type=Path, default=RESULTS_DIR)
    checks = reconcile(p.parse_args(argv).results)
    for ch in checks:
        print(f"  {'ok  ' if ch['ok'] else 'FAIL'} {ch['check']} ({ch['rows']} rows)")
        for prob in ch["problems"]:
            print(f"       {prob}")
    ok = all(ch["ok"] for ch in checks)
    print(f"  RECONCILIATION: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
