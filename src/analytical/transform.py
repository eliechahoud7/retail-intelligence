"""Assemble the analytical layer from clean frames (strategy section 13, steps 11-13)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import dims
from . import facts as f
from .joins import JoinLog


@dataclass
class AnalyticalBuild:
    frames: dict[str, pd.DataFrame]
    joins: JoinLog
    steps: list[dict] = field(default_factory=list)


def build_analytical(clean: dict[str, pd.DataFrame]) -> AnalyticalBuild:
    c = clean
    joins = JoinLog()
    b = AnalyticalBuild(frames={}, joins=joins)

    def step(name, decision, inputs, output):
        b.steps.append({"step": name, "decision": decision, "inputs": {k: len(c[k]) for k in inputs},
                        "output": output, "rows_out": len(b.frames[output])})

    ctx = f.order_context(c["orders"], c["customers"], joins)
    lines = f.fact_order_lines(c["order_items"], ctx, joins)
    b.frames["fact_order_lines"] = lines
    step("order lines at the six-attribute grain (quantity, line totals, order_item_ids)", "D-15",
         ["order_items", "orders", "customers"], "fact_order_lines")

    b.frames["fact_orders"] = f.fact_orders(c["orders"], c["customers"], c["order_items"], c["payments"], c["reviews"],
                                            lines, joins)
    step("orders + independently aggregated item, payment and review summaries (one_to_one); mismatch flag; durations",
         "D-13 / D-08 / Q-04 / Q-11", ["orders", "customers", "order_items", "payments", "reviews"], "fact_orders")
    b.frames["fact_payments"] = f.fact_payments(c["payments"], ctx, joins)
    step("payment transactions + order context (many_to_one)", "D-13", ["payments", "orders"], "fact_payments")
    b.frames["fact_reviews"] = f.fact_reviews(c["reviews"], ctx, c["orders"], joins)
    step("review-order links + order context (many_to_one)", "D-07 / Q-04", ["reviews", "orders"], "fact_reviews")

    b.frames["dim_customers"] = dims.dim_customers(b.frames["fact_orders"])
    step("persons from fact_orders: all-order and delivered-order measures", "D-01", ["customers", "orders"], "dim_customers")
    b.frames["dim_products"] = dims.dim_products(c["products"])
    step("product attributes, category labels and flags", "D-12", ["products"], "dim_products")
    b.frames["dim_sellers"] = dims.dim_sellers(c["sellers"])
    step("seller attributes and flags", "D-11 / D-10", ["sellers"], "dim_sellers")
    o, r = c["orders"], c["reviews"]
    b.frames["dim_dates"] = dims.dim_dates([o[col] for col in (f.P, f.A, f.C, f.D, f.E)]
                                           + [c["order_items"]["shipping_limit_date"], r["review_creation_date"],
                                              r["review_answer_timestamp"]])
    step("calendar from the first to the last event date", "13 step 13", ["orders", "order_items", "reviews"], "dim_dates")
    return b
