"""Declared schema of the nine raw Olist CSV files.

The schema is the *ingestion contract*: the exact header each file must have, the dtype
used to read every column, and the SHA-256 checksum of the approved raw file.

Loading rules (see reports/data_preparation_strategy.md, sections 2-3):

* Identifiers, zip-code prefixes, free text and date/time columns are read as
  ``string``. Zip prefixes in particular are 5-character codes whose leading zeros are
  lost if pandas infers them as integers (strategy D-04).
* Date/time columns are deliberately kept as text here. Parsing them is a *staging*
  step (strategy D-03), not part of ingestion.
* Whole-number columns use the nullable ``Int64`` dtype, so a missing value does not
  force the column to float. Decimal amounts and coordinates use ``float64``.
* Column names are kept exactly as in the source, including the misspelled
  ``*_lenght`` columns. Renaming them is a staging step (strategy D-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

STRING = "string"
INT = "Int64"
FLOAT = "float64"


@dataclass(frozen=True)
class DatasetSpec:
    """Ingestion contract for one raw CSV file."""

    name: str
    filename: str
    columns: Mapping[str, str]  # column name (in source order) -> pandas dtype
    sha256: str  # checksum of the approved raw file (baseline from notebook 03, section 11)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(self.columns)


def _spec(name: str, filename: str, sha256: str, columns: list[tuple[str, str]]) -> DatasetSpec:
    return DatasetSpec(name=name, filename=filename, sha256=sha256,
                       columns=MappingProxyType(dict(columns)))


DATASETS: tuple[DatasetSpec, ...] = (
    _spec("customers", "olist_customers_dataset.csv",
          "983a422239e1712ded753b3bf9ecf47dc73f144d306029dcfa99e70a226883d2", [
              ("customer_id", STRING),
              ("customer_unique_id", STRING),
              ("customer_zip_code_prefix", STRING),
              ("customer_city", STRING),
              ("customer_state", STRING),
          ]),
    _spec("geolocation", "olist_geolocation_dataset.csv",
          "b514f6fc991b9566aeba02aa5d67e2c3630f034b60a0e05aa0d082a3b66d88d6", [
              ("geolocation_zip_code_prefix", STRING),
              ("geolocation_lat", FLOAT),
              ("geolocation_lng", FLOAT),
              ("geolocation_city", STRING),
              ("geolocation_state", STRING),
          ]),
    _spec("orders", "olist_orders_dataset.csv",
          "8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf", [
              ("order_id", STRING),
              ("customer_id", STRING),
              ("order_status", STRING),
              ("order_purchase_timestamp", STRING),
              ("order_approved_at", STRING),
              ("order_delivered_carrier_date", STRING),
              ("order_delivered_customer_date", STRING),
              ("order_estimated_delivery_date", STRING),
          ]),
    _spec("order_items", "olist_order_items_dataset.csv",
          "0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279", [
              ("order_id", STRING),
              ("order_item_id", INT),
              ("product_id", STRING),
              ("seller_id", STRING),
              ("shipping_limit_date", STRING),
              ("price", FLOAT),
              ("freight_value", FLOAT),
          ]),
    _spec("payments", "olist_order_payments_dataset.csv",
          "4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0", [
              ("order_id", STRING),
              ("payment_sequential", INT),
              ("payment_type", STRING),
              ("payment_installments", INT),
              ("payment_value", FLOAT),
          ]),
    _spec("reviews", "olist_order_reviews_dataset.csv",
          "012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7", [
              ("review_id", STRING),
              ("order_id", STRING),
              ("review_score", INT),
              ("review_comment_title", STRING),
              ("review_comment_message", STRING),
              ("review_creation_date", STRING),
              ("review_answer_timestamp", STRING),
          ]),
    _spec("products", "olist_products_dataset.csv",
          "3e6569628a17fbc75fd206ee357b59e20364b9afa90f5b6cd5b4d624c58aa9cc", [
              ("product_id", STRING),
              ("product_category_name", STRING),
              ("product_name_lenght", INT),          # source spelling kept (renamed at staging)
              ("product_description_lenght", INT),   # source spelling kept (renamed at staging)
              ("product_photos_qty", INT),
              ("product_weight_g", INT),
              ("product_length_cm", INT),
              ("product_height_cm", INT),
              ("product_width_cm", INT),
          ]),
    _spec("sellers", "olist_sellers_dataset.csv",
          "1f643d2b950373b85735e7794b20986f528d7a000432e7c6f9bcbb44d0846a0e", [
              ("seller_id", STRING),
              ("seller_zip_code_prefix", STRING),
              ("seller_city", STRING),
              ("seller_state", STRING),
          ]),
    _spec("translation", "product_category_name_translation.csv",
          "a81f0d1f27b27e7293f761bc79e3ce8f348ee39c4b3ed3e49bde38f478586278", [
              ("product_category_name", STRING),
              ("product_category_name_english", STRING),
          ]),
)

DATASETS_BY_NAME: Mapping[str, DatasetSpec] = MappingProxyType({d.name: d for d in DATASETS})

ZIP_COLUMNS: Mapping[str, str] = MappingProxyType({
    "customers": "customer_zip_code_prefix",
    "sellers": "seller_zip_code_prefix",
    "geolocation": "geolocation_zip_code_prefix",
})
