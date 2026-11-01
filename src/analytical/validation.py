"""Analytical-layer validation (strategy 11.2 V09-V13, 11.3, 11.4 W01/W04/W05).

Reuses the staging check classes. Monetary sums are compared after rounding to cents
(strategy 3.3: rounding is used for comparisons only).
"""

from __future__ import annotations

import pandas as pd

from src.staging.validation import BLOCKING, REGRESSION, WARNING, CheckResult
from src.staging.validation import _check as check

from .facts import delivered_mask
from .schema import (BASELINE_ANALYTICAL, BASELINE_MAX_QUANTITY, BASELINE_MISMATCH_RANGE,
                     BASELINE_ORDERS_WITHOUT_ITEMS_BY_STATUS, FACTS, MONEY_DECIMALS, NON_NEGATIVE_DURATIONS,
                     PAYMENT_MISMATCH_TOLERANCE, SIX_ATTRIBUTE_GRAIN, TABLES)


def _money(x) -> float:
    return round(float(x), MONEY_DECIMALS)


def _dtype_ok(actual, expected: str) -> bool:
    return isinstance(actual, pd.CategoricalDtype) if expected == "category" else str(actual) == expected


# ----------------------------------------------------------------------------- lineage
def lineage_checks(inputs: dict, raw_checksums: dict[str, str], filenames: dict[str, str]) -> list[CheckResult]:
    by_file = {filenames[n]: sha for n, sha in raw_checksums.items()}
    out = []
    for name, inp in inputs.items():
        rec = inp.lineage.get("raw_sha256", {})
        stale = sorted(f for f, sha in rec.items() if by_file.get(f) != sha)
        ok = bool(rec) and not stale and inp.lineage.get("layer") == "clean" and inp.lineage.get("dataset") == name
        out.append(check(f"C01.{name}", BLOCKING, f"clean {name}: lineage present and its raw SHA-256 equals the current raw file",
                         [], stale if rec else ["<no lineage>"], passed=ok))
    out.append(check("C02", BLOCKING, "all clean inputs come from one clean run", 1,
                     len({i.lineage.get("run_id") for i in inputs.values()})))
    return out


# ----------------------------------------------------------------------------- blocking
def schema_and_key_checks(frames) -> list[CheckResult]:
    out = []
    for name, spec in TABLES.items():
        df = frames[name]
        cols_ok = list(df.columns) == [c for c, _ in spec.columns]
        bad = [c for c, d in spec.columns if not _dtype_ok(df[c].dtype, d)] if cols_ok else ["<columns differ>"]
        out.append(check(f"V03.{name}", BLOCKING, f"{name}: columns and dtypes match the declared schema", [], bad,
                         passed=cols_ok and not bad))
        pk = list(spec.primary_key)
        out.append(check(f"V10.{name}", BLOCKING, f"{name}: grain key {tuple(pk)} unique and non-null", (0, 0),
                         (int(df.duplicated(subset=pk).sum()), int(df[pk].isna().any(axis=1).sum()))))
        for col, parent, pcol in spec.foreign_keys:
            c, p = df[col], frames[parent][pcol]
            out.append(check(f"FK.{name}.{col}", BLOCKING, f"{name}.{col} -> {parent}.{pcol}: no orphans (nulls count as orphans)",
                             0, int((c.isna() | ~c.isin(p)).sum())))
        bools = [c for c, d in spec.columns if d == "bool"]
        out.append(check(f"F01.{name}", BLOCKING, f"{name}: boolean flag columns have no missing values", [],
                         [c for c in bools if df[c].isna().any()]))
    return out


def row_and_source_checks(clean, frames) -> list[CheckResult]:
    """Facts/dims at a source grain keep every source row and every carried source value."""
    out = []
    pairs = {"fact_orders": "orders", "fact_payments": "payments", "fact_reviews": "reviews",
             "dim_products": "products", "dim_sellers": "sellers"}
    for name, src in pairs.items():
        df, s = frames[name], clean[src]
        out.append(check(f"ROWS.{name}", BLOCKING, f"{name}: rows = clean {src} rows", len(s), len(df)))
        shared = [c for c in df.columns if c in s.columns]
        changed = [c for c in shared if len(df) != len(s)
                   or not df[c].reset_index(drop=True).equals(s[c].reset_index(drop=True))]
        out.append(check(f"A14.{name}", BLOCKING, f"{name}: every column carried from clean {src} is identical (values, order)",
                         [], changed))
    persons = clean["customers"]["customer_unique_id"].nunique()
    out.append(check("ROWS.dim_customers", BLOCKING, "dim_customers: one row per distinct customer_unique_id", persons,
                     len(frames["dim_customers"])))
    d = frames["dim_dates"]["date"]
    out.append(check("ROWS.dim_dates", BLOCKING, "dim_dates: contiguous days, no gaps", len(d),
                     int((d.max() - d.min()).days) + 1))
    return out


