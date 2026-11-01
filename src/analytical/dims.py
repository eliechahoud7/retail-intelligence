"""Dimension tables.

* ``dim_customers`` is keyed on the person (``customer_unique_id``, D-01) and built from
  ``fact_orders`` (already one row per order, so summing its totals cannot double-count).
  Measures come in two labelled variants: all orders, and orders with status ``delivered``.
  It deliberately has no address: location varies by order and stays in ``fact_orders``.
  Its measures span the full history; time-bounded features for modelling must be derived
  from the facts with an explicit cut-off date to avoid target leakage.
* ``dim_products`` / ``dim_sellers`` carry the clean attributes, labels and flags.
* ``dim_dates`` covers every day from the first to the last event date.
"""

from __future__ import annotations

import pandas as pd

from .facts import P, conform, delivered_mask, in_window


def _person_measures(orders: pd.DataFrame, suffix: str = "") -> pd.DataFrame:
    g = orders.groupby("customer_unique_id", sort=True)
    out = g.agg(**{f"n_orders{suffix}": ("order_id", "size"), f"n_items{suffix}": ("item_count", "sum")})
    for c in ("items_price_total", "items_freight_total", "payment_total"):
        out[f"{c}{suffix}"] = g[c].sum(min_count=1)  # missing when no order of the person has the measure
    return out


def dim_customers(fo: pd.DataFrame) -> pd.DataFrame:
    g = fo.groupby("customer_unique_id", sort=True)
    out = g.agg(n_customer_ids=("customer_id", "nunique"), n_distinct_zips=("customer_zip_code_prefix", "nunique"),
                n_distinct_states=("customer_state_std", "nunique"), first_purchase_ts=(P, "min"),
                last_purchase_ts=(P, "max"))
    out["cohort_month"] = out["first_purchase_ts"].dt.to_period("M").astype(str)
    out = out.join(_person_measures(fo))

    delivered = fo[delivered_mask(fo)]
    dm = _person_measures(delivered, "_delivered").reindex(out.index)
    for c in ("n_orders_delivered", "n_items_delivered"):
        dm[c] = dm[c].fillna(0).astype("int64")
    dg = delivered.groupby("customer_unique_id")[P]
    dm["first_delivered_purchase_ts"] = dg.min().reindex(out.index)
    dm["last_delivered_purchase_ts"] = dg.max().reindex(out.index)
    out = out.join(dm).reset_index()
    for c in ("n_customer_ids", "n_distinct_zips", "n_distinct_states", "n_orders", "n_items"):
        out[c] = out[c].astype("int64")
    return conform(out, "dim_customers")


def dim_products(products: pd.DataFrame) -> pd.DataFrame:
    return conform(products, "dim_products")


def dim_sellers(sellers: pd.DataFrame) -> pd.DataFrame:
    return conform(sellers, "dim_sellers")


def dim_dates(event_dates: list[pd.Series]) -> pd.DataFrame:
    lo = min(s.min() for s in event_dates).normalize()
    hi = max(s.max() for s in event_dates).normalize()
    d = pd.Series(pd.date_range(lo, hi, freq="D").astype("datetime64[us]"), name="date")
    iso = d.dt.isocalendar()
    out = pd.DataFrame({
        "date": d, "year": d.dt.year, "quarter": d.dt.quarter, "month": d.dt.month,
        "year_month": d.dt.to_period("M").astype(str), "iso_year": iso["year"], "iso_week": iso["week"],
        "day_of_week": d.dt.day_of_week + 1, "day_name": d.dt.day_name(),
    })
    out["is_weekend"] = out["day_of_week"] >= 6
    out["in_candidate_window"] = in_window(d).astype(bool)
    for c in ("year", "quarter", "month", "iso_year", "iso_week", "day_of_week"):
        out[c] = out[c].astype("int64")
    return conform(out, "dim_dates")
