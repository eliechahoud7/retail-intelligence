# PySpark: Workloads, Optimization and Benchmark (Phase 6)

**Engine:** PySpark 4.2.0 (Hadoop 3.5.0), OpenJDK 17, `local[4]`, WSL2 Ubuntu 24.04 (8 vCPU, 7 GB) on an i7-1165G7 laptop.
**Inputs:** the eight Gold Parquet files (`data/processed/analytical/`, run `baea48c9b8ac`), the source of truth for this phase.
**Code:** `src/spark/jobs.py` (workloads), `src/spark/benchmark.py` (scaling, plans, benchmark), `src/spark/reconcile.py` (checks against PostgreSQL).

Spark adds a second engine next to the existing pandas pipeline. It replaces nothing: the Gold build, the warehouse, the views and the dbt marts are unchanged and serve as the trusted results.

## 1. Running it

Native Windows Spark cannot write Parquet without third-party `winutils` binaries (`HADOOP_HOME and hadoop.home.dir are unset`), so Spark runs in WSL2. One-time setup (root): `apt-get install openjdk-17-jre-headless python3.12-venv`; then `python3.12 -m venv ~/venvs/retail-spark` and `pip install -r requirements-spark.txt`. `JAVA_HOME` is set in `~/.profile`.

```bash
# WSL2, from the project root (the Windows folder seen from WSL, e.g. /mnt/c/path/to/retail-intelligence)
PY=~/venvs/retail-spark/bin/python
$PY -m src.spark.jobs                               # 3 workloads -> data/processed/spark/results/
$PY -m unittest tests.test_spark                    # business-rule tests on tiny inputs
$PY -m src.spark.benchmark scale  --factor 10       # scaled data in ~/retail-spark-data/10x
$PY -m src.spark.benchmark verify --factor 10       # Spark == pandas == 10 x the 1x results
$PY -m src.spark.benchmark pandas --factor 10       # timings, one process per engine
$PY -m src.spark.benchmark spark  --factor 10
$PY -m src.spark.benchmark plans  --factor 10       # final plans, SQL metrics, A/B configs, pruning
```

```powershell
# Windows, project environment: reconcile the Spark results with PostgreSQL (read-only)
.venv\Scripts\python.exe -m src.spark.reconcile
```

WSL2 cannot reach the Windows PostgreSQL (NAT networking, `localhost` listener). The reconciliation therefore runs on Windows and reads the Spark results from the project folder; neither PostgreSQL nor WSL networking was changed. Benchmark JSON and plan text are written to `data/processed/spark/benchmark/` (git-ignored).

## 2. Workloads and correctness

| Workload | API | Logic (unchanged business rules) | Rows | Reconciled with |
|---|---|---|---:|---|
| `category_sales_monthly` | DataFrame | order lines -> products, dates (many-to-one, broadcast); item-based only (Q-03) | 1,283 | `analytics.category_performance` (per category), `analytics.sales_daily` (per month), query `category_monthly_ranking` (100 cells) |
| `delivery_monthly` | Spark SQL | FILTER aggregates over the D-06 population (delivered + delivery date); D-08 durations; exact median | 25 | `analytics.delivery_monthly` |
| `payment_methods_monthly` | DataFrame + window | payment-based only, no order lines; monthly share by window; D-16 mean excludes zero instalments | 90 | dbt `mart_payment_methods_monthly` |

- **Money:** Gold stores money as float64. Each value is rounded to cents as `decimal(12,2)` before summing, the loader's rule, so totals are exact. pandas uses integer cents.
- **Q-11:** the candidate window is carried as a flag; no workload filters on it.
- **Reconciliation:** `RECONCILIATION: OK` (6/6 checks), rerun after the category optimization. Counts and money are exact. Delivery durations are within the view's 2-dp rounding (largest difference 0.0049).
- **Benchmark equivalence:** `benchmark verify` shows pandas == Spark cell by cell at 1x and 10x, and every additive 10x measure is exactly 10 x the 1x value (averages, medians, rates and shares unchanged).

## 3. Partitioning

| Data | Decision | Reason |
|---|---|---|
| Workload outputs (25-1,283 rows) | **not partitioned**, one file each (`coalesce(1)`) | partitioning would only create tiny files |
| Gold inputs (1x, 8-20 MB per table) | **not partitioned** | one file per table is already well within a sensible file size |
| Scaled `fact_orders` | **partitioned by `purchase_year_month`** (existing column, no derived key) | month is the natural filter for order and delivery analysis |

Partition file sizes for `fact_orders_by_month` (25 partitions):

| Scale | Median file | Largest | Smallest |
|---|---:|---:|---:|
| 10x | 4.6 MB | 8.3 MB | 0.02 MB |
| 30x | 10.1 MB | 19.6 MB | 0.02 MB |

Even at 30x the files are far below the ~128 MB usually targeted. Monthly partitioning is only justified at much larger volumes; at this scale, partitioning by year would be the more proportional choice.

**Pruning evidence** (10x, query: months 2018-01 to 2018-08, count and average delivery days per month):

| Read | `PartitionFilters` in plan | Partitions read | Size of files read | Rows from scan | Warm time |
|---|:-:|---:|---:|---:|---:|
| plain Parquet (8 files, sorted by date) | no | - | 141.9 MB | 674,410 | 0.166 s |
| partitioned by month | **yes** | **8 of 25** | **58.6 MB** | 539,910 | 0.136 s |

- **Bytes:** pruning skips 59 % of the bytes.
- **Rows:** the plain read already skips some rows through Parquet row-group statistics, because the files are sorted by date. That's why it returns 674,410 rows rather than all 994,410, which keeps the time difference small at this size.

## 4. Optimization (evidence from final, post-AQE plans and SQL metrics, 10x)