def order_line_checks(clean, frames) -> list[CheckResult]:
    """V13: the six-attribute aggregation is lossless and every item is in exactly one line."""
    items, lines = clean["order_items"], frames["fact_order_lines"]
    exploded = lines[["order_line_id", "order_id", "product_id", "seller_id", "unit_price", "freight_value_per_row",
                      "shipping_limit_date", "order_item_ids"]].explode("order_item_ids")
    exploded["order_item_id"] = exploded["order_item_ids"].astype("Int64")
    dup = int(exploded.duplicated(["order_id", "order_item_id"]).sum())
    back = exploded.merge(items, on=["order_id", "order_item_id"], how="outer", indicator=True, suffixes=("_line", ""))
    unmatched = int((back["_merge"] != "both").sum())
    both = back[back["_merge"] == "both"]
    attr_diff = int((~((both["product_id_line"] == both["product_id"]) & (both["seller_id_line"] == both["seller_id"])
                       & (both["unit_price"] == both["price"]) & (both["freight_value_per_row"] == both["freight_value"])
                       & (both["shipping_limit_date_line"] == both["shipping_limit_date"]))).sum())
    per_line = both.groupby("order_line_id")[["price", "freight_value"]].sum().reindex(lines["order_line_id"])
    price_diff = int((per_line["price"].round(2).to_numpy() != lines["line_price_total"].round(2).to_numpy()).sum())
    freight_diff = int((per_line["freight_value"].round(2).to_numpy() != lines["freight_total"].round(2).to_numpy()).sum())
    qty_vs_ids = int((lines["order_item_ids"].map(len) != lines["quantity"]).sum())
    return [
        check("V13.quantity_sum", BLOCKING, "sum(quantity) = order_items rows", len(items), int(lines["quantity"].sum())),
        check("V13.each_item_once", BLOCKING,
              "every (order_id, order_item_id) is in exactly one line (duplicates, unmatched either side)", (0, 0),
              (dup, unmatched)),
        check("V13.lossless_attributes", BLOCKING,
              "every item's six attributes equal those of its line (nothing with different freight/deadline merged)",
              0, attr_diff),
        check("V13.line_totals", BLOCKING, "per line: line_price_total and freight_total = sums of their items (cents)",
              (0, 0), (price_diff, freight_diff)),
        check("V13.ids_match_quantity", BLOCKING, "len(order_item_ids) = quantity on every line", 0, qty_vs_ids),
    ]


def financial_checks(clean, frames) -> list[CheckResult]:
    """V09: monetary totals of every fact and of dim_customers equal the clean sums (cents)."""
    price, freight = _money(clean["order_items"]["price"].sum()), _money(clean["order_items"]["freight_value"].sum())
    pay = _money(clean["payments"]["payment_value"].sum())
    fl, fo, fp, dc = frames["fact_order_lines"], frames["fact_orders"], frames["fact_payments"], frames["dim_customers"]
    pairs = [
        ("fact_order_lines.line_price_total", price, fl["line_price_total"].sum()),
        ("fact_order_lines.freight_total", freight, fl["freight_total"].sum()),
        ("fact_orders.items_price_total", price, fo["items_price_total"].sum()),
        ("fact_orders.items_freight_total", freight, fo["items_freight_total"].sum()),
        ("fact_orders.payment_total", pay, fo["payment_total"].sum()),
        ("fact_payments.payment_value", pay, fp["payment_value"].sum()),
        ("dim_customers.items_price_total", price, dc["items_price_total"].sum()),
        ("dim_customers.items_freight_total", freight, dc["items_freight_total"].sum()),
        ("dim_customers.payment_total", pay, dc["payment_total"].sum()),
    ]
    out = [check(f"V09.{n}", BLOCKING, f"{n}: total = clean total (same run, cents)", e, _money(o)) for n, e, o in pairs]
    deliv = delivered_mask(fo)
    out.append(check("V09.dim_customers.delivered_variant", BLOCKING,
                     "dim_customers delivered-variant totals = fact_orders totals over delivered orders",
                     tuple(_money(fo.loc[deliv, c].sum()) for c in ("items_price_total", "items_freight_total", "payment_total")),
                     tuple(_money(dc[f"{c}_delivered"].sum()) for c in ("items_price_total", "items_freight_total", "payment_total"))))
    return out


