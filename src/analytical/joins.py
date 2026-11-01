"""Validated joins (strategy P5, V11).

Every join in the analytical layer goes through ``JoinLog.merge``. It always passes
``validate=`` to pandas (a cardinality breach raises ``pandas.errors.MergeError``) and records
the row counts before and after, so the report shows that no join multiplied rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.staging.validation import BLOCKING, CheckResult

ALLOWED = {"one_to_one", "many_to_one"}  # a left join of either kind can never add rows


@dataclass
class JoinLog:
    records: list[dict] = field(default_factory=list)

    def merge(self, name: str, left: pd.DataFrame, right: pd.DataFrame, on, validate: str,
              how: str = "left") -> pd.DataFrame:
        if validate not in ALLOWED or how != "left":
            raise ValueError(f"join {name}: only left joins validated as {sorted(ALLOWED)} are allowed")
        out = left.merge(right, on=on, how=how, validate=validate, sort=False)
        out.index = left.index
        self.records.append({"join": name, "validate": validate, "how": how, "on": on if isinstance(on, list) else [on],
                             "left_rows": len(left), "right_rows": len(right), "result_rows": len(out)})
        return out

    def checks(self) -> list[CheckResult]:
        return [CheckResult(f"V11.{r['join']}", BLOCKING,
                            f"join {r['join']} (validate={r['validate']}, how={r['how']}): row count preserved",
                            r["left_rows"], r["result_rows"], r["left_rows"] == r["result_rows"])
                for r in self.records]