| Decision | Evidence | Kept? |
|---|---|---|
| **Broadcast the dimensions** (`products` 33k rows, `dates` 1.3k) | Plan: `BroadcastHashJoin` x2, no sort. Without hints: `SortMergeJoin` with 4 extra exchanges and 4 sorts. Category shuffle 236 MB vs 527 MB (2.08 s vs 2.86 s); payments 178 MB vs 306 MB (1.39 s vs 1.63 s) | yes |
| **Two-stage aggregation instead of two `countDistinct`** (category) | Before: an `Expand` operator copied every row per distinct aggregate, giving 484 MB of shuffle from 100 MB of input. After: no `Expand`, 236 MB. Warm time 3.49 s -> 2.33 s at 10x (1.72 s -> 1.12 s at 1x). Results identical (reconciled) | yes |
| **AQE + `shuffle.partitions=8`** (instead of 200 without AQE) | AQE coalesces the shuffles to 1-5 partitions (`AQEShuffleRead`). Spark defaults: category 4.28 s vs 2.08 s, payments 2.81 s vs 1.39 s, delivery 0.78 s vs 0.65 s | yes |
| **Column pruning** | `ReadSchema` of the order-lines scan lists only the 7 columns used (of 18); `fact_orders` only the 10 used (of 53) | automatic |
| **Predicate / partition pruning** | `PartitionFilters` in the plan; 8 of 25 partitions read (section 3) | for filtered reads |
| **Caching** | Not used. Each workload reads its fact table exactly once, and the only shared input is `dim_dates` (1.3k rows). Caching would add memory and a materialisation step without a second use | no |

The delivery workload has no join. Its single exchange (10 MB) comes from the per-month aggregation, and it's insensitive to the broadcast setting.

## 5. Benchmark: pandas vs Spark

**Method:**
- **Same input files and logic:** both engines read the same Parquet copy on the Linux file system, and `verify` confirms the outputs are equal.
- **Processes:** each engine runs in its own process.
- **Timing:** end to end, from Parquet read to the collected result. pandas reads only the needed columns, as Spark does through column pruning. Timings are the first (cold) run and the median of 3 warm runs.
- **Spark start-up** (JVM and session) is timed separately and excluded from workload times.
- **Scaling:** facts are copied N times with suffixed order keys; dimensions aren't scaled, so the joins stay many-to-one.

| Scale | Rows (lines / orders / payments) | Fact Parquet | Workload | pandas warm (cold) | Spark warm (cold) | Spark / pandas |
|---|---|---:|---|---:|---:|---:|
| 1x | 102k / 99k / 104k | 43 MB | category | 0.114 s (0.158) | 1.119 s (5.907) | 9.8x |
| | | | delivery | 0.023 s (0.025) | 0.484 s (1.245) | 21.0x |
| | | | payments | 0.046 s (0.053) | 0.794 s (1.306) | 17.3x |
| 10x | 1.02M / 0.99M / 1.04M | 299 MB | category | 0.914 s (1.119) | 2.332 s (7.570) | 2.6x |
| | | | delivery | 0.184 s (0.208) | 0.663 s (1.705) | 3.6x |
| | | | payments | 0.337 s (0.364) | 1.474 s (2.216) | 4.4x |
| 30x | 3.07M / 2.98M / 3.12M | 618 MB | category | 3.688 s (3.711) | 5.502 s (11.224) | 1.5x |
| | | | delivery | 0.535 s (0.661) | 1.554 s (2.419) | 2.9x |
| | | | payments | 1.190 s (1.358) | 3.705 s (5.153) | 3.1x |

| Scale | pandas peak RSS | Spark JVM peak RSS | Spark session start-up |
|---|---:|---:|---:|
| 1x | 382 MB | 2,029 MB | 4.2 s |
| 10x | 991 MB | 2,217 MB | 4.3 s |
| 30x | 2,090 MB | 3,634 MB | 4.5 s |

The 30x run is a single benchmark data point. Its scaled data was produced by the same verified generator, but `verify` (which holds pandas and Spark results in one process) was run at 1x and 10x only, to stay within the 7 GB VM.

## 6. Conclusions and limitations

- **pandas is faster at every scale tested.** At 3 million rows per fact it's still 1.5-3x faster. The gap narrows steadily (category 9.8x -> 2.6x -> 1.5x), because Spark's cost per row grows more slowly. The trend suggests Spark would overtake pandas somewhere beyond 30x, but that wasn't measured and isn't claimed.
- **Why Spark is slower here:**
  - per-query overhead: planning, code generation, task scheduling and the Python-JVM bridge;
  - shuffles that write and read intermediate data even when a single process could aggregate in memory;
  - only 4 local cores;
  - on top of that, a fixed 4-5 s session start-up and 0.5-6 s of first-run (cold) overhead that pandas doesn't pay.

  At this size the data fits comfortably in one process's memory, which is the case pandas handles best.
- **Memory:** Spark used more memory than pandas at every scale (JVM 2.0-3.6 GB vs 0.4-2.1 GB). Spark's advantage is that it doesn't need the whole data set in memory, which only matters beyond the RAM of one machine.
- **What the optimizations show:** the measurable wins came from choices visible in the plans: broadcast joins, removing the `Expand`, and AQE with a sensible partition count, which together roughly halve Spark's time against its defaults. These are the levers that matter at real scale.
- **Limitations:**
  - one laptop and `local[4]`, with no cluster, so no network shuffle and no real parallelism beyond 4 cores;
  - scaled data is replicated Olist data, so key distributions and skew are those of the original copied N times;
  - warm medians of 3 runs on a shared desktop OS carry a few percent of noise;
  - the WSL2 VM has 7 GB, which capped the scale at 30x.
