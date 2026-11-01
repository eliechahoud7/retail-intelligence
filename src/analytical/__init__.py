"""Analytical layer (Task 4.4).

Consumes the clean Parquet outputs and writes a star schema to ``data/processed/analytical/``:

* facts: ``fact_order_lines`` (D-15 six-attribute grain), ``fact_orders`` (D-13 summaries,
  payment/item reconciliation, delivery durations), ``fact_payments``, ``fact_reviews``;
* dimensions: ``dim_customers`` (persons), ``dim_products``, ``dim_sellers``, ``dim_dates``.

Typical use::

    from src.analytical import run_analytical
    result = run_analytical()
"""

from .facts import build_order_lines
from .inputs import AnalyticalInputError, load_clean
from .pipeline import DEFAULT_ANALYTICAL_DIR, DEFAULT_REPORT_DIR, AnalyticalResult, run_analytical
from .schema import TABLES
from .transform import build_analytical

__all__ = ["run_analytical", "AnalyticalResult", "DEFAULT_ANALYTICAL_DIR", "DEFAULT_REPORT_DIR", "build_analytical",
           "build_order_lines", "load_clean", "AnalyticalInputError", "TABLES"]
