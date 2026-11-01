"""Compare warehouse contents with the Gold Parquet frames (Task 5.2.4).

Independent of the loader's conversion code: Gold values are canonicalised directly from
pandas, database values from what PostgreSQL returns, and the two are compared cell by cell
after ordering both sides by the primary key (text keys in "C" collation = Python order).

Checks: row counts, column mapping, NULL counts, primary-key uniqueness, foreign-key orphans
(by query, independent of the constraints), exact monetary sums, and full content equality.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
from psycopg import sql
from sqlalchemy.engine import Connection

from .warehouse import LOAD_ORDER, expected_tables

# strategy 11.3.3 monetary baselines (only meaningful for the approved Gold run)
BASELINE_TOTALS = {("fact_order_lines", "line_price_total"): Decimal("13591643.70"),
                   ("fact_order_lines", "freight_total"): Decimal("2251909.54"),
                   ("fact_payments", "payment_value"): Decimal("16008872.12"),
                   ("fact_orders", "payment_total"): Decimal("16008872.12")}
TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"  # microseconds: any rounding by timestamp(0) would show


def canonical_gold(s: pd.Series, pg: str) -> list:
    missing = s.isna().to_numpy()
    if pg == "numeric(12,2)":  # exact cent values; Decimal("-0.00") == Decimal("0.00") (numeric has no -0)
        vals = [Decimal(f"{v:.2f}") for v in s.to_numpy(dtype=float, na_value=0.0)]
    elif pg == "date":
        vals = s.dt.strftime("%Y-%m-%d").tolist()
    elif pg.startswith("timestamp"):
        vals = s.dt.strftime(TS_FORMAT).tolist()
    elif pg == "integer[]":
        vals = [tuple(int(x) for x in a) if a is not None else None for a in s]
    elif pg in ("integer", "double precision", "boolean"):
        vals = s.astype(object).tolist()
    else:
        vals = s.astype(object).tolist()
    return [None if m else (bool(v) if pg == "boolean" else int(v) if pg == "integer"
                            else float(v) if pg == "double precision" else v if pg != "text" else str(v))
            for v, m in zip(vals, missing)]


def canonical_db(values, pg: str) -> list:
    def one(v):
        if v is None:
            return None
        if pg == "numeric(12,2)":
            return v  # Decimal as returned by PostgreSQL
        if pg == "date":
            return v.isoformat()
        if pg.startswith("timestamp"):
            return v.strftime(TS_FORMAT)
        if pg == "integer[]":
            return tuple(v)
        return v
    return [one(v) for v in values]


def _q(raw, query, params=None):
    with raw.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def compare_with_gold(conn: Connection, frames: dict[str, pd.DataFrame], schema: str,
                      check_baselines: bool = True) -> dict:
    raw = conn.connection.dbapi_connection
    exp = expected_tables()
    problems: list[str] = []
    detail: dict[str, dict] = {}

    for t in LOAD_ORDER:
        e, gold = exp[t], frames[t]
        tid = sql.Identifier(schema, t)
        info = detail.setdefault(t, {})

        db_cols = [r[0] for r in _q(raw, "SELECT column_name FROM information_schema.columns "
                                         "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                                    (schema, t))]
        if db_cols != list(gold.columns):
            problems.append(f"{t}: column mapping differs from Gold")
            continue

        n_db = _q(raw, sql.SQL("SELECT count(*) FROM {}").format(tid))[0][0]
        info["rows"] = (len(gold), n_db)
        if n_db != len(gold):
            problems.append(f"{t}: {n_db} rows in PostgreSQL, {len(gold)} in Gold")

        null_sql = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(sql.SQL("count(*) - count({})").format(sql.Identifier(c)) for c, _, _ in e.columns), tid)
        db_nulls = dict(zip(db_cols, _q(raw, null_sql)[0]))
        gold_nulls = {c: int(gold[c].isna().sum()) for c in gold.columns}
        bad_nulls = {c: (gold_nulls[c], db_nulls[c]) for c in gold.columns if gold_nulls[c] != db_nulls[c]}
        info["null_columns"] = sum(1 for v in gold_nulls.values() if v)
        if bad_nulls:
            problems.append(f"{t}: NULL counts differ (gold, db) {bad_nulls}")

        pk = sql.SQL(", ").join(sql.Identifier(c) for c in e.primary_key)
        dup = _q(raw, sql.SQL("SELECT count(*) - count(DISTINCT ({})) FROM {}").format(pk, tid))[0][0] \
            if len(e.primary_key) > 1 else \
            _q(raw, sql.SQL("SELECT count(*) - count(DISTINCT {}) FROM {}").format(pk, tid))[0][0]
        if dup:
            problems.append(f"{t}: {dup} duplicate primary keys")

        for col, parent, pcol in e.foreign_keys:
            orphans = _q(raw, sql.SQL("SELECT count(*) FROM {} c WHERE c.{} IS NULL OR NOT EXISTS "
                                      "(SELECT 1 FROM {} p WHERE p.{} = c.{})").format(
                tid, sql.Identifier(col), sql.Identifier(schema, parent), sql.Identifier(pcol), sql.Identifier(col)))[0][0]
            if orphans:
                problems.append(f"{t}.{col} -> {parent}: {orphans} orphan or NULL foreign keys")

        money = [c for c, pg, _ in e.columns if pg == "numeric(12,2)"]
        if money:
            sums = _q(raw, sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(sql.SQL("coalesce(sum({}), 0)").format(sql.Identifier(c)) for c in money), tid))[0]
            info["money_totals"] = {}
            for c, db_sum in zip(money, sums):
                gold_sum = sum((v for v in canonical_gold(gold[c], "numeric(12,2)") if v is not None), Decimal("0"))
                info["money_totals"][c] = str(db_sum)
                if db_sum != gold_sum:
                    problems.append(f"{t}.{c}: sum {db_sum} in PostgreSQL, {gold_sum} in Gold")
                base = BASELINE_TOTALS.get((t, c))
                if check_baselines and base is not None and db_sum != base:
                    problems.append(f"{t}.{c}: sum {db_sum} differs from the strategy baseline {base}")

        # full content equality, both sides ordered by the primary key
        order = sql.SQL(", ").join(
            sql.SQL("{} COLLATE \"C\"").format(sql.Identifier(c)) if dict((x, p) for x, p, _ in e.columns)[c] == "text"
            else sql.Identifier(c) for c in e.primary_key)
        rows = _q(raw, sql.SQL("SELECT * FROM {} ORDER BY {}").format(tid, order))
        g = gold.sort_values(list(e.primary_key), kind="stable").reset_index(drop=True)
        db_columns = list(zip(*rows)) if rows else [()] * len(e.columns)
        mismatched = {}
        for i, (c, pg, _) in enumerate(e.columns):
            a, b = canonical_gold(g[c], pg), canonical_db(db_columns[i], pg)
            diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
            if diff:
                mismatched[c] = diff
        info["cells_compared"] = len(g) * len(e.columns)
        if mismatched:
            problems.append(f"{t}: content differs from Gold in {len(mismatched)} column(s) {mismatched}")

    return {"ok": not problems, "problems": problems, "tables": detail,
            "rows_total": sum(d.get("rows", (0, 0))[1] for d in detail.values()),
            "cells_compared": sum(d.get("cells_compared", 0) for d in detail.values())}


def validate_loaded(engine, frames: dict[str, pd.DataFrame], schema: str, check_baselines: bool = True) -> dict:
    """Run ``compare_with_gold`` on a fresh, read-only connection (post-commit check)."""
    with engine.connect() as conn:
        return compare_with_gold(conn, frames, schema, check_baselines)
