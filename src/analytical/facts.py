"""Fact tables (strategy D-13, D-15, D-08 item 3, section 13 steps 11-13).

Order of operations, so that no one-to-many table is ever joined before it is aggregated:

1. order_items -> order lines (D-15, six-attribute grain, lossless);
2. order_items -> item summary per order;     payments -> payment summary per order;
   reviews -> review count per order          (each aggregated on its own);
3. each summary is joined to orders with ``validate="one_to_one"``;
4. transaction-grain facts (payments, reviews) receive order context via ``many_to_one``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .joins import JoinLog
from .schema import (CANDIDATE_WINDOW, DELIVERED, MONEY_DECIMALS, PAYMENT_MISMATCH_TOLERANCE, SIX_ATTRIBUTE_GRAIN,
                     TABLES)

P, A, C, D, E = ("order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
                 "order_delivered_customer_date", "order_estimated_delivery_date")


def conform(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Select the declared columns in declared order and cast to the declared dtypes."""
    cols = TABLES[table].columns
    out = df[[c for c, _ in cols]].copy()
    for c, dtype in cols:
        if dtype in ("object", "category") or str(out[c].dtype) == dtype:
            continue
        out[c] = out[c].astype(dtype)
    return out.reset_index(drop=True)


def in_window(ts: pd.Series) -> pd.Series:
    month = ts.dt.to_period("M")
    return (month >= CANDIDATE_WINDOW[0]) & (month <= CANDIDATE_WINDOW[1])


def order_context(orders: pd.DataFrame, customers: pd.DataFrame, joins: JoinLog) -> pd.DataFrame:
    """One row per order: the conformed keys and attributes carried by every fact."""
    ctx = joins.merge("orders_x_customers.context", orders[["order_id", "customer_id", "order_status", P]],
                      customers[["customer_id", "customer_unique_id"]], on="customer_id", validate="one_to_one")
    ctx["purchase_date"] = ctx[P].dt.normalize()
    ctx["in_candidate_window"] = in_window(ctx[P]).astype(bool)
    return ctx.drop(columns="customer_id")


# ----------------------------------------------------------------------------- D-15
def build_order_lines(items: pd.DataFrame) -> pd.DataFrame:
    """Group order_items on all six non-sequence attributes; every row lands in exactly one line."""
    key = list(SIX_ATTRIBUTE_GRAIN)
    it = items.assign(_line=items.groupby(key, sort=False, dropna=False).ngroup())
    g = it.groupby("_line", sort=True)
    lines = g.agg(order_id=("order_id", "first"), product_id=("product_id", "first"), seller_id=("seller_id", "first"),
                  shipping_limit_date=("shipping_limit_date", "first"), unit_price=("price", "first"),
                  freight_value_per_row=("freight_value", "first"), quantity=("order_item_id", "size"),
                  first_order_item_id=("order_item_id", "min"), freight_total=("freight_value", "sum"),
                  dq_shipping_limit_out_of_range=("dq_shipping_limit_out_of_range", "max"))
    ordered = it.sort_values(["_line", "order_item_id"], kind="stable")
    ids = ordered["order_item_id"].to_numpy(dtype="int64")
    bounds = np.cumsum(ordered.groupby("_line", sort=True).size().to_numpy())[:-1]
    lines["order_item_ids"] = pd.Series(np.split(ids, bounds), index=lines.index, dtype=object)
    lines["quantity"] = lines["quantity"].astype("int64")
    lines["line_price_total"] = lines["unit_price"] * lines["quantity"]
    lines["order_line_id"] = lines["order_id"] + ":" + lines["first_order_item_id"].astype("string")
    return lines.sort_values(["order_id", "first_order_item_id"], kind="stable").reset_index(drop=True)


def fact_order_lines(items, ctx, joins: JoinLog) -> pd.DataFrame:
    lines = build_order_lines(items)
    lines = joins.merge("order_lines_x_order_context", lines, ctx, on="order_id", validate="many_to_one")
    return conform(lines, "fact_order_lines")


