"""Three analytical workloads on the Gold Parquet outputs with PySpark (Phase 6, Milestone A).

Run in the Spark environment (WSL2) from the project root::

    ~/venvs/retail-spark/bin/python -m src.spark.jobs [--gold-dir DIR] [--out DIR]

Each workload reads Gold Parquet and writes one small Parquet result to ``--out``; the results are
reconciled with PostgreSQL by ``python -m src.spark.reconcile`` (Windows). Business rules are those
of the Gold contract and the existing views / dbt marts, which remain the source of truth:

* ``category_sales_monthly`` - **DataFrame API**. Order lines joined many-to-one to products and dates
  (both broadcast: small dimensions, no shuffle of the fact). Item-based only (Q-03). No fan-out:
  every join is to a primary key.
* ``delivery_monthly`` - **Spark SQL**, the declarative form of ``analytics.delivery_monthly``:
  FILTER aggregates over the D-06 eligible population (status delivered with a delivery date),
  durations averaged where defined (D-08), exact median.
* ``payment_methods_monthly`` - **DataFrame API with a window** (monthly share), the logic of the
  dbt mart ``mart_payment_methods_monthly``: payment-based only, no order lines; average instalments
  exclude zero-instalment rows (D-16).

Money: Gold stores monetary values as float64; each value is rounded to cents and cast to
decimal(12,2) before aggregation (the loader's rule), so totals are exact, not float sums.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = PROJECT_ROOT / "data" / "processed" / "analytical"
RESULTS_DIR = PROJECT_ROOT / "data" / "processed" / "spark" / "results"
WORKLOADS = ("category_sales_monthly", "delivery_monthly", "payment_methods_monthly")


def build_session(app_name: str = "retail-intelligence") -> SparkSession:
    """Local session sized for the WSL2 VM (8 CPUs, 7 GB). UTC keeps Gold's timezone-naive timestamps as is."""
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    spark = (SparkSession.builder.master("local[4]").appName(app_name)
             .config("spark.driver.memory", "3g")
             .config("spark.sql.shuffle.partitions", "8")          # default 200 is far too many locally
             .config("spark.sql.session.timeZone", "UTC")
             .config("spark.ui.enabled", "false")
             .config("spark.ui.showConsoleProgress", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def read_gold(spark: SparkSession, gold_dir: Path, *tables: str) -> dict[str, DataFrame]:
    return {t: spark.read.parquet(str(gold_dir / f"{t}.parquet")) for t in tables}


def money(column: str):
    """Gold float64 money -> exact cents (decimal(12,2)), the warehouse loader's rounding rule."""
    return F.round(F.col(column), 2).cast("decimal(12,2)")


# --------------------------------------------------------------------------- 1. DataFrame API
def category_sales_monthly(lines: DataFrame, products: DataFrame, dates: DataFrame, broadcast: bool = True) -> DataFrame:
    """Grain: purchase month x category_reporting (combinations with at least one order line).

    n_orders = distinct orders; additive across months of one category (an order has one purchase
    month), NOT across categories (an order can span several)."""
    delivered = F.col("order_status") == "delivered"
    b = F.broadcast if broadcast else (lambda df: df)      # broadcast=False only for the benchmark A/B
    return (lines
            .join(b(products.select("product_id", "category_reporting")), "product_id")
            .join(b(dates.select(F.col("date").alias("purchase_date"),
                                 F.col("year_month"), F.col("in_candidate_window").alias("month_in_window"))),
                  "purchase_date")
            .withColumn("line_price", money("line_price_total"))
            .withColumn("line_freight", money("freight_total"))
            # Two-stage aggregation instead of two countDistinct (n_orders, n_orders_delivered): Spark plans
            # several distinct aggregates with an Expand that copies every row once per distinct aggregate
            # before the shuffle (Milestone B: 484 MB shuffled at 10x). Stage 1 = one row per order within
            # (month, category); stage 2 counts those rows. Identical results by construction.
            .groupBy("year_month", "category_reporting", "month_in_window", "order_id")
            .agg(F.count(F.lit(1)).alias("lines"),
                 F.sum("quantity").alias("qty"),
                 F.sum("line_price").alias("price"),
                 F.sum("line_freight").alias("freight"),
                 F.max(delivered.cast("int")).alias("is_delivered"),           # order has a delivered line
                 F.sum(F.when(delivered, F.col("quantity"))).alias("qty_dlv"),
                 F.sum(F.when(delivered, F.col("line_price"))).alias("price_dlv"))
            .groupBy("year_month", "category_reporting", "month_in_window")
            .agg(F.count(F.lit(1)).alias("n_orders"),
                 F.sum("lines").alias("n_order_lines"),
                 F.sum("qty").alias("units"),
                 F.sum("price").cast("decimal(14,2)").alias("items_price_total"),
                 F.sum("freight").cast("decimal(14,2)").alias("items_freight_total"),
                 F.sum("is_delivered").alias("n_orders_delivered"),
                 F.coalesce(F.sum("qty_dlv"), F.lit(0)).alias("units_delivered"),
                 F.coalesce(F.sum("price_dlv"), F.lit(0)).cast("decimal(14,2)").alias("items_price_total_delivered"))
            .withColumnRenamed("month_in_window", "in_candidate_window"))


# --------------------------------------------------------------------------- 2. Spark SQL
DELIVERY_MONTHLY_SQL = """
WITH o AS (
    SELECT *,
           order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL AS eligible   -- D-06
    FROM fact_orders
)
SELECT purchase_year_month,
       count(*)                                                                   AS n_orders,
       count(*) FILTER (WHERE order_status = 'delivered')                         AS n_orders_delivered,
       count(*) FILTER (WHERE eligible)                                           AS n_delivery_eligible,
       count(*) FILTER (WHERE dq_delivered_missing_delivery_date)                 AS n_delivered_missing_date,
       count(*) FILTER (WHERE dq_status_canceled_with_date)                       AS n_canceled_with_delivery_date,
       count(*) FILTER (WHERE eligible AND is_late)                               AS n_late,
       -- ANSI mode (Spark 4 default) raises on x / 0; nullif makes a month without eligible orders NULL
       count(*) FILTER (WHERE eligible AND is_late) / nullif(count(*) FILTER (WHERE eligible), 0) AS late_rate,
       avg(purchase_to_delivery_days) FILTER (WHERE eligible)                     AS avg_purchase_to_delivery_days,
       percentile(purchase_to_delivery_days, 0.5) FILTER (WHERE eligible)         AS median_purchase_to_delivery_days,
       avg(carrier_to_customer_days) FILTER (WHERE eligible)                      AS avg_carrier_to_customer_days,
       avg(approval_hours) FILTER (WHERE eligible)                                AS avg_approval_hours,
       avg(delivery_vs_estimate_days) FILTER (WHERE eligible)                     AS avg_delivery_vs_estimate_days,
       count(*) FILTER (WHERE eligible AND carrier_to_customer_days IS NULL)      AS n_eligible_without_valid_transit
FROM o
GROUP BY purchase_year_month
"""


def delivery_monthly(spark: SparkSession, orders: DataFrame) -> DataFrame:
    """Grain: purchase month. Measures are left unrounded (the view rounds; reconcile compares within
    that rounding). late_rate is NULL for a month without eligible orders (division by zero -> NULL)."""
    orders.createOrReplaceTempView("fact_orders")
    return spark.sql(DELIVERY_MONTHLY_SQL)


# --------------------------------------------------------------------------- 3. DataFrame API + window
def payment_methods_monthly(payments: DataFrame, dates: DataFrame, broadcast: bool = True) -> DataFrame:
    """Grain: purchase month x payment type. Payment-based only; every transaction kept."""
    month = Window.partitionBy("purchase_year_month")
    b = F.broadcast if broadcast else (lambda df: df)
    return (payments
            .join(b(dates.select(F.col("date").alias("purchase_date"),
                                           F.col("year_month").alias("purchase_year_month"))), "purchase_date")
            .withColumn("value", money("payment_value"))
            .groupBy("purchase_year_month", "payment_type", "in_candidate_window")
            .agg(F.count(F.lit(1)).alias("n_transactions"),
                 F.countDistinct("order_id").alias("n_orders"),
                 F.sum("value").cast("decimal(14,2)").alias("payment_value_total"),
                 # D-16: instalment statistics exclude zero-instalment rows. Exact decimal sum / count
                 # (avg() on decimals rounds to scale + 4 first, which could double-round at 2 dp)
                 (F.sum(F.when(~F.col("dq_installments_zero"), F.col("payment_installments"))).cast("decimal(20,0)")
                  / F.nullif(F.count(F.when(~F.col("dq_installments_zero"), F.lit(1))), F.lit(0)).cast("decimal(20,0)"))
                 .alias("avg_installments_raw"),
                 F.sum(F.col("dq_installments_zero").cast("int")).alias("n_zero_installment_transactions"),
                 F.sum(F.col("dq_payment_zero").cast("int")).alias("n_zero_value_transactions"))
            .withColumn("month_payment_value_total", F.sum("payment_value_total").over(month).cast("decimal(14,2)"))
            .withColumn("payment_value_share_pct",
                        F.round(F.lit(100) * F.col("payment_value_total")
                                / F.nullif(F.col("month_payment_value_total"), F.lit(0)), 4).cast("decimal(7,4)"))
            .withColumn("avg_installments", F.round("avg_installments_raw", 2).cast("decimal(6,2)"))
            .select("purchase_year_month", "payment_type", "in_candidate_window", "n_transactions", "n_orders",
                    "payment_value_total", "month_payment_value_total", "payment_value_share_pct",
                    "avg_installments", "n_zero_installment_transactions", "n_zero_value_transactions"))


# --------------------------------------------------------------------------- run
def run(gold_dir: Path = GOLD_DIR, out_dir: Path = RESULTS_DIR) -> dict[str, tuple[int, float]]:
    spark = build_session()
    try:
        g = read_gold(spark, gold_dir, "fact_order_lines", "fact_orders", "fact_payments", "dim_products", "dim_dates")
        results = {
            "category_sales_monthly": lambda: category_sales_monthly(g["fact_order_lines"], g["dim_products"], g["dim_dates"]),
            "delivery_monthly": lambda: delivery_monthly(spark, g["fact_orders"]),
            "payment_methods_monthly": lambda: payment_methods_monthly(g["fact_payments"], g["dim_dates"]),
        }
        summary = {}
        for name, build in results.items():
            t = time.perf_counter()
            df = build()
            df.coalesce(1).write.mode("overwrite").parquet(str(out_dir / name))   # small result: one file
            summary[name] = (spark.read.parquet(str(out_dir / name)).count(), time.perf_counter() - t)
        return summary
    finally:
        spark.stop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.spark.jobs", description="Run the PySpark workloads on Gold.")
    p.add_argument("--gold-dir", type=Path, default=GOLD_DIR)
    p.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = p.parse_args(argv)
    for name, (rows, seconds) in run(args.gold_dir, args.out).items():
        print(f"{name:26} {rows:>5} rows  {seconds:6.1f}s  -> {args.out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
