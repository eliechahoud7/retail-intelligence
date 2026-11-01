"""Product metadata missingness flags, exactly as defined in strategy section 4.3.

Field-level flags: one per column, True when that value is missing.
Row-level flags:   True when ALL fields of the group are missing.
"""

from __future__ import annotations

import pandas as pd

LISTING_FLAGS = {  # flag -> staged column
    "dq_missing_category": "product_category_name",
    "dq_missing_name_length": "product_name_length",
    "dq_missing_description_length": "product_description_length",
    "dq_missing_photos_qty": "product_photos_qty",
}
PHYSICAL_FLAGS = {
    "dq_missing_weight_g": "product_weight_g",
    "dq_missing_length_cm": "product_length_cm",
    "dq_missing_height_cm": "product_height_cm",
    "dq_missing_width_cm": "product_width_cm",
}
ROW_FLAGS = ("dq_listing_metadata_missing", "dq_physical_attributes_missing", "dq_all_attributes_missing")


def build_product_metadata_flags(products: pd.DataFrame) -> pd.DataFrame:
    """Return ``product_id`` plus 8 field-level and 3 row-level boolean flags."""
    out = pd.DataFrame({"product_id": products["product_id"]})
    for flag, col in {**LISTING_FLAGS, **PHYSICAL_FLAGS}.items():
        out[flag] = products[col].isna().astype(bool)
    listing = out[list(LISTING_FLAGS)]
    physical = out[list(PHYSICAL_FLAGS)]
    out["dq_listing_metadata_missing"] = listing.all(axis=1)
    out["dq_physical_attributes_missing"] = physical.all(axis=1)
    out["dq_all_attributes_missing"] = listing.all(axis=1) & physical.all(axis=1)
    return out


def group_equivalence(flags: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """For each field group: (products with ANY field missing, products with ALL missing).

    Strategy 4.3 requires the two counts to be equal; a difference means a new
    partial-missingness pattern has appeared.
    """
    return {
        "listing": (int(flags[list(LISTING_FLAGS)].any(axis=1).sum()),
                    int(flags["dq_listing_metadata_missing"].sum())),
        "physical": (int(flags[list(PHYSICAL_FLAGS)].any(axis=1).sum()),
                     int(flags["dq_physical_attributes_missing"].sum())),
    }
