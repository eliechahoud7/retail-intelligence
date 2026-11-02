"""Retail Intelligence pipeline orchestrated by Airflow (Phase 7).

Airflow runs in WSL2 (it does not run natively on Windows). Every task calls an EXISTING project
command - no pipeline logic lives here:

* Windows stages run the Windows project environment through WSL interop
  (``.venv/Scripts/python.exe``). They are Windows processes, so they reach PostgreSQL on Windows
  ``localhost`` without any network change.
* ``spark_jobs`` runs natively in WSL with the Spark environment (``~/venvs/retail-spark``).

Safe reruns: every command is idempotent - the layers rebuild their outputs and refuse to write on
stale or invalid inputs, ``load-warehouse`` is one atomic full refresh with post-load validation,
``dbt build`` and the Spark jobs overwrite their own outputs. Any task can be cleared and rerun.
Each command exits non-zero on failure, which fails the task; downstream tasks then do not run.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

PROJECT = str(Path(__file__).resolve().parents[2])  # project root: this file is <root>/airflow/dags/
WIN_PY = ".venv/Scripts/python.exe"                 # Windows project environment, via WSL interop
SPARK_PY = "$HOME/venvs/retail-spark/bin/python"    # Spark environment in WSL

with DAG(
    dag_id="retail_intelligence_pipeline",
    description="Raw -> staging -> clean -> Gold -> PostgreSQL -> dbt, plus PySpark workloads and reconciliation",
    schedule="0 6 * * 1",                     # Mondays 06:00 UTC; the source is a static extract, so weekly is ample
    start_date=datetime(2026, 11, 2),
    catchup=False,                            # no backfill of past Mondays
    max_active_runs=1,                        # stages share files and the warehouse: never two runs at once
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
        "execution_timeout": timedelta(minutes=30),
    },
    tags=["retail-intelligence"],
) as dag:

    def task(task_id: str, command: str, **kwargs) -> BashOperator:
        return BashOperator(task_id=task_id, bash_command=command, cwd=PROJECT, **kwargs)

    ingest = task("ingest", f"{WIN_PY} -m src.ingestion")                     # verifies raw files + checksums
    staging = task("staging", f"{WIN_PY} -m src.staging")
    clean = task("clean", f"{WIN_PY} -m src.clean")
    analytical = task("analytical", f"{WIN_PY} -m src.analytical")            # Gold star schema
    load_warehouse = task("load_warehouse", f"{WIN_PY} -m src.database load-warehouse")
    verify_views = task("verify_views", f"{WIN_PY} -m src.database verify-views")
    dbt_build = task("dbt_build", f"{WIN_PY} -m src.database dbt build")
    spark_jobs = task("spark_jobs", f"{SPARK_PY} -m src.spark.jobs")
    spark_reconcile = task("spark_reconcile", f"{WIN_PY} -m src.spark.reconcile")

    ingest >> staging >> clean >> analytical >> [load_warehouse, spark_jobs]
    load_warehouse >> verify_views >> dbt_build
    [dbt_build, spark_jobs] >> spark_reconcile