# ----------------------------------------------------------------------------- D-13 summaries
def item_summary(items: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    g = items.groupby("order_id", sort=True)
    out = g.agg(item_count=("order_item_id", "size"), distinct_products=("product_id", "nunique"),
                distinct_sellers=("seller_id", "nunique"), items_price_total=("price", "sum"),
                items_freight_total=("freight_value", "sum"))
    out["order_line_count"] = lines.groupby("order_id").size().reindex(out.index)
    return out.reset_index()


def payment_summary(payments: pd.DataFrame) -> pd.DataFrame:
    g = payments.groupby("order_id", sort=True)
    types = payments["payment_type"].astype("string")
    out = g.agg(payment_rows=("payment_sequential", "size"), payment_total=("payment_value", "sum"))
    out["payment_types"] = types.groupby(payments["order_id"]).agg(lambda s: "|".join(sorted(set(s.dropna()))))
    out["has_voucher"] = (types == "voucher").fillna(False).groupby(payments["order_id"]).any()
    usable = payments.loc[~payments["dq_installments_zero"]]          # D-16: zero instalments excluded here only
    out["max_installments"] = usable.groupby("order_id")["payment_installments"].max().reindex(out.index)
    return out.reset_index()


def review_summary(reviews: pd.DataFrame) -> pd.DataFrame:
    return reviews.groupby("order_id", sort=True).size().rename("review_count").reset_index()  # Q-04: no score


# ----------------------------------------------------------------------------- D-08 item 3
def _days(start: pd.Series, end: pd.Series) -> pd.Series:
    return (end - start).dt.total_seconds() / 86_400


def durations(o: pd.DataFrame) -> pd.DataFrame:
    """Each duration is missing where an input is missing or its own chronology flag is True."""
    out = pd.DataFrame(index=o.index)
    out["approval_hours"] = (o[A] - o[P]).dt.total_seconds() / 3_600
    out["purchase_to_carrier_days"] = _days(o[P], o[C]).mask(o["dq_carrier_before_purchase"])
    out["approval_to_carrier_days"] = _days(o[A], o[C]).mask(o["dq_carrier_before_approval"])
    out["carrier_to_customer_days"] = _days(o[C], o[D]).mask(o["dq_delivered_before_carrier"])
    out["approval_to_delivery_days"] = _days(o[A], o[D]).mask(o["dq_delivered_before_approval"])
    out["purchase_to_delivery_days"] = _days(o[P], o[D])
    out["delivery_vs_estimate_days"] = _days(o[E], o[D].dt.normalize())          # day level
    late = out["delivery_vs_estimate_days"] > 0
    out["is_late"] = late.astype("boolean").mask(out["delivery_vs_estimate_days"].isna())
    return out


def fact_orders(orders, customers, items, payments, reviews, lines, joins: JoinLog) -> pd.DataFrame:
    cust = customers[["customer_id", "customer_unique_id", "customer_zip_code_prefix", "city_key", "customer_state_std",
                      "lat_centroid", "lng_centroid", "dq_no_geolocation"]].rename(columns={
        "city_key": "customer_city_key", "lat_centroid": "customer_lat_centroid",
        "lng_centroid": "customer_lng_centroid", "dq_no_geolocation": "dq_customer_no_geolocation"})
    o = joins.merge("orders_x_customers", orders, cust, on="customer_id", validate="one_to_one")
    o = joins.merge("orders_x_item_summary", o, item_summary(items, lines), on="order_id", validate="one_to_one")
    o = joins.merge("orders_x_payment_summary", o, payment_summary(payments), on="order_id", validate="one_to_one")
    o = joins.merge("orders_x_review_summary", o, review_summary(reviews), on="order_id", validate="one_to_one")

    for c in ("item_count", "order_line_count", "distinct_products", "distinct_sellers", "payment_rows", "review_count"):
        o[c] = o[c].fillna(0).astype("int64")                    # a count over no rows is 0 ...
    o["has_items"] = o["item_count"] > 0                          # ... but totals stay missing (Q-16)
    o["has_payment"] = o["payment_rows"] > 0
    o["has_voucher"] = o["has_voucher"].fillna(False).astype(bool)

    diff = (o["payment_total"] - (o["items_price_total"] + o["items_freight_total"])).round(MONEY_DECIMALS)
    o["payment_minus_items"] = diff                               # missing unless the order has items and payments
    o["dq_payment_items_mismatch"] = (diff.abs() > PAYMENT_MISMATCH_TOLERANCE).fillna(False).astype(bool)

    o["purchase_date"] = o[P].dt.normalize()
    o["purchase_year_month"] = o[P].dt.to_period("M").astype(str)
    o["in_candidate_window"] = in_window(o[P]).astype(bool)
    seq = o.sort_values(["customer_unique_id", P, "order_id"], kind="stable").groupby("customer_unique_id").cumcount() + 1
    o["customer_order_seq"] = seq.reindex(o.index).astype("int64")
    o["is_first_order"] = o["customer_order_seq"] == 1
    o = pd.concat([o, durations(o)], axis=1)
    return conform(o, "fact_orders")


# ----------------------------------------------------------------------------- transaction facts
def fact_payments(payments, ctx, joins: JoinLog) -> pd.DataFrame:
    return conform(joins.merge("payments_x_order_context", payments, ctx, on="order_id", validate="many_to_one"),
                   "fact_payments")


def fact_reviews(reviews, ctx, orders, joins: JoinLog) -> pd.DataFrame:
    r = joins.merge("reviews_x_order_context", reviews, ctx, on="order_id", validate="many_to_one")
    r = joins.merge("reviews_x_delivery_date", r, orders[["order_id", D]], on="order_id", validate="many_to_one")
    return conform(r, "fact_reviews")


def delivered_mask(df: pd.DataFrame) -> pd.Series:
    return (df["order_status"].astype("string") == DELIVERED).fillna(False).astype(bool)
