"""Clean-layer contract: inputs, parameters, added columns (with lineage) and baselines.

Every column the clean layer adds is declared in ``ADDED_COLUMNS`` together with its dtype,
source column(s), rule and the strategy decision that approves it
(reports/data_preparation_strategy.md). Source columns are never modified: a clean table is
its staged table, in the same row order, with the declared columns appended.

Baselines are the values measured in notebooks 01-03 and listed in strategy section 11.3.
They are evaluated as *regression* checks, and only when the raw files match the approved
checksums.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from src.staging.product_flags import LISTING_FLAGS, PHYSICAL_FLAGS, ROW_FLAGS
from src.staging.schema import BRAZIL_BBOX

# ----------------------------------------------------------------------------- inputs / outputs
SOURCE_TABLES: tuple[str, ...] = ("customers", "geolocation", "orders", "order_items", "payments",
                                  "reviews", "products", "sellers", "translation")
STAGING_DERIVED: tuple[str, ...] = ("geolocation_rows", "geolocation_points", "products_metadata_flags")
STAGING_INPUTS: tuple[str, ...] = SOURCE_TABLES + STAGING_DERIVED
REFERENCE_TABLES: tuple[str, ...] = ("category_map", "zip_lookup")
CLEAN_OUTPUTS: tuple[str, ...] = SOURCE_TABLES + REFERENCE_TABLES

GRAIN: Mapping[str, str] = MappingProxyType({
    "customers": "one customer record per order (customer_id)",
    "geolocation": "one raw coordinate row (no key; 1:1 with staging rows)",
    "orders": "one order (order_id)",
    "order_items": "one unit line within an order (order_id, order_item_id)",
    "payments": "one payment transaction (order_id, payment_sequential)",
    "reviews": "one review-order link (review_id, order_id)",
    "products": "one product listing (product_id)",
    "sellers": "one seller (seller_id)",
    "translation": "one category name mapping (product_category_name)",
    "category_map": "one Portuguese category (product_category_name)",
    "zip_lookup": "one zip prefix (geolocation_zip_code_prefix)",
})

# ----------------------------------------------------------------------------- parameters
DENSITY_THRESHOLD_G_PER_CM3 = 2.0                    # D-09 / NB03 6.4 (heuristic screen, strict >)
SUSPICIOUS_DIMS_CM = (16, 2, 11)                     # D-09 / NB03 6.4: (length, height, width)
SHIPPING_LIMIT_MAX_OFFSET_YEARS = 1                  # D-08 / NB03 5.1: > last purchase + 1 year
URL_ENCODED_APOSTROPHE = "%26apos%3b"                # D-11 / NB03 7.3: decoded in geolocation_city_std only
MALFORMED_CITY_PATTERN = r"[\d/\\@,]|\s-\s"          # D-11 / NB03 7.3 (Python ``re`` semantics)
BRAZIL_UFS: frozenset[str] = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
})
OBSERVED_VOCABULARIES: Mapping[tuple[str, str], frozenset[str]] = MappingProxyType({  # NB03 7.1
    ("orders", "order_status"): frozenset({"approved", "canceled", "created", "delivered", "invoiced",
                                           "processing", "shipped", "unavailable"}),
    ("payments", "payment_type"): frozenset({"boleto", "credit_card", "debit_card", "not_defined", "voucher"}),
})

CITY_COLUMNS = MappingProxyType({"customers": "customer_city", "sellers": "seller_city",
                                 "geolocation": "geolocation_city"})
STATE_COLUMNS = MappingProxyType({"customers": "customer_state", "sellers": "seller_state",
                                  "geolocation": "geolocation_state"})
ZIP_KEY = "geolocation_zip_code_prefix"

PARAMETERS: Mapping[str, object] = MappingProxyType({
    "density_threshold_g_per_cm3": DENSITY_THRESHOLD_G_PER_CM3,
    "suspicious_dims_cm": list(SUSPICIOUS_DIMS_CM),
    "shipping_limit_max_offset_years": SHIPPING_LIMIT_MAX_OFFSET_YEARS,
    "url_encoded_apostrophe": URL_ENCODED_APOSTROPHE,
    "malformed_city_pattern": MALFORMED_CITY_PATTERN,
    "city_key_rule": "NFKD, drop non-ASCII, lower case, strip",
    "brazil_bbox": {k: list(v) for k, v in BRAZIL_BBOX.items()},
    "zip_centroid": "unweighted component-wise median over distinct in-Brazil (zip, lat, lng) points",
    "date_only_comparisons": "review_creation_date compared at day level",
})


# ----------------------------------------------------------------------------- added columns
@dataclass(frozen=True)
class ColumnSpec:
    """One column added by the clean layer, with its lineage."""

    dtype: str           # "bool", "string", "category", "float64", "Int64", "int64"
    sources: tuple[str, ...]
    rule: str
    decision: str        # strategy decision / section


def _c(dtype, sources, rule, decision):
    return ColumnSpec(dtype, tuple(sources), rule, decision)


_GEO = (
    ("lat_centroid", _c("float64", ["zip_lookup.lat_centroid"], "zip_lookup centroid via many_to_one join on zip prefix", "D-10.4")),
    ("lng_centroid", _c("float64", ["zip_lookup.lng_centroid"], "zip_lookup centroid via many_to_one join on zip prefix", "D-10.4")),
    ("dq_no_geolocation", _c("bool", ["zip_lookup"], "zip absent from zip_lookup, or zip has no valid point (centroid missing)", "D-10.4")),
)

_METADATA_FLAGS = tuple(
    (flag, _c("bool", ["products_metadata_flags." + flag],
              f"reused from staging products_metadata_flags ({'all fields of the group missing' if flag in ROW_FLAGS else (LISTING_FLAGS | PHYSICAL_FLAGS)[flag] + ' missing'})",
              "4.3"))
    for flag in (*LISTING_FLAGS, *PHYSICAL_FLAGS, *ROW_FLAGS)
)

ADDED_COLUMNS: Mapping[str, tuple[tuple[str, ColumnSpec], ...]] = MappingProxyType({
    "customers": (
        ("city_key", _c("string", ["customer_city"], "accent-free, lower case, trimmed", "D-11")),
        ("customer_state_std", _c("category", ["customer_state"], "upper case, trimmed; validated against the 27 UFs", "8.1")),
        *_GEO,
    ),
    "sellers": (
        ("city_key", _c("string", ["seller_city"], "accent-free, lower case, trimmed (malformed values not repaired)", "D-11")),
        ("seller_state_std", _c("category", ["seller_state"], "upper case, trimmed; validated against the 27 UFs", "8.1")),
        ("dq_city_malformed", _c("bool", ["seller_city"], f"seller_city matches {MALFORMED_CITY_PATTERN!r}", "D-11 / Q-12")),
        *_GEO,
    ),
    "geolocation": (
        ("geolocation_city_std", _c("string", ["geolocation_city"], f"'{URL_ENCODED_APOSTROPHE}' decoded to an apostrophe; otherwise unchanged", "8.1")),
        ("city_key", _c("string", ["geolocation_city"], "accent-free, lower case, trimmed (raw text, no decoding)", "D-11")),
        ("geolocation_state_std", _c("category", ["geolocation_state"], "upper case, trimmed; validated against the 27 UFs", "8.1")),
    ),
    "orders": (
        ("dq_delivered_missing_approval", _c("bool", ["order_status", "order_approved_at"], "status = delivered and approval missing", "D-06")),
        ("dq_delivered_missing_carrier_date", _c("bool", ["order_status", "order_delivered_carrier_date"], "status = delivered and carrier date missing", "D-06")),
        ("dq_delivered_missing_delivery_date", _c("bool", ["order_status", "order_delivered_customer_date"], "status = delivered and delivery date missing", "D-06")),
        ("dq_carrier_before_purchase", _c("bool", ["order_delivered_carrier_date", "order_purchase_timestamp"], "carrier date < purchase (both present)", "D-08")),
        ("dq_delivered_before_carrier", _c("bool", ["order_delivered_customer_date", "order_delivered_carrier_date"], "delivery date < carrier date (both present)", "D-08")),
        ("dq_carrier_before_approval", _c("bool", ["order_delivered_carrier_date", "order_approved_at"], "carrier date < approval (both present)", "D-08")),
        ("dq_delivered_before_approval", _c("bool", ["order_delivered_customer_date", "order_approved_at"], "delivery date < approval (both present)", "D-08")),
        ("dq_status_canceled_with_date", _c("bool", ["order_status", "order_delivered_customer_date"], "status = canceled and delivery date present", "D-08 / Q-01")),
        ("dq_same_person_same_second", _c("bool", ["customers.customer_unique_id", "order_purchase_timestamp"], "another order by the same customer_unique_id has the same purchase timestamp (all rows of the group)", "D-07 / Q-09")),
    ),
    "order_items": (
        ("dq_shipping_limit_out_of_range", _c("bool", ["shipping_limit_date", "orders.order_purchase_timestamp"], "shipping_limit_date > max(purchase) + 1 year", "D-08 / Q-15")),
    ),
    "payments": (
        ("dq_payment_zero", _c("bool", ["payment_value"], "payment_value = 0", "D-09")),
        ("dq_installments_zero", _c("bool", ["payment_installments"], "payment_installments = 0 (value kept)", "D-16 / Q-07")),
    ),
    "reviews": (
        ("has_comment_title", _c("bool", ["review_comment_title"], "title present", "4.2")),
        ("has_comment_message", _c("bool", ["review_comment_message"], "message present", "4.2")),
        ("dq_review_id_shared", _c("bool", ["review_id"], "review_id appears on more than one row (all rows)", "D-07 / Q-04")),
        ("dq_review_content_duplicate", _c("bool", ["order_id", "review_score", "review_comment_title", "review_comment_message", "review_creation_date"], "repeats an earlier row's order, score, title, message and creation date (first occurrence not flagged)", "D-07")),
        ("dq_review_before_purchase", _c("bool", ["review_creation_date", "orders.order_purchase_timestamp"], "creation date < purchase date (day level)", "D-08 / Q-15")),
        ("dq_review_before_delivery", _c("bool", ["review_creation_date", "orders.order_delivered_customer_date"], "creation date < delivery date (day level); informational", "D-08")),
    ),
    "products": (
        *_METADATA_FLAGS,
        ("dq_weight_zero", _c("bool", ["product_weight_g"], "product_weight_g = 0", "D-09")),
        ("weight_g_valid", _c("Int64", ["product_weight_g"], "product_weight_g, set to missing where dq_weight_zero (analysis column)", "D-09")),
        ("dq_density_suspicious", _c("bool", ["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"], f"weight / (length x height x width) > {DENSITY_THRESHOLD_G_PER_CM3} g/cm3; missing inputs -> False", "D-09 / Q-06")),
        ("dq_dims_16x2x11", _c("bool", ["product_length_cm", "product_height_cm", "product_width_cm"], "dimensions exactly 16 x 2 x 11 cm", "D-09 / Q-06")),
        ("category_en_clean", _c("string", ["product_category_name", "category_map.category_en_clean"], "category_map label via many_to_one join; missing where the category is missing", "D-12")),
        ("category_reporting", _c("string", ["product_category_name", "category_en_clean"], "category_en_clean; explicit unknown label where the raw category is missing", "D-12 / Q-05")),
    ),
    "translation": (),
})

REFERENCE_SCHEMAS: Mapping[str, tuple[tuple[str, str], ...]] = MappingProxyType({
    "category_map": (("product_category_name", "string"), ("category_en_source", "string"),
                     ("category_en_clean", "string"), ("is_added_by_project", "bool"), ("note", "string")),
    "zip_lookup": ((ZIP_KEY, "string"), ("lat_centroid", "float64"), ("lng_centroid", "float64"),
                   ("n_points_used", "int64"), ("n_points_excluded_outside_brazil", "int64"),
                   ("raw_rows_represented", "int64"), ("n_distinct_states", "int64"),
                   ("dq_zip_multi_state", "bool"), ("dq_zip_no_valid_point", "bool")),
})


def added_column_names(table: str) -> list[str]:
    return [name for name, _ in ADDED_COLUMNS[table]]


# ----------------------------------------------------------------------------- baselines (11.3)
BASELINE_CLEAN_FLAGS: Mapping[tuple[str, str], int] = MappingProxyType({  # strategy 11.3.1
    ("orders", "dq_carrier_before_purchase"): 166,
    ("orders", "dq_delivered_before_carrier"): 23,
    ("orders", "dq_carrier_before_approval"): 1_359,
    ("orders", "dq_delivered_before_approval"): 61,
    ("orders", "dq_delivered_missing_delivery_date"): 8,
    ("orders", "dq_delivered_missing_approval"): 14,
    ("orders", "dq_delivered_missing_carrier_date"): 2,
    ("orders", "dq_status_canceled_with_date"): 6,
    ("orders", "dq_same_person_same_second"): 582,
    ("order_items", "dq_shipping_limit_out_of_range"): 4,
    ("reviews", "dq_review_before_purchase"): 64,
    ("reviews", "dq_review_id_shared"): 1_603,
    ("reviews", "dq_review_content_duplicate"): 86,
    ("products", "dq_weight_zero"): 4,
    ("products", "dq_density_suspicious"): 159,
    ("products", "dq_dims_16x2x11"): 588,
    ("payments", "dq_installments_zero"): 2,
    ("payments", "dq_payment_zero"): 9,
    ("sellers", "dq_city_malformed"): 24,
    ("zip_lookup", "dq_zip_multi_state"): 8,
})

BASELINE_DOMAIN_RULES: Mapping[str, int] = MappingProxyType({  # strategy 11.3.2
    "price_le_0": 0,
    "freight_lt_0": 0,
    "freight_eq_0": 383,
    "payment_lt_0": 0,
    "payment_eq_0": 9,
    "installments_lt_1": 2,
    "installments_gt_1_non_credit_card": 0,
    "review_score_outside_1_5": 0,
    "weight_le_0": 4,
    "any_dimension_le_0": 0,
    "photos_lt_1": 0,
    "customers_state_not_uf": 0,
    "sellers_state_not_uf": 0,
    "geolocation_state_not_uf": 0,
    "order_status_outside_vocabulary": 0,
    "payment_type_outside_vocabulary": 0,
})

BASELINE_STRUCTURE: Mapping[str, int] = MappingProxyType({  # strategy 11.3.3, D-07, D-11, D-12
    "distinct_review_id": 98_410,
    "distinct_customer_unique_id": 96_096,
    "untranslated_products_before_map": 13,
    "untranslated_products_after_map": 0,
    "category_map_rows": 73,
    "missing_category_products": 610,
    "zip_lookup_rows": 19_015,
    "customers_zip_absent": 278,
    "sellers_zip_absent": 7,
    "city_key_distinct_customers": 4_119,
    "city_key_distinct_sellers": 609,
    "city_key_distinct_geolocation": 5_966,
})
