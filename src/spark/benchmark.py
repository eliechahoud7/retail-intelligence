"""Scaled data, partitioning, execution plans and the pandas-vs-Spark benchmark (Phase 6, Milestone B).

Spark environment (WSL2), from the project root::

    PY=~/venvs/retail-spark/bin/python
    $PY -m src.spark.benchmark scale --factor 1      # copy of Gold on the Linux file system
    $PY -m src.spark.benchmark scale --factor 10     # 10x facts + fact_orders partitioned by month
    $PY -m src.spark.benchmark spark  --factor 10    # Spark timings (separate process per engine)
    $PY -m src.spark.benchmark pandas --factor 10    # pandas timings
    $PY -m src.spark.benchmark plans  --factor 10    # plans, SQL metrics, A/B configurations, pruning
    $PY -m src.spark.benchmark verify --factor 10    # Spark == pandas == factor x the 1x results

Scaling: the fact tables are copied ``factor`` times with every order key suffixed (``order_id#k``),
so each copy is a disjoint set of orders with identical attributes; dimensions are not scaled (facts
grow, products / dates do not), so every join stays many-to-one. Expected results are therefore
exact: counts and money x factor; averages, medians, shares and rates unchanged.

Results (JSON) and plans (text) go to ``data/processed/spark/benchmark/`` (git-ignored); data to
``~/retail-spark-data/<factor>x`` on the Linux file system (fast, and the same disk for both engines).
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

from src.spark.jobs import GOLD_DIR, PROJECT_ROOT, WORKLOADS

DATA_ROOT = Path.home() / "retail-spark-data"
OUT_DIR = PROJECT_ROOT / "data" / "processed" / "spark" / "benchmark"
FACTS = ("fact_order_lines", "fact_orders", "fact_payments")
DIMS = ("dim_products", "dim_dates")
CENT, CENT4, CENT2 = Decimal("0.01"), Decimal("0.0001"), Decimal("0.01")

# columns each workload needs (pandas reads only these; Spark prunes columns itself - see `plans`)
LINE_COLS = ["order_id", "product_id", "quantity", "line_price_total", "freight_total", "order_status", "purchase_date"]
ORDER_COLS = ["purchase_year_month", "order_status", "order_delivered_customer_date", "dq_delivered_missing_delivery_date",
              "dq_status_canceled_with_date", "is_late", "purchase_to_delivery_days", "carrier_to_customer_days",
              "approval_hours", "delivery_vs_estimate_days"]
PAY_COLS = ["order_id", "payment_type", "payment_installments", "payment_value", "dq_payment_zero",
            "dq_installments_zero", "in_candidate_window", "purchase_date"]


def data_dir(factor: int) -> Path:
    return DATA_ROOT / f"{factor}x"


def size_mb(path: Path) -> float:
    files = [path] if path.is_file() else [p for p in path.rglob("*.parquet")]
    return sum(p.stat().st_size for p in files) / 1e6


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024   # Linux: KiB


def save(name: str, payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return path


# =========================================================================== scaled data
def scale(factor: int) -> dict:
    out = data_dir(factor)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for t in DIMS:
        shutil.copy2(GOLD_DIR / f"{t}.parquet", out / f"{t}.parquet")
    if factor == 1:                                    # exact copy of Gold, no Spark involved
        for t in FACTS:
            shutil.copy2(GOLD_DIR / f"{t}.parquet", out / f"{t}.parquet")
    else:
        from pyspark.sql import functions as F
        from src.spark.jobs import build_session
        spark = build_session("retail-scale")
        try:
            copies = spark.range(factor).withColumnRenamed("id", "copy")
            key = lambda c: F.when(F.col("copy") == 0, F.col(c)).otherwise(F.concat_ws("#", F.col(c), F.col("copy").cast("string")))  # noqa: E731
            for t in FACTS:
                df = spark.read.parquet(str(GOLD_DIR / f"{t}.parquet"))
                keyed = [c for c in ("order_id", "order_line_id", "customer_id", "customer_unique_id") if c in df.columns]
                scaled = df.crossJoin(F.broadcast(copies)).select(*[key(c).alias(c) if c in keyed else F.col(c) for c in df.columns])
                # ~1M rows per table at 10x: 8 files of ~5-15 MB, sorted by date so row-group statistics are useful
                scaled.repartition(8).sortWithinPartitions("purchase_date").write.parquet(str(out / f"{t}.parquet"))
            # partitioned copy of fact_orders by purchase month (existing Gold column; one file per month)
            (spark.read.parquet(str(out / "fact_orders.parquet"))
             .repartition("purchase_year_month").write.partitionBy("purchase_year_month")
             .parquet(str(out / "fact_orders_by_month")))
        finally:
            spark.stop()
    info = {t: round(size_mb(out / f"{t}.parquet"), 1) for t in (*FACTS, *DIMS)}
    if factor > 1:
        parts = sorted((out / "fact_orders_by_month").glob("purchase_year_month=*/*.parquet"))
        sizes = [p.stat().st_size / 1e6 for p in parts]
        info["fact_orders_by_month"] = {"partitions": len(parts), "total_mb": round(sum(sizes), 1),
                                        "file_mb_min_median_max": [round(min(sizes), 2), round(statistics.median(sizes), 2),
                                                                   round(max(sizes), 2)]}
    return info


# =========================================================================== pandas equivalents
def _cents(s: pd.Series) -> pd.Series:
    return (s * 100).round().astype("int64")          # 2-dp float money -> exact integer cents


def pd_category_sales_monthly(lines, products, dates) -> pd.DataFrame:
    df = (lines.merge(products[["product_id", "category_reporting"]], on="product_id", validate="m:1")
               .merge(dates.rename(columns={"date": "purchase_date", "in_candidate_window": "month_in_window"})
                      [["purchase_date", "year_month", "month_in_window"]], on="purchase_date", validate="m:1"))
    dlv = df["order_status"] == "delivered"
    df = df.assign(price=_cents(df["line_price_total"]), freight=_cents(df["freight_total"]),
                   order_dlv=df["order_id"].where(dlv), units_dlv=df["quantity"].where(dlv, 0))
    df["price_dlv"] = df["price"].where(dlv, 0)
    return (df.groupby(["year_month", "category_reporting", "month_in_window"], observed=True, sort=False)
              .agg(n_orders=("order_id", "nunique"), n_order_lines=("order_id", "size"), units=("quantity", "sum"),
                   items_price_total=("price", "sum"), items_freight_total=("freight", "sum"),
                   n_orders_delivered=("order_dlv", "nunique"), units_delivered=("units_dlv", "sum"),
                   items_price_total_delivered=("price_dlv", "sum"))
              .reset_index().rename(columns={"month_in_window": "in_candidate_window"}))


def pd_delivery_monthly(orders) -> pd.DataFrame:
    o = orders
    elig = (o["order_status"] == "delivered") & o["order_delivered_customer_date"].notna()      # D-06
    flags = pd.DataFrame({"purchase_year_month": o["purchase_year_month"], "n_orders": 1,
                          "n_orders_delivered": o["order_status"] == "delivered", "n_delivery_eligible": elig,
                          "n_delivered_missing_date": o["dq_delivered_missing_delivery_date"],
                          "n_canceled_with_delivery_date": o["dq_status_canceled_with_date"],
                          "n_late": elig & o["is_late"].fillna(False).astype(bool),
                          "n_eligible_without_valid_transit": elig & o["carrier_to_customer_days"].isna()})
    counts = flags.groupby("purchase_year_month", sort=False).sum()
    e = o.loc[elig, ["purchase_year_month", "purchase_to_delivery_days", "carrier_to_customer_days", "approval_hours",
                     "delivery_vs_estimate_days"]].groupby("purchase_year_month", sort=False)
    stats = e.mean().add_prefix("avg_").join(e["purchase_to_delivery_days"].median().rename("median_purchase_to_delivery_days"))
    out = counts.join(stats).reset_index()
    out["late_rate"] = out["n_late"] / out["n_delivery_eligible"].where(out["n_delivery_eligible"] > 0)
    return out


def pd_payment_methods_monthly(payments, dates) -> pd.DataFrame:
    df = payments.merge(dates.rename(columns={"date": "purchase_date", "year_month": "purchase_year_month"})
                        [["purchase_date", "purchase_year_month"]], on="purchase_date", validate="m:1")
    ok_inst = ~df["dq_installments_zero"]
    df = df.assign(value=_cents(df["payment_value"]), inst=df["payment_installments"].where(ok_inst, 0),
                   inst_n=ok_inst.astype("int64"))
    g = (df.groupby(["purchase_year_month", "payment_type", "in_candidate_window"], observed=True, sort=False)
           .agg(n_transactions=("order_id", "size"), n_orders=("order_id", "nunique"), value=("value", "sum"),
                inst=("inst", "sum"), inst_n=("inst_n", "sum"),
                n_zero_installment_transactions=("dq_installments_zero", "sum"),
                n_zero_value_transactions=("dq_payment_zero", "sum")).reset_index())
    g["month_value"] = g.groupby("purchase_year_month")["value"].transform("sum")
    # exact decimal finishing on the small result (D-16 mean, monthly share), HALF_UP like Spark / PostgreSQL
    g["payment_value_total"] = [Decimal(v) / 100 for v in g["value"]]
    g["month_payment_value_total"] = [Decimal(v) / 100 for v in g["month_value"]]
    g["payment_value_share_pct"] = [(Decimal(100) * v / m).quantize(CENT4, ROUND_HALF_UP) if m else None
                                    for v, m in zip(g["value"], g["month_value"])]
    g["avg_installments"] = [(Decimal(int(s)) / n).quantize(CENT2, ROUND_HALF_UP) if n else None
                             for s, n in zip(g["inst"], g["inst_n"])]
    return g.drop(columns=["value", "month_value", "inst", "inst_n"])


def pandas_workloads(d: Path) -> dict:
    """name -> zero-argument callable doing read (needed columns only) + compute, as a pandas user would."""
    rd = lambda t, cols=None: pd.read_parquet(d / f"{t}.parquet", columns=cols)  # noqa: E731
    return {
        "category_sales_monthly": lambda: pd_category_sales_monthly(rd("fact_order_lines", LINE_COLS),
                                                                    rd("dim_products", ["product_id", "category_reporting"]),
                                                                    rd("dim_dates", ["date", "year_month", "in_candidate_window"])),
        "delivery_monthly": lambda: pd_delivery_monthly(rd("fact_orders", ORDER_COLS)),
        "payment_methods_monthly": lambda: pd_payment_methods_monthly(rd("fact_payments", PAY_COLS),
                                                                      rd("dim_dates", ["date", "year_month"])),
    }


def spark_workloads(spark, d: Path, broadcast: bool = True) -> dict:
    """name -> zero-argument callable returning the (lazy) DataFrame; reading is part of the timed run."""
    from src.spark import jobs
    rd = lambda t: spark.read.parquet(str(d / f"{t}.parquet"))  # noqa: E731
    return {
        "category_sales_monthly": lambda: jobs.category_sales_monthly(rd("fact_order_lines"), rd("dim_products"),
                                                                      rd("dim_dates"), broadcast=broadcast),
        "delivery_monthly": lambda: jobs.delivery_monthly(spark, rd("fact_orders")),
        "payment_methods_monthly": lambda: jobs.payment_methods_monthly(rd("fact_payments"), rd("dim_dates"),
                                                                        broadcast=broadcast),
    }


# =========================================================================== timing
def _secs(fn) -> float:
    t = time.perf_counter(); fn(); return time.perf_counter() - t


def _time(fn, repeats: int) -> dict:
    """First (cold) run, then the median of `repeats` warm runs, wall clock."""
    t = time.perf_counter(); result = fn(); cold = time.perf_counter() - t
    warm = []
    for _ in range(repeats):
        t = time.perf_counter(); fn(); warm.append(time.perf_counter() - t)
    return {"cold_s": round(cold, 3), "warm_median_s": round(statistics.median(warm), 3),
            "warm_s": [round(w, 3) for w in warm], "rows": len(result)}


def bench_pandas(factor: int, repeats: int = 3) -> dict:
    d = data_dir(factor)
    res = {n: _time(fn, repeats) for n, fn in pandas_workloads(d).items()}
    return {"engine": "pandas", "pandas": pd.__version__, "factor": factor, "workloads": res,
            "peak_rss_mb": round(peak_rss_mb())}


def _jvm_peak_mb(spark) -> float | None:
    pid = spark.sparkContext._jvm.java.lang.ProcessHandle.current().pid()
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return round(int(line.split()[1]) / 1024)
    return None


def bench_spark(factor: int, repeats: int = 3) -> dict:
    from src.spark.jobs import build_session
    t = time.perf_counter(); spark = build_session("retail-bench"); startup = time.perf_counter() - t
    try:
        d = data_dir(factor)
        res = {n: _time(lambda fn=fn: fn().collect(), repeats) for n, fn in spark_workloads(spark, d).items()}
        return {"engine": "spark", "spark": spark.version, "master": spark.sparkContext.master, "factor": factor,
                "startup_s": round(startup, 3), "workloads": res, "jvm_peak_rss_mb": _jvm_peak_mb(spark),
                "python_peak_rss_mb": round(peak_rss_mb())}
    finally:
        spark.stop()


# =========================================================================== plans and metrics
def _walk(jvm, node):
    """Physical plan nodes, following AQE query stages and the final adaptive plan."""
    conv = jvm.scala.jdk.javaapi.CollectionConverters
    name = node.nodeName()
    if name.startswith("AdaptiveSparkPlan"):
        yield from _walk(jvm, node.executedPlan()); return
    yield node
    if "QueryStage" in name:
        yield from _walk(jvm, node.plan())
    for child in conv.asJava(node.children()):
        yield from _walk(jvm, child)


def plan_metrics(spark, df) -> dict:
    """Execute and summarise the FINAL (post-AQE) plan: operators, joins, scans, shuffles."""
    df.collect()
    jvm = spark.sparkContext._jvm
    conv = jvm.scala.jdk.javaapi.CollectionConverters
    nodes = list(_walk(jvm, df._jdf.queryExecution().executedPlan()))
    summary = {"operators": {}, "scans": [], "shuffle_bytes": 0, "aqe_partitions": []}
    for n in nodes:
        name = n.nodeName()
        summary["operators"][name] = summary["operators"].get(name, 0) + 1
        m = {k: v.value() for k, v in conv.asJava(n.metrics()).items()}
        if name.startswith("Scan parquet"):
            summary["scans"].append({"files": m.get("numFiles"), "mb_read": round(m.get("filesSize", 0) / 1e6, 2),
                                     "partitions_read": m.get("numPartitions"), "rows_out": m.get("numOutputRows")})
        if name in ("Exchange", "ShuffleExchange"):
            summary["shuffle_bytes"] += m.get("dataSize", 0)
        if name == "AQEShuffleRead":
            summary["aqe_partitions"].append(m.get("numPartitions"))
    summary["shuffle_mb"] = round(summary.pop("shuffle_bytes") / 1e6, 2)
    return summary


def plans(factor: int) -> dict:
    from src.spark.jobs import build_session
    d = data_dir(factor)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {"factor": factor, "configs": {}}
    configs = {  # (broadcast hints, AQE, shuffle partitions)
        "configured (broadcast, AQE, 8 partitions)": (True, "true", "8"),
        "no broadcast (sort-merge joins)": (False, "true", "8"),
        "Spark defaults without AQE (200 partitions)": (True, "false", "200"),
    }
    spark = build_session("retail-plans")
    # each timed run builds a NEW query: re-collecting the same DataFrame would reuse its shuffle files
    timed = lambda build: statistics.median(_secs(lambda: build().collect()) for _ in range(3))  # noqa: E731
    try:
        spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")    # only explicit hints broadcast (A/B is clean)
        for fn in spark_workloads(spark, d).values():                    # warm-up: JIT / caches, not measured
            fn().collect()
        for label, (bc, aqe, parts) in configs.items():
            spark.conf.set("spark.sql.adaptive.enabled", aqe)
            spark.conf.set("spark.sql.shuffle.partitions", parts)
            per = {}
            for name, fn in spark_workloads(spark, d, broadcast=bc).items():
                df = fn()
                metrics = plan_metrics(spark, df)
                metrics["warm_median_s"] = round(timed(fn), 3)
                per[name] = metrics
                if label.startswith("configured"):   # after execution: the formatted plan is AQE's final plan
                    (OUT_DIR / f"plan_{name}_{factor}x.txt").write_text(
                        spark._jvm.PythonSQLUtils.explainString(df._jdf.queryExecution(), "formatted"), encoding="utf-8")
            out["configs"][label] = per
        # partition pruning: an ad-hoc filtered read (months 2018-01..2018-08) on plain vs partitioned fact_orders.
        # The workloads themselves never filter (Q-11 stays a flag); this is a separate access pattern.
        spark.conf.set("spark.sql.adaptive.enabled", "true"); spark.conf.set("spark.sql.shuffle.partitions", "8")
        from pyspark.sql import functions as F
        pr = {}
        if (d / "fact_orders_by_month").exists():
            for label, path in (("plain", d / "fact_orders.parquet"), ("partitioned by month", d / "fact_orders_by_month")):
                build = lambda path=path: (spark.read.parquet(str(path))  # noqa: E731
                                           .filter(F.col("purchase_year_month").between("2018-01", "2018-08"))
                                           .groupBy("purchase_year_month")
                                           .agg(F.count(F.lit(1)).alias("n"), F.avg("purchase_to_delivery_days").alias("avg_days")))
                df = build()
                plan_text = df._jdf.queryExecution().executedPlan().toString()
                m = plan_metrics(spark, df)
                m["warm_median_s"] = round(timed(build), 3)
                m["partition_filters_in_plan"] = "PartitionFilters: [isnotnull(purchase_year_month" in plan_text
                m["rows"] = sum(r["n"] for r in df.collect())
                pr[label] = m
        out["partition_pruning"] = pr
    finally:
        spark.stop()
    return out


# =========================================================================== correctness of the benchmark
def _canon(df: pd.DataFrame, key: list[str]) -> dict:
    """{key: {col: value}} with money/decimals as Decimal and floats rounded to 9 dp (engine-neutral)."""
    out = {}
    for r in df.to_dict("records"):
        vals = {}
        for c, v in r.items():
            if c in key:
                continue
            if v is None or (isinstance(v, float) and pd.isna(v)):
                vals[c] = None
            elif isinstance(v, float):
                vals[c] = round(v, 9)
            else:
                vals[c] = v if isinstance(v, (bool, Decimal)) or type(v).__name__ == "bool_" else int(v)
        out[tuple(r[k] for k in key)] = vals
    return out


KEYS = {"category_sales_monthly": ["year_month", "category_reporting"], "delivery_monthly": ["purchase_year_month"],
        "payment_methods_monthly": ["purchase_year_month", "payment_type"]}
MONEY = {"items_price_total", "items_freight_total", "items_price_total_delivered"}   # pandas keeps cents


def verify(factor: int) -> dict:
    """Spark == pandas at `factor`, and (factor > 1) Spark(factor) == factor x Spark(1x) for additive measures."""
    from src.spark.jobs import build_session
    spark = build_session("retail-verify")
    report = {}
    try:
        sp = {n: fn().toPandas() for n, fn in spark_workloads(spark, data_dir(factor)).items()}
        base = {n: fn().toPandas() for n, fn in spark_workloads(spark, data_dir(1)).items()} if factor > 1 else None
    finally:
        spark.stop()
    pdo = {n: fn() for n, fn in pandas_workloads(data_dir(factor)).items()}
    for n in WORKLOADS:
        p = pdo[n].copy()
        for c in MONEY & set(p.columns):
            p[c] = [Decimal(int(v)) / 100 for v in p[c]]
        s_, p_ = _canon(sp[n], KEYS[n]), _canon(p[[*KEYS[n], *[c for c in sp[n].columns if c not in KEYS[n]]]], KEYS[n])
        diffs = [f"{k}.{c}: spark={s_[k][c]} pandas={p_[k].get(c)}" for k in s_ if k in p_ for c in s_[k] if s_[k][c] != p_[k].get(c)]
        if set(s_) != set(p_):
            diffs.append(f"keys differ: {len(set(s_) ^ set(p_))}")
        entry = {"rows": len(s_), "spark_equals_pandas": not diffs, "diffs": diffs[:5]}
        if base is not None:
            b_ = _canon(base[n], KEYS[n])
            scaled_diffs = []
            for k, vals in s_.items():
                for c, v in vals.items():
                    bv = b_.get(k, {}).get(c)
                    if c.startswith(("n_", "units", "items_", "payment_value_total", "month_payment")):
                        expect = None if bv is None else bv * factor
                    elif c == "late_rate" or c.startswith(("avg_", "median_", "payment_value_share")) or c == "in_candidate_window":
                        expect = bv
                    else:
                        continue
                    if v != expect and not (isinstance(v, float) and expect is not None and abs(v - expect) < 1e-9 * max(1, abs(expect))):
                        scaled_diffs.append(f"{k}.{c}: {v} != expected {expect}")
            entry["equals_factor_x_1x"] = not scaled_diffs and set(s_) == set(b_)
            entry["scaled_diffs"] = scaled_diffs[:5]
        report[n] = entry
    return report


# =========================================================================== CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.spark.benchmark")
    p.add_argument("command", choices=["scale", "spark", "pandas", "plans", "verify"])
    p.add_argument("--factor", type=int, default=1)
    p.add_argument("--repeats", type=int, default=3)
    a = p.parse_args(argv)
    if a.command == "scale":
        result = scale(a.factor)
    elif a.command == "spark":
        result = bench_spark(a.factor, a.repeats)
    elif a.command == "pandas":
        result = bench_pandas(a.factor, a.repeats)
    elif a.command == "plans":
        result = plans(a.factor)
    else:
        result = verify(a.factor)
    path = save(f"{a.command}_{a.factor}x", result)
    print(json.dumps(result, indent=1, default=str))
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
