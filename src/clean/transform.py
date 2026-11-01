"""Assemble the clean layer from staged frames (strategy section 13, steps 4-9).

A clean source table = its staged table (same rows, same order, source columns untouched)
with the columns declared in ``schema.ADDED_COLUMNS`` appended. Nothing is filtered,
de-duplicated, imputed or aggregated; the only joins are many_to_one lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import flags as fl
from . import standardise as sd
from .reference import UNCATEGORISED_LABEL, build_category_map, build_zip_lookup
from .schema import ADDED_COLUMNS, CITY_COLUMNS, STATE_COLUMNS, added_column_names


@dataclass
class CleanBuild:
    frames: dict[str, pd.DataFrame]
    steps: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # overrides not applicable to this input, etc.


def extend(base: pd.DataFrame, table: str, added: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``base`` with the declared added columns appended in declared order."""
    declared = added_column_names(table)
    if sorted(added.columns) != sorted(declared):
        raise ValueError(f"{table}: added columns {sorted(added.columns)} differ from the declared {sorted(declared)}")
    if not added.index.equals(base.index):
        raise ValueError(f"{table}: added columns are not aligned with the staged rows")
    clash = set(declared) & set(base.columns)
    if clash:
        raise ValueError(f"{table}: added columns would overwrite source columns {sorted(clash)}")
    return pd.concat([base.copy(), added[declared]], axis=1)


def _standardised_location(df: pd.DataFrame, table: str) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["city_key"] = sd.city_key(df[CITY_COLUMNS[table]])
    out[f"{STATE_COLUMNS[table]}_std"] = sd.state_std(df[STATE_COLUMNS[table]])
    return out


def build_clean(staged: dict[str, pd.DataFrame]) -> CleanBuild:
    """Build every clean output from the staged frames (inputs are not mutated)."""
    s = staged
    build = CleanBuild(frames={})
    frames = build.frames

    def step(name, decision, inputs, output):
        build.steps.append({"step": name, "decision": decision,
                            "inputs": {k: len(s[k]) for k in inputs}, "output": output, "rows_out": len(frames[output])})

    # Step 5 - reference tables
    frames["zip_lookup"] = build_zip_lookup(s["geolocation_points"], s["geolocation_rows"])
    step("zip_lookup from geolocation_points (unweighted median of distinct in-Brazil points)", "D-10",
         ["geolocation_points", "geolocation_rows"], "zip_lookup")
    frames["category_map"], not_applied = build_category_map(s["products"], s["translation"])
    build.notes += not_applied
    step("category_map (source translation + project overrides)", "D-12", ["translation", "products"], "category_map")

    # Step 4 - categorical standardisation (+ D-10.4 centroid lookup)
    for table, zip_col in (("customers", "customer_zip_code_prefix"), ("sellers", "seller_zip_code_prefix")):
        df = s[table]
        added = _standardised_location(df, table)
        if table == "sellers":
            added["dq_city_malformed"] = sd.city_malformed(df["seller_city"])
        added = pd.concat([added, sd.attach_geolocation(df, zip_col, frames["zip_lookup"])], axis=1)
        frames[table] = extend(df, table, added)
        step(f"{table}: city_key, state_std, centroid lookup", "D-11 / D-10.4", [table], table)

    geo = s["geolocation"]
    added = _standardised_location(geo, "geolocation")
    added.insert(0, "geolocation_city_std", sd.decode_url_apostrophe(geo["geolocation_city"]))
    frames["geolocation"] = extend(geo, "geolocation", added)
    step("geolocation: geolocation_city_std, city_key, state_std", "D-11", ["geolocation"], "geolocation")

    # Steps 6-9 - missing-value gaps, chronology/status, numerical and duplicate flags
    frames["orders"] = extend(s["orders"], "orders", fl.order_flags(s["orders"], s["customers"]))
    step("orders: delivered-gap, chronology, status and same-second flags", "D-06 / D-07 / D-08",
         ["orders", "customers"], "orders")
    frames["order_items"] = extend(s["order_items"], "order_items", fl.order_item_flags(s["order_items"], s["orders"]))
    step("order_items: shipping-limit range flag (source grain kept)", "D-08", ["order_items", "orders"], "order_items")
    frames["payments"] = extend(s["payments"], "payments", fl.payment_flags(s["payments"]))
    step("payments: zero-value and zero-instalment flags", "D-09 / D-16", ["payments"], "payments")
    frames["reviews"] = extend(s["reviews"], "reviews", fl.review_flags(s["reviews"], s["orders"]))
    step("reviews: comment presence, duplicate and timing flags", "4.2 / D-07 / D-08", ["reviews", "orders"], "reviews")

    prod = s["products"]
    added = pd.concat([fl.product_flags(prod, s["products_metadata_flags"]),
                       fl.product_categories(prod, frames["category_map"], UNCATEGORISED_LABEL)], axis=1)
    frames["products"] = extend(prod, "products", added)
    step("products: staging metadata flags (reused), D-09 flags, category_map labels", "4.3 / D-09 / D-12",
         ["products", "products_metadata_flags"], "products")

    frames["translation"] = extend(s["translation"], "translation", pd.DataFrame(index=s["translation"].index))
    step("translation: unchanged (corrections live in category_map)", "D-12", ["translation"], "translation")
    return build


def transformations(table: str) -> list[str]:
    """Human-readable description of what the clean layer added to ``table``."""
    if table == "translation":
        return ["no change (label corrections live in category_map, D-12)"]
    return [f"+ {name} [{spec.dtype}] <- {', '.join(spec.sources)}: {spec.rule} ({spec.decision})"
            for name, spec in ADDED_COLUMNS[table]]