def summary_checks(clean, frames) -> list[CheckResult]:
    fo, fl, dc = frames["fact_orders"], frames["fact_order_lines"], frames["dim_customers"]
    per = fl.groupby("order_id").agg(q=("quantity", "sum"), n=("order_line_id", "size")).reindex(fo["order_id"]).fillna(0)
    has_both = fo["has_items"] & fo["has_payment"]
    diff_expected = (fo["payment_minus_items"].abs() > PAYMENT_MISMATCH_TOLERANCE).fillna(False)
    flagged = [c for c in NON_NEGATIVE_DURATIONS if c not in ("approval_hours", "purchase_to_delivery_days")]
    flag_of = {"purchase_to_carrier_days": "dq_carrier_before_purchase", "approval_to_carrier_days": "dq_carrier_before_approval",
               "carrier_to_customer_days": "dq_delivered_before_carrier", "approval_to_delivery_days": "dq_delivered_before_approval"}
    return [
        check("S.item_count", BLOCKING, "fact_orders.item_count / order_line_count = quantity sum / line count per order",
              (0, 0), (int((fo["item_count"].to_numpy() != per["q"].to_numpy()).sum()),
                       int((fo["order_line_count"].to_numpy() != per["n"].to_numpy()).sum()))),
        check("S.row_counts", BLOCKING, "sums of item_count / payment_rows / review_count = clean rows",
              (len(clean["order_items"]), len(clean["payments"]), len(clean["reviews"])),
              (int(fo["item_count"].sum()), int(fo["payment_rows"].sum()), int(fo["review_count"].sum()))),
        check("S.payment_minus_items_defined", BLOCKING, "payment_minus_items present exactly for orders with items and payments",
              True, bool((fo["payment_minus_items"].notna() == has_both).all())),
        check("S.mismatch_flag", BLOCKING, "dq_payment_items_mismatch = |payment_minus_items| > tolerance", True,
              bool((fo["dq_payment_items_mismatch"] == diff_expected).all())),
        check("S.duration_flags", BLOCKING, "each guarded duration is missing wherever its chronology flag is True", 0,
              int(sum(fo.loc[fo[flag_of[d]], d].notna().sum() for d in flagged))),
        check("S.duration_non_negative", BLOCKING, "guarded durations are never negative where defined", 0,
              int(sum((fo[d] < 0).sum() for d in flagged))),
        check("S.is_late_defined", BLOCKING, "is_late is missing exactly where the delivery date is missing", True,
              bool((fo["is_late"].isna() == fo["order_delivered_customer_date"].isna()).all())),
        check("S.customer_order_seq", BLOCKING, "one first order per person; max sequence = n_orders",
              (len(dc), True), (int(fo["is_first_order"].sum()),
                                bool((fo.groupby("customer_unique_id")["customer_order_seq"].max()
                                      .reindex(dc["customer_unique_id"]).to_numpy() == dc["n_orders"].to_numpy()).all()))),
        check("S.dim_customers_counts", BLOCKING, "dim_customers: sum(n_orders) = orders, sum(n_items) = order_items rows",
              (len(clean["orders"]), len(clean["order_items"])), (int(dc["n_orders"].sum()), int(dc["n_items"].sum()))),
    ]


def blocking_checks(clean, frames, joins) -> list[CheckResult]:
    return (schema_and_key_checks(frames) + joins.checks() + row_and_source_checks(clean, frames)
            + order_line_checks(clean, frames) + financial_checks(clean, frames) + summary_checks(clean, frames))


