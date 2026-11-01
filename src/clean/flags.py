"""Clean-layer data-quality flags (strategy D-06, D-07, D-08, D-09, D-16).

Every rule is the one measured in notebook 03 and approved in the strategy. Each function
takes staged frames, never mutates them, and returns a DataFrame of new columns aligned
with the table it describes. Flags are plain ``bool``: a comparison involving a missing
value is False (it is not a violation), as in NB03.
"""

from __future__ import annotations

import pandas as pd

from .schema import DENSITY_THRESHOLD_G_PER_CM3, SHIPPING_LIMIT_MAX_OFFSET_YEARS, SUSPICIOUS_DIMS_CM
from .standardise import lookup

P, A = "order_purchase_timestamp", "order_approved_at"
C, D = "order_delivered_carrier_date", "order_delivered_customer_date"


def _bool(mask: pd.Series) -> pd.Series:
    """Nullable/NaN-aware mask -> plain bool (missing -> False)."""
    return mask.fillna(False).astype(bool) if mask.dtype != bool else mask


# ----------------------------------------------------------------------------- orders
def order_flags(orders: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    status = orders["order_status"].astype("string")
    delivered = _bool(status == "delivered")
    canceled = _bool(status == "canceled")
    p, a, c, d = orders[P], orders[A], orders[C], orders[D]

    out = pd.DataFrame(index=orders.index)
    # D-06: gaps on delivered orders
    out["dq_delivered_missing_approval"] = delivered & a.isna()
    out["dq_delivered_missing_carrier_date"] = delivered & c.isna()
    out["dq_delivered_missing_delivery_date"] = delivered & d.isna()
    # D-08: chronology (evaluated only where both timestamps exist; NaT comparisons are False)
    out["dq_carrier_before_purchase"] = _bool(c < p)
    out["dq_delivered_before_carrier"] = _bool(d < c)
    out["dq_carrier_before_approval"] = _bool(c < a)
    out["dq_delivered_before_approval"] = _bool(d < a)
    out["dq_status_canceled_with_date"] = canceled & d.notna()
    # D-07: same person, same purchase second (every order of such a group is flagged)
    person = lookup(orders, customers, "customer_id", "customer_id", ["customer_unique_id"])["customer_unique_id"]
    out["dq_same_person_same_second"] = pd.DataFrame({"u": person, "p": p}).duplicated(keep=False).astype(bool)
    return out


# ----------------------------------------------------------------------------- order_items
def shipping_limit_threshold(orders: pd.DataFrame) -> pd.Timestamp:
    """Last purchase timestamp plus the approved offset (NB03 5.1)."""
    return orders[P].max() + pd.DateOffset(years=SHIPPING_LIMIT_MAX_OFFSET_YEARS)


def order_item_flags(items: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=items.index)
    out["dq_shipping_limit_out_of_range"] = _bool(items["shipping_limit_date"] > shipping_limit_threshold(orders))
    return out


# ----------------------------------------------------------------------------- payments
def payment_flags(payments: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=payments.index)
    out["dq_payment_zero"] = _bool(payments["payment_value"] == 0)
    out["dq_installments_zero"] = _bool(payments["payment_installments"] == 0)
    return out


# ----------------------------------------------------------------------------- reviews
REVIEW_CONTENT = ["order_id", "review_score", "review_comment_title", "review_comment_message", "review_creation_date"]


def review_flags(reviews: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=reviews.index)
    out["has_comment_title"] = reviews["review_comment_title"].notna().astype(bool)
    out["has_comment_message"] = reviews["review_comment_message"].notna().astype(bool)
    out["dq_review_id_shared"] = reviews["review_id"].duplicated(keep=False).astype(bool)
    out["dq_review_content_duplicate"] = reviews.duplicated(subset=REVIEW_CONTENT, keep="first").astype(bool)

    od = lookup(reviews, orders, "order_id", "order_id", [P, D])
    created = reviews["review_creation_date"].dt.normalize()  # date-only field: compare at day level
    out["dq_review_before_purchase"] = _bool(created < od[P].dt.normalize())
    out["dq_review_before_delivery"] = _bool(created < od[D].dt.normalize())
    return out


# ----------------------------------------------------------------------------- products
DIMENSIONS = ["product_length_cm", "product_height_cm", "product_width_cm"]


def product_flags(products: pd.DataFrame, metadata_flags: pd.DataFrame) -> pd.DataFrame:
    """Staging metadata flags (reused, joined on product_id) plus the D-09 flags."""
    meta_cols = [c for c in metadata_flags.columns if c != "product_id"]
    out = lookup(products, metadata_flags, "product_id", "product_id", meta_cols)
    if not products["product_id"].is_unique:  # pragma: no cover - V04 also guards this
        raise ValueError("products.product_id is not unique")

    weight = products["product_weight_g"]
    out["dq_weight_zero"] = _bool(weight == 0)
    out["weight_g_valid"] = weight.mask(out["dq_weight_zero"])  # original column unchanged
    volume = products[DIMENSIONS[0]] * products[DIMENSIONS[1]] * products[DIMENSIONS[2]]
    out["dq_density_suspicious"] = _bool(weight / volume > DENSITY_THRESHOLD_G_PER_CM3)
    length, height, width = SUSPICIOUS_DIMS_CM
    out["dq_dims_16x2x11"] = _bool((products[DIMENSIONS[0]] == length) & (products[DIMENSIONS[1]] == height)
                                   & (products[DIMENSIONS[2]] == width))
    return out


def product_categories(products: pd.DataFrame, category_map: pd.DataFrame, unknown_label: str) -> pd.DataFrame:
    """``category_en_clean`` from ``category_map`` and the reporting label (D-12)."""
    out = lookup(products, category_map, "product_category_name", "product_category_name", ["category_en_clean"])
    out["category_en_clean"] = out["category_en_clean"].astype("string")
    missing = products["product_category_name"].isna()
    out["category_reporting"] = out["category_en_clean"].mask(missing, unknown_label).astype("string")
    return out
