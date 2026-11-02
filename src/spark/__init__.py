"""PySpark workloads on the Gold Parquet outputs (Phase 6).

``jobs`` runs in the Spark environment (WSL2, ``requirements-spark.txt``); ``reconcile`` runs in the
project environment on Windows and compares the Spark results with PostgreSQL. This package
``__init__`` imports neither, so each side only needs its own dependencies.
"""
