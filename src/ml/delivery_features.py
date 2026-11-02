"""Order-level modelling dataset for delivery prediction at checkout (Phase 10).

Prediction moment: the customer places the order (``order_purchase_timestamp``). Every feature
below is known at that moment; nothing recorded after it is used:

* order: purchase date/time, the delivery PROMISE shown at checkout (estimated delivery date),
  basket (items, products, sellers, item price and freight charged), payment method / instalments;
* customer: state and zip-centroid coordinates of the order's customer record;
* main seller (the seller with the largest item value in the order): state and coordinates, the
  customer-seller distance;
* products: total weight and volume, main category (category of the largest-value line).

Excluded as LEAKAGE (known only after checkout): approval, carrier and delivery timestamps, all
durations, ``order_status`` (the final status), reviews, and the ``dq_*`` flags derived from
post-purchase events. ``LEAKAGE_COLUMNS`` is checked by ``build_dataset``.

Population: D-06 eligible orders (delivered, with a delivery date) purchased in the Q-11 candidate
window, i.e. orders whose outcome is known. Targets: ``delivery_days`` (Gold
``purchase_to_delivery_days``) and ``is_late`` (Gold, day level). One row per order: line and
product attributes are aggregated per order before joining (no fan-out).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

GOLD_DIR = Path(__file__).resolve().parents[2] / "data" / "processed" / "analytical"

NUMERIC = ["promised_days", "items_price_total", "items_freight_total", "freight_share", "item_count",
           "distinct_products", "distinct_sellers", "total_weight_kg", "total_volume_l", "max_installments",
           "distance_km", "customer_lat", "customer_lng", "seller_lat", "seller_lng",
           "purchase_hour", "purchase_weekday", "purchase_month"]
CATEGORICAL = ["customer_state", "seller_state", "main_category", "payment_type", "same_state"]
FEATURES = NUMERIC + CATEGORICAL
TARGETS = ["delivery_days", "is_late"]

# post-checkout information that must never enter FEATURES
LEAKAGE_COLUMNS = {"order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_status",
                   "approval_hours", "purchase_to_carrier_days", "approval_to_carrier_days", "carrier_to_customer_days",
                   "approval_to_delivery_days", "purchase_to_delivery_days", "delivery_vs_estimate_days", "is_late",
                   "review_count", "delivery_days"}


def _haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = (np.radians(x) for x in (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def build_dataset(gold_dir: Path = GOLD_DIR) -> pd.DataFrame:
    """One row per eligible order: ``order_id``, ``purchase_ts``, FEATURES and TARGETS, sorted by purchase time."""
    assert not (set(FEATURES) & LEAKAGE_COLUMNS), "a feature is on the leakage list"
    read = lambda t: pd.read_parquet(gold_dir / f"{t}.parquet")  # noqa: E731
    orders, lines, products, sellers = read("fact_orders"), read("fact_order_lines"), read("dim_products"), read("dim_sellers")

    o = orders[orders["in_candidate_window"] & (orders["order_status"] == "delivered")
               & orders["order_delivered_customer_date"].notna() & orders["has_items"]].copy()

    # line-level attributes -> one row per order (aggregate BEFORE joining: no fan-out)
    lp = lines.merge(products[["product_id", "category_reporting", "weight_g_valid", "product_length_cm",
                               "product_height_cm", "product_width_cm"]], on="product_id", validate="m:1")
    lp["weight_g"] = lp["weight_g_valid"] * lp["quantity"]
    lp["volume_cm3"] = lp["product_length_cm"] * lp["product_height_cm"] * lp["product_width_cm"] * lp["quantity"]
    basket = lp.groupby("order_id").agg(total_weight_kg=("weight_g", lambda s: s.sum(min_count=len(s)) / 1000),
                                        total_volume_l=("volume_cm3", lambda s: s.sum(min_count=len(s)) / 1000))
    main = (lp.sort_values(["order_id", "line_price_total", "seller_id", "product_id"], ascending=[True, False, True, True])
              .drop_duplicates("order_id")[["order_id", "seller_id", "category_reporting"]]
              .merge(sellers[["seller_id", "seller_state_std", "lat_centroid", "lng_centroid"]], on="seller_id", validate="m:1"))

    df = (o.merge(basket, left_on="order_id", right_index=True, how="left", validate="1:1")
           .merge(main, on="order_id", how="left", validate="1:1"))
    assert len(df) == len(o) and df["order_id"].is_unique

    out = pd.DataFrame({
        "order_id": df["order_id"],
        "purchase_ts": df["order_purchase_timestamp"],
        # promise shown at checkout, at day level like Gold's is_late (estimated date is date-only, D-08)
        "promised_days": (df["order_estimated_delivery_date"].dt.normalize() - df["purchase_date"]).dt.days,
        "items_price_total": df["items_price_total"], "items_freight_total": df["items_freight_total"],
        "freight_share": df["items_freight_total"] / (df["items_price_total"] + df["items_freight_total"]),
        "item_count": df["item_count"], "distinct_products": df["distinct_products"], "distinct_sellers": df["distinct_sellers"],
        "total_weight_kg": df["total_weight_kg"], "total_volume_l": df["total_volume_l"],
        "max_installments": df["max_installments"],
        "distance_km": _haversine_km(df["customer_lat_centroid"], df["customer_lng_centroid"], df["lat_centroid"], df["lng_centroid"]),
        "customer_lat": df["customer_lat_centroid"], "customer_lng": df["customer_lng_centroid"],
        "seller_lat": df["lat_centroid"], "seller_lng": df["lng_centroid"],
        "purchase_hour": df["order_purchase_timestamp"].dt.hour, "purchase_weekday": df["order_purchase_timestamp"].dt.dayofweek,
        "purchase_month": df["order_purchase_timestamp"].dt.month,
        "customer_state": df["customer_state_std"].astype(str), "seller_state": df["seller_state_std"].astype(str),
        "main_category": df["category_reporting"].astype(str),
        # payment method chosen at checkout; mixed payments are labelled by their combination (e.g. credit_card|voucher)
        "payment_type": df["payment_types"].fillna("unknown").astype(str),
        "same_state": (df["customer_state_std"].astype(str) == df["seller_state_std"].astype(str)).astype(str),
        "delivery_days": df["purchase_to_delivery_days"], "is_late": df["is_late"].astype(int),
    })
    # plain float64 with NaN (not pandas' nullable Float64 / pd.NA), so every scikit-learn estimator accepts them
    out[NUMERIC] = out[NUMERIC].astype("float64")
    assert all(out[c].dtype == "float64" for c in NUMERIC)
    return out.sort_values(["purchase_ts", "order_id"], kind="stable").reset_index(drop=True)


def preprocessor(kind: str):
    """Leakage-safe preprocessing (fitted inside a Pipeline, i.e. on training folds only).

    ``linear``: median imputation + scaling, one-hot (rare levels < 100 grouped); ``onehot``: the same
    without scaling (tree models); ``native``: numeric passthrough (NaN kept) + ordinal codes, unknown
    categories -> NaN, for HistGradientBoosting with ``categorical_features=CATEGORICAL_INDEX``.
    """
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

    onehot = OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=100, sparse_output=False)
    if kind == "linear":
        return ColumnTransformer([("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), NUMERIC),
                                  ("cat", onehot, CATEGORICAL)])
    if kind == "onehot":
        return ColumnTransformer([("num", SimpleImputer(strategy="median"), NUMERIC), ("cat", onehot, CATEGORICAL)])
    if kind == "native":
        return ColumnTransformer([("num", "passthrough", NUMERIC),
                                  ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan), CATEGORICAL)])
    raise ValueError(f"unknown preprocessor kind: {kind}")


CATEGORICAL_INDEX = list(range(len(NUMERIC), len(NUMERIC) + len(CATEGORICAL)))


def temporal_split(data: pd.DataFrame, test_start: str = "2018-05-01") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Development = purchases before ``test_start``; test = from ``test_start`` on (the most recent months)."""
    cut = pd.Timestamp(test_start)
    dev, test = data[data["purchase_ts"] < cut], data[data["purchase_ts"] >= cut]
    assert dev["purchase_ts"].max() < test["purchase_ts"].min()
    return dev.reset_index(drop=True), test.reset_index(drop=True)
