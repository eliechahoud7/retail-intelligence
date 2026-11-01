"""Clean-layer reference tables: ``category_map`` (D-12) and ``zip_lookup`` (D-10).

``category_map`` is a project-owned table. Its overrides live here, under a version number
with change notes, so every label decision is reviewable in one place and the raw
translation file stays immutable.

``zip_lookup`` is built from the staging output ``geolocation_points``; each distinct
in-Brazil (zip, lat, lng) point counts once, whatever its raw occurrence count.
"""

from __future__ import annotations

from types import MappingProxyType

import pandas as pd

from src.staging import geolocation as geo

from .schema import REFERENCE_SCHEMAS, ZIP_KEY

# ----------------------------------------------------------------------------- category_map
CATEGORY_MAP_VERSION = "1.0.0"
CATEGORY_MAP_CHANGE_NOTES = (
    "1.0.0 (Task 4.3): initial table. 4 English typo corrections; 2 translations added by the "
    "project (provisional wording, pending Q-05 sign-off); '_2' twins kept separate (Q-05); "
    "products without a category labelled 'uncategorized' in the reporting column only (provisional, Q-05).",
)

# Portuguese category -> (English name expected in the source translation, corrected English name).
# The expected source value guards against silently re-labelling a changed source row.
ENGLISH_TYPO_CORRECTIONS = MappingProxyType({
    "construcao_ferramentas_jardim": ("costruction_tools_garden", "construction_tools_garden"),
    "construcao_ferramentas_ferramentas": ("costruction_tools_tools", "construction_tools_tools"),
    "fashion_roupa_feminina": ("fashio_female_clothing", "fashion_female_clothing"),
    "casa_conforto": ("home_confort", "home_comfort"),
})

# Portuguese categories absent from the source translation (NB02 3.2). Provisional wording.
PROJECT_TRANSLATIONS = MappingProxyType({
    "pc_gamer": "pc_gamer",
    "portateis_cozinha_e_preparadores_de_alimentos": "portable_kitchen_and_food_preparers",
})

TWIN_CATEGORIES = frozenset({"casa_conforto_2", "eletrodomesticos_2"})  # kept separate (Q-05)

UNCATEGORISED_LABEL = "uncategorized"  # reporting label for products with no category (provisional, Q-05)

NOTE_TYPO = "English typo corrected by the project ({src} -> {clean})"
NOTE_ADDED = "translation added by the project; provisional wording pending Q-05 sign-off"
NOTE_TWIN = "'_2' twin kept as a separate category pending Q-05"


class CategoryMapError(ValueError):
    """A project override no longer matches the source translation and must be reviewed."""


def build_category_map(products: pd.DataFrame, translation: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return (category_map, notes on overrides that did not apply to this input).

    One row per Portuguese category found in the translation table or in products.
    Raises ``CategoryMapError`` when an override conflicts with the source: a typo
    correction whose source English value differs from the expected one, or a project
    translation for a category that the source now translates itself.
    """
    src = dict(zip(translation["product_category_name"], translation["product_category_name_english"]))
    not_applied: list[str] = []

    for pt, (expected, _) in ENGLISH_TYPO_CORRECTIONS.items():
        if pt not in src:
            not_applied.append(f"typo correction for '{pt}': category absent from translation")
        elif src[pt] != expected:
            raise CategoryMapError(f"typo correction for '{pt}' expects source '{expected}', found '{src[pt]}'")
    clash = sorted(set(PROJECT_TRANSLATIONS) & set(src))
    if clash:
        raise CategoryMapError(f"project translations now present in the source translation: {clash}")

    used = set(products["product_category_name"].dropna())
    categories = sorted(set(src) | used | set(PROJECT_TRANSLATIONS))
    rows = []
    for pt in categories:
        en_src = src.get(pt)
        clean, added, notes = en_src, False, []
        if pt in ENGLISH_TYPO_CORRECTIONS and en_src is not None:
            clean = ENGLISH_TYPO_CORRECTIONS[pt][1]
            notes.append(NOTE_TYPO.format(src=en_src, clean=clean))
        if pt in PROJECT_TRANSLATIONS:
            clean, added = PROJECT_TRANSLATIONS[pt], True
            notes.append(NOTE_ADDED)
        if pt in TWIN_CATEGORIES:
            notes.append(NOTE_TWIN)
        rows.append((pt, en_src, clean, added, "; ".join(notes) or None))

    cols = [c for c, _ in REFERENCE_SCHEMAS["category_map"]]
    out = pd.DataFrame(rows, columns=cols)
    return out.astype(dict(REFERENCE_SCHEMAS["category_map"])), not_applied


# ----------------------------------------------------------------------------- zip_lookup
def build_zip_lookup(points: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """One row per zip prefix in ``geolocation_points`` (strategy D-10.3).

    * centroids: component-wise median of lat and lng over the zip's distinct points with
      ``dq_outside_brazil`` False, each point counted once (unweighted);
    * ``n_distinct_states`` / ``dq_zip_multi_state``: distinct ``geolocation_state`` over all
      of the zip's ``geolocation_rows`` (the NB02 5.3 definition);
    * ``dq_zip_no_valid_point``: no valid point, so both centroids are missing.
    """
    valid = points[~points["dq_outside_brazil"]]
    zips = pd.Index(sorted(points[geo.ZIP].unique()), name=ZIP_KEY)
    agg = (valid.groupby(geo.ZIP, sort=True)
           .agg(lat_centroid=(geo.LAT, "median"), lng_centroid=(geo.LNG, "median"),
                n_points_used=(geo.LAT, "size"), raw_rows_represented=("raw_row_count", "sum"))
           .reindex(zips))
    excluded = points[points["dq_outside_brazil"]].groupby(geo.ZIP).size().reindex(zips, fill_value=0)
    states = rows.groupby(geo.ZIP, observed=True)[geo.STATE].nunique().reindex(zips, fill_value=0)

    out = pd.DataFrame(index=zips)
    out["lat_centroid"] = agg["lat_centroid"].astype("float64")
    out["lng_centroid"] = agg["lng_centroid"].astype("float64")
    out["n_points_used"] = agg["n_points_used"].fillna(0).astype("int64")
    out["n_points_excluded_outside_brazil"] = excluded.astype("int64")
    out["raw_rows_represented"] = agg["raw_rows_represented"].fillna(0).astype("int64")
    out["n_distinct_states"] = states.astype("int64")
    out["dq_zip_multi_state"] = (out["n_distinct_states"] > 1).astype(bool)
    out["dq_zip_no_valid_point"] = (out["n_points_used"] == 0).astype(bool)
    out = out.reset_index()
    out[ZIP_KEY] = out[ZIP_KEY].astype("string")
    return out[[c for c, _ in REFERENCE_SCHEMAS["zip_lookup"]]]
