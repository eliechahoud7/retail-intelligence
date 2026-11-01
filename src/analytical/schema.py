"""Analytical-layer contract: tables, grains, keys, explicit schemas, parameters, baselines.

Design (strategy D-01, D-13, D-15, sections 2.1 and 13 steps 11-13; decisions taken for
Task 4.4 on Q-04, Q-11 and person-level eligibility/location are recorded in ``DECISIONS``):

* a star schema: four facts (order lines, orders, payments, reviews) and four dimensions
  (customers = persons, products, sellers, dates);
* every one-to-many table is aggregated to its target grain *before* it is joined, and
  every join declares ``validate=`` (P5, D-13);
* nothing is filtered out: the candidate analysis window is a flag (Q-11).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from src.staging.product_flags import LISTING_FLAGS, PHYSICAL_FLAGS, ROW_FLAGS

CLEAN_INPUTS: tuple[str, ...] = ("customers", "orders", "order_items", "payments", "reviews", "products", "sellers")

# ----------------------------------------------------------------------------- parameters
CANDIDATE_WINDOW = (pd.Period("2017-01", "M"), pd.Period("2018-08", "M"))  # Q-11 candidate, purchase month
PAYMENT_MISMATCH_TOLERANCE = 0.01  # D-13 / NB03 6.2: |round(payment - items, 2)| > 0.01
MONEY_DECIMALS = 2                 # rounding used for comparisons and the stored difference only
SIX_ATTRIBUTE_GRAIN = ("order_id", "product_id", "seller_id", "price", "freight_value", "shipping_limit_date")  # D-15
DELIVERED = "delivered"

PARAMETERS: Mapping[str, object] = MappingProxyType({
    "candidate_window_purchase_months": [str(p) for p in CANDIDATE_WINDOW],
    "candidate_window_applied_as": "flag in_candidate_window (no rows removed; Q-11 open)",
    "payment_mismatch_tolerance": PAYMENT_MISMATCH_TOLERANCE,
    "payment_minus_items_rounding_decimals": MONEY_DECIMALS,
    "order_line_grain": list(SIX_ATTRIBUTE_GRAIN),
    "delivered_variant_status": DELIVERED,
    "duration_rule": "each duration is missing where an input is missing or its D-08 chronology flag is True",
    "date_only_comparisons": "delivery vs estimated delivery compared at day level",
})

DECISIONS: Mapping[str, str] = MappingProxyType({
    "Q-11": "candidate window 2017-01..2018-08 applied as a flag only; W05 reports rows outside it",
    "Q-04": "fact_orders carries review_count only; no order-level score",
    "eligibility": "dim_customers measures in two labelled variants: all orders, and status = delivered",
    "location": "customer location kept per order in fact_orders; dim_customers has no chosen address",
    "Q-03": "item-based and payment-based totals are separate, labelled columns; never combined into one revenue",
    "Q-16": "orders without items / payments are kept; their counts are 0 and their totals missing (not 0)",
    "D-16": "max_installments ignores dq_installments_zero rows; payment totals include them",
})


# ----------------------------------------------------------------------------- table specs
@dataclass(frozen=True)
class TableSpec:
    name: str
    kind: str                         # "fact" or "dimension"
    purpose: str
    grain: str
    primary_key: tuple[str, ...]
    foreign_keys: tuple[tuple[str, str, str], ...]  # (column, parent table, parent column)
    sources: tuple[str, ...]
    columns: tuple[tuple[str, str], ...]            # (name, dtype) in output order


_STR, _CAT, _TS, _F, _I, _NI, _B, _NB, _LIST = ("string", "category", "datetime64[us]", "float64", "int64", "Int64",
                                                 "bool", "boolean", "object")
_ORDER_TS = ("order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
             "order_delivered_customer_date", "order_estimated_delivery_date")
_ORDER_FLAGS = ("dq_delivered_missing_approval", "dq_delivered_missing_carrier_date", "dq_delivered_missing_delivery_date",
                "dq_carrier_before_purchase", "dq_delivered_before_carrier", "dq_carrier_before_approval",
                "dq_delivered_before_approval", "dq_status_canceled_with_date", "dq_same_person_same_second")
_ORDER_CONTEXT = (("customer_unique_id", _STR), ("order_status", _CAT), ("order_purchase_timestamp", _TS),
                  ("purchase_date", _TS), ("in_candidate_window", _B))
DURATIONS = ("approval_hours", "purchase_to_carrier_days", "approval_to_carrier_days", "carrier_to_customer_days",
             "approval_to_delivery_days", "purchase_to_delivery_days", "delivery_vs_estimate_days")
_PRODUCT_FLAGS = (*LISTING_FLAGS, *PHYSICAL_FLAGS, *ROW_FLAGS, "dq_weight_zero")

TABLES: Mapping[str, TableSpec] = MappingProxyType({
    "fact_order_lines": TableSpec(
        "fact_order_lines", "fact",
        "Product, category, seller and demand analysis: units sold and item-based sales per line (D-15)",
        "one order line = order_items rows identical in (order_id, product_id, seller_id, price, freight_value, "
        "shipping_limit_date)",
        ("order_line_id",),
        (("order_id", "fact_orders", "order_id"), ("product_id", "dim_products", "product_id"),
         ("seller_id", "dim_sellers", "seller_id"), ("customer_unique_id", "dim_customers", "customer_unique_id"),
         ("purchase_date", "dim_dates", "date")),
        ("order_items", "orders", "customers"),
        (("order_line_id", _STR), ("order_id", _STR), ("first_order_item_id", _NI), ("product_id", _STR),
         ("seller_id", _STR), ("shipping_limit_date", _TS), ("quantity", _I), ("unit_price", _F),
         ("freight_value_per_row", _F), ("line_price_total", _F), ("freight_total", _F), ("order_item_ids", _LIST),
         ("dq_shipping_limit_out_of_range", _B), *_ORDER_CONTEXT)),
    "fact_orders": TableSpec(
        "fact_orders", "fact",
        "Order-level sales, payment reconciliation, delivery/logistics, cohort and retention analysis (D-13, D-08)",
        "one order", ("order_id",),
        (("customer_unique_id", "dim_customers", "customer_unique_id"), ("purchase_date", "dim_dates", "date")),
        ("orders", "customers", "order_items", "payments", "reviews"),
        (("order_id", _STR), ("customer_id", _STR), ("customer_unique_id", _STR), ("order_status", _CAT),
         *((c, _TS) for c in _ORDER_TS), ("purchase_date", _TS), ("purchase_year_month", _STR),
         ("in_candidate_window", _B), ("customer_order_seq", _I), ("is_first_order", _B),
         ("customer_zip_code_prefix", _STR), ("customer_city_key", _STR), ("customer_state_std", _CAT),
         ("customer_lat_centroid", _F), ("customer_lng_centroid", _F), ("dq_customer_no_geolocation", _B),
         *((c, _B) for c in _ORDER_FLAGS),
         ("has_items", _B), ("item_count", _I), ("order_line_count", _I), ("distinct_products", _I),
         ("distinct_sellers", _I), ("items_price_total", _F), ("items_freight_total", _F),
         ("has_payment", _B), ("payment_rows", _I), ("payment_total", _F), ("payment_types", _STR),
         ("has_voucher", _B), ("max_installments", _NI),
         ("payment_minus_items", _F), ("dq_payment_items_mismatch", _B), ("review_count", _I),
         *((c, _F) for c in DURATIONS), ("is_late", _NB))),
    "fact_payments": TableSpec(
        "fact_payments", "fact", "Payment-method, instalment and cash-collected analysis at transaction level",
        "one payment transaction", ("order_id", "payment_sequential"),
        (("order_id", "fact_orders", "order_id"), ("customer_unique_id", "dim_customers", "customer_unique_id"),
         ("purchase_date", "dim_dates", "date")),
        ("payments", "orders", "customers"),
        (("order_id", _STR), ("payment_sequential", _NI), ("payment_type", _CAT), ("payment_installments", _NI),
         ("payment_value", _F), ("dq_payment_zero", _B), ("dq_installments_zero", _B), *_ORDER_CONTEXT)),
    "fact_reviews": TableSpec(
        "fact_reviews", "fact", "Customer satisfaction analysis; review-level counts use distinct review_id (D-07, Q-04)",
        "one review-order link", ("review_id", "order_id"),
        (("order_id", "fact_orders", "order_id"), ("customer_unique_id", "dim_customers", "customer_unique_id"),
         ("purchase_date", "dim_dates", "date")),
        ("reviews", "orders", "customers"),
        (("review_id", _STR), ("order_id", _STR), ("review_score", _NI), ("review_comment_title", _STR),
         ("review_comment_message", _STR), ("review_creation_date", _TS), ("review_answer_timestamp", _TS),
         ("has_comment_title", _B), ("has_comment_message", _B), ("dq_review_id_shared", _B),
         ("dq_review_content_duplicate", _B), ("dq_review_before_purchase", _B), ("dq_review_before_delivery", _B),
         *_ORDER_CONTEXT, ("order_delivered_customer_date", _TS))),
    "dim_customers": TableSpec(
        "dim_customers", "dimension",
        "Person-level customer analytics: RFM inputs, cohorts, retention, historical value (D-01). Measures span "
        "the full history: do not use them as prediction features; derive time-dependent features from the fact "
        "tables with an explicit cut-off date to avoid target leakage",
        "one person (customer_unique_id)", ("customer_unique_id",), (), ("customers", "orders", "order_items", "payments"),
        (("customer_unique_id", _STR), ("n_customer_ids", _I), ("n_distinct_zips", _I), ("n_distinct_states", _I),
         ("first_purchase_ts", _TS), ("last_purchase_ts", _TS), ("cohort_month", _STR),
         ("n_orders", _I), ("n_items", _I), ("items_price_total", _F), ("items_freight_total", _F), ("payment_total", _F),
         ("n_orders_delivered", _I), ("n_items_delivered", _I), ("items_price_total_delivered", _F),
         ("items_freight_total_delivered", _F), ("payment_total_delivered", _F),
         ("first_delivered_purchase_ts", _TS), ("last_delivered_purchase_ts", _TS))),
    "dim_products": TableSpec(
        "dim_products", "dimension", "Product attributes, category labels (category_map) and quality flags",
        "one product", ("product_id",), (), ("products",),
        (("product_id", _STR), ("product_category_name", _STR), ("category_en_clean", _STR), ("category_reporting", _STR),
         ("product_name_length", _NI), ("product_description_length", _NI), ("product_photos_qty", _NI),
         ("product_weight_g", _NI), ("weight_g_valid", _NI), ("product_length_cm", _NI), ("product_height_cm", _NI),
         ("product_width_cm", _NI), *((c, _B) for c in _PRODUCT_FLAGS),
         ("dq_density_suspicious", _B), ("dq_dims_16x2x11", _B))),
    "dim_sellers": TableSpec(
        "dim_sellers", "dimension", "Seller location and quality flags", "one seller", ("seller_id",), (), ("sellers",),
        (("seller_id", _STR), ("seller_zip_code_prefix", _STR), ("seller_city", _STR), ("city_key", _STR),
         ("seller_state_std", _CAT), ("dq_city_malformed", _B), ("lat_centroid", _F), ("lng_centroid", _F),
         ("dq_no_geolocation", _B))),
    "dim_dates": TableSpec(
        "dim_dates", "dimension", "Calendar for time series, seasonality and forecasting",
        "one calendar day from the first to the last event date", ("date",), (),
        ("orders", "order_items", "reviews"),
        (("date", _TS), ("year", _I), ("quarter", _I), ("month", _I), ("year_month", _STR), ("iso_year", _I),
         ("iso_week", _I), ("day_of_week", _I), ("day_name", _STR), ("is_weekend", _B), ("in_candidate_window", _B))),
})

FACTS = tuple(n for n, t in TABLES.items() if t.kind == "fact")
DIMENSIONS = tuple(n for n, t in TABLES.items() if t.kind == "dimension")
NON_NEGATIVE_DURATIONS = tuple(d for d in DURATIONS if d != "delivery_vs_estimate_days")

# ----------------------------------------------------------------------------- baselines
BASELINE_ANALYTICAL = MappingProxyType({
    "order_lines": 102_425,                  # 11.3.3 (derived from NB03 3.3)
    "orders": 99_441,                        # 11.3.3
    "persons": 96_096,                       # NB02 5.2
    "persons_with_several_orders": 2_997,    # NB02 5.2
    "dq_payment_items_mismatch": 303,        # 11.3.1 / NB03 6.2
    "orders_reconciled": 98_665,             # NB03 6.2 (orders with items and payments)
    "mismatch_overpaid": 264,                # NB03 6.2
    "mismatch_underpaid": 39,                # NB03 6.2
    "mismatch_gt_1": 249,                    # NB03 6.2
    "orders_without_items": 775,             # NB02 / Q-16
    "orders_without_payment": 1,             # NB03 9 / Q-16
    "orders_with_several_reviews": 547,      # NB03 9 / Q-04
    "late_deliveries": 6_535,                # NB03 5.4 (day level)
    "delivery_over_60_days": 306,            # NB03 5.4
})
BASELINE_MAX_QUANTITY = 20                    # D-15: max quantity <= 20 (NB02 5.4)
BASELINE_MISMATCH_RANGE = (-51.62, 182.81)    # NB03 6.2
BASELINE_ORDERS_WITHOUT_ITEMS_BY_STATUS = MappingProxyType(  # Q-16
    {"unavailable": 603, "canceled": 164, "created": 5, "invoiced": 2, "shipped": 1})