# ----------------------------------------------------------------------------- regression
def regression_checks(frames) -> list[CheckResult]:
    fo, fl, fr, dc = frames["fact_orders"], frames["fact_order_lines"], frames["fact_reviews"], frames["dim_customers"]
    diff = fo["payment_minus_items"]
    mism = diff[fo["dq_payment_items_mismatch"]]
    observed = {
        "order_lines": len(fl), "orders": len(fo), "persons": len(dc),
        "persons_with_several_orders": int((dc["n_orders"] > 1).sum()),
        "dq_payment_items_mismatch": int(fo["dq_payment_items_mismatch"].sum()),
        "orders_reconciled": int(diff.notna().sum()),
        "mismatch_overpaid": int((mism > 0).sum()), "mismatch_underpaid": int((mism < 0).sum()),
        "mismatch_gt_1": int((diff.abs() > 1).sum()),
        "orders_without_items": int((~fo["has_items"]).sum()), "orders_without_payment": int((~fo["has_payment"]).sum()),
        "orders_with_several_reviews": int((fo["review_count"] > 1).sum()),
        "late_deliveries": int(fo["is_late"].sum()),
        "delivery_over_60_days": int((fo["purchase_to_delivery_days"] > 60).sum()),
    }
    out = [check(f"R.{k}", REGRESSION, f"{k} (strategy 11.3 / NB02-NB03)", v, observed[k]) for k, v in BASELINE_ANALYTICAL.items()]
    no_items = fo.loc[~fo["has_items"], "order_status"].astype("string").value_counts().to_dict()
    out += [
        check("R.max_quantity", REGRESSION, f"max quantity per line <= {BASELINE_MAX_QUANTITY} (D-15)", True,
              int(fl["quantity"].max()) <= BASELINE_MAX_QUANTITY),
        check("R.mismatch_range", REGRESSION, "min / max payment_minus_items among mismatched orders (NB03 6.2)",
              BASELINE_MISMATCH_RANGE, (float(mism.min()), float(mism.max()))),
        check("R.orders_without_items_by_status", REGRESSION, "status of orders without items (Q-16)",
              dict(BASELINE_ORDERS_WITHOUT_ITEMS_BY_STATUS), {k: int(v) for k, v in sorted(no_items.items(), key=lambda kv: -kv[1])}),
        check("R.negative_unguarded_durations", REGRESSION,
              "approval before purchase / delivery before purchase (NB03 5.2: 0 / 0)", (0, 0),
              (int((fo["approval_hours"] < 0).sum()), int((fo["purchase_to_delivery_days"] < 0).sum()))),
        check("R.distinct_review_id", REGRESSION, "distinct review_id in fact_reviews (NB02 5.1)", 98_410,
              int(fr["review_id"].nunique())),
    ]
    return out


# ----------------------------------------------------------------------------- warnings
def warning_checks(clean, frames) -> list[CheckResult]:
    items = clean["order_items"]
    coarse = items.groupby(["order_id", "product_id", "seller_id", "price"])
    w01 = int(((coarse["freight_value"].nunique() > 1) | (coarse["shipping_limit_date"].nunique() > 1)).sum())
    fo, ds = frames["fact_orders"], frames["dim_sellers"]
    w04 = {"orders_with_customer_centroid": int((~fo["dq_customer_no_geolocation"]).sum()),
           "orders_share_pct": round(float((~fo["dq_customer_no_geolocation"]).mean() * 100), 3),
           "sellers_with_centroid": int((~ds["dq_no_geolocation"]).sum()),
           "sellers_share_pct": round(float((~ds["dq_no_geolocation"]).mean() * 100), 3)}
    w05 = {n: int((~frames[n]["in_candidate_window"]).sum()) for n in FACTS}
    fl = frames["fact_order_lines"]
    return [
        check("W01", WARNING, "(order_id, product_id, seller_id, price) groups with >1 freight_value or shipping_limit_date "
              "(rejected coarser grain, D-15)", None, w01),
        check("W04", WARNING, "orders / sellers that receive a centroid via zip_lookup", None, w04),
        check("W05", WARNING, "fact rows outside the candidate window 2017-01..2018-08 (flagged, not removed; Q-11)", None, w05),
        check("W.lines_with_quantity_gt_1", WARNING, "order lines with quantity > 1", None, int((fl["quantity"] > 1).sum())),
    ]
