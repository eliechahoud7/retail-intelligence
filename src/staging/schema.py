"""Staging-layer contract: approved structural transformations and documented baselines.

Every transformation here is approved in reports/data_preparation_strategy.md:

* D-03: parse the 8 date/time columns with one explicit format, timezone-naive.
* D-04: zip-code prefixes stay 5-character strings (already read as text by ingestion).
* D-05: rename the two misspelled product columns; nothing else is renamed.
* Section 3.3: identifiers stay ``string``; counts ``Int64``; money and coordinates
  ``float64``; closed vocabularies (order_status, payment_type, *_state) ``category``.

Baselines are the values measured in notebooks 01-03. They are checked as *regression*
checks, and only when the raw files match the approved checksums.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

DATE_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "orders": ("order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date"),
    "order_items": ("shipping_limit_date",),
    "reviews": ("review_creation_date", "review_answer_timestamp"),
})

COLUMN_RENAMES: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "products": MappingProxyType({
        "product_name_lenght": "product_name_length",
        "product_description_lenght": "product_description_length",
    }),
})

CATEGORICAL_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "customers": ("customer_state",),
    "geolocation": ("geolocation_state",),
    "orders": ("order_status",),
    "payments": ("payment_type",),
    "sellers": ("seller_state",),
})

# Primary keys validated in notebook 02 (geolocation has none).
PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "customers": ("customer_id",),
    "orders": ("order_id",),
    "order_items": ("order_id", "order_item_id"),
    "payments": ("order_id", "payment_sequential"),
    "reviews": ("review_id", "order_id"),
    "products": ("product_id",),
    "sellers": ("seller_id",),
    "translation": ("product_category_name",),
})

# Core foreign keys with 0 orphans in notebook 02: (child, child_col, parent, parent_col).
CORE_FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("payments", "order_id", "orders", "order_id"),
    ("reviews", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("order_items", "seller_id", "sellers", "seller_id"),
)

# Approximate national extent used in notebook 03 section 6.5 and strategy D-10.
BRAZIL_BBOX = MappingProxyType({"lat": (-33.75, 5.27), "lng": (-73.99, -34.79)})

# ----------------------------------------------------------------------------- baselines
BASELINE_ROWS = MappingProxyType({  # notebook 01
    "customers": 99_441, "geolocation": 1_000_163, "orders": 99_441, "order_items": 112_650,
    "payments": 103_886, "reviews": 99_224, "products": 32_951, "sellers": 3_095, "translation": 71,
})

BASELINE_DATES = MappingProxyType({  # notebook 03 section 4.1: (non-missing, min, max)
    ("orders", "order_purchase_timestamp"): (99_441, "2016-09-04 21:15:19", "2018-10-17 17:30:18"),
    ("orders", "order_approved_at"): (99_281, "2016-09-15 12:16:38", "2018-09-03 17:40:06"),
    ("orders", "order_delivered_carrier_date"): (97_658, "2016-10-08 10:34:01", "2018-09-11 19:48:28"),
    ("orders", "order_delivered_customer_date"): (96_476, "2016-10-11 13:46:32", "2018-10-17 13:22:46"),
    ("orders", "order_estimated_delivery_date"): (99_441, "2016-09-30 00:00:00", "2018-11-12 00:00:00"),
    ("order_items", "shipping_limit_date"): (112_650, "2016-09-19 00:15:34", "2020-04-09 22:35:08"),
    ("reviews", "review_creation_date"): (99_224, "2016-10-02 00:00:00", "2018-08-31 00:00:00"),
    ("reviews", "review_answer_timestamp"): (99_224, "2016-10-07 18:32:28", "2018-10-29 12:27:35"),
})

BASELINE_LEADING_ZERO_ZIPS = MappingProxyType({  # notebook 03 section 4.3
    "customers": 23_995, "sellers": 1_027, "geolocation": 245_733,
})

BASELINE_MONETARY_TOTALS = MappingProxyType({  # notebooks 02 section 6.2 / 03 section 6.2
    ("order_items", "price"): 13_591_643.70,
    ("order_items", "freight_value"): 2_251_909.54,
    ("payments", "payment_value"): 16_008_872.12,
})

BASELINE_GEOLOCATION = MappingProxyType({  # notebooks 02 section 2.2/5.3, 03 section 6.5
    "geolocation_rows": 738_332,
    "geolocation_points": 720_154,
    "zip_prefixes": 19_015,
    "raw_rows_outside_brazil": 42,
})

BASELINE_UNTRANSLATED_PRODUCTS = 13  # notebook 02 section 3.2

BASELINE_PRODUCT_FLAGS = MappingProxyType({  # strategy section 4.3 (from notebook 03 section 2)
    "dq_missing_category": 610,
    "dq_missing_name_length": 610,
    "dq_missing_description_length": 610,
    "dq_missing_photos_qty": 610,
    "dq_missing_weight_g": 2,
    "dq_missing_length_cm": 2,
    "dq_missing_height_cm": 2,
    "dq_missing_width_cm": 2,
    "dq_listing_metadata_missing": 610,
    "dq_physical_attributes_missing": 2,
    "dq_all_attributes_missing": 1,
})
