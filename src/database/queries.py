"""Advanced SQL analytics queries (Task 5.3.1).

Queries live in ``src/database/sql/queries/<group>/NN_<name>.sql``, one per file, with a header::

    -- name: <unique query name>
    -- grain: <what one result row represents>
    -- key: <comma-separated columns that are unique at that grain>
    -- purpose: ...            (may continue on following "--" lines)
    -- competencies: ...
    -- notes: ...
    -- params: reference_date:date; order_scope:choice(delivered|all)=delivered   (optional)

``{w}`` / ``{a}`` are the warehouse / analytics schema placeholders (as for the views).
Declared parameters are referenced in the SQL as ``:name`` bind parameters. Their values are
validated in Python (``date`` = ISO date, ``choice`` = one of the listed values, ``int``) and then
passed to the driver as bound values - never formatted into the SQL text. A parameter without a
default is required; nothing falls back to CURRENT_DATE.

* ``run_query`` executes one query in a READ ONLY transaction that is always rolled back, so an
  analytical query can never modify the warehouse. Every SQL text executed that way must be exactly
  ONE statement (``check_single_statement``: a trailing ';' is allowed, a second statement - e.g.
  ``; COMMIT`` - is rejected before anything is sent), so the read-only transaction cannot be ended.
* ``verify_queries`` checks that every query executes, returns rows and keeps its declared key
  unique, and runs the reconciliation checks in ``RECONCILIATIONS`` against the fact tables. A check
  that raises is reported as a failed check; it does not stop the run.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .connection import get_engine
from .warehouse import ANALYTICS_SCHEMA, WAREHOUSE_SCHEMA

QUERY_DIR = Path(__file__).resolve().parent / "sql" / "queries"
HEADER_FIELDS = ("name", "grain", "key", "purpose", "competencies", "notes", "params")
_FIELD = re.compile(r"^--\s*(" + "|".join(HEADER_FIELDS) + r"):\s*(.*)$")
_PARAM = re.compile(r"^(?P<name>[a-z_][a-z0-9_]*):(?P<type>date|int|choice\((?P<choices>[a-z0-9_|]+)\))"
                    r"(?:=(?P<default>[a-z0-9_-]+))?$")


class QueryParameterError(ValueError):
    """A query parameter is unknown, missing or invalid."""


class UnknownQueryError(KeyError):
    """No analytical query has the requested name (a KeyError, as before, for existing callers)."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


class UnsafeQueryError(ValueError):
    """SQL text that is not exactly one statement (e.g. a second statement appended after ';')."""


_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def statement_code(sql: str) -> str:
    """``sql`` with comments and quoted text blanked out, so that only real SQL tokens remain.

    A small PostgreSQL lexer (structure, not keywords): -- line comments, nested /* */ comments,
    '...' strings ('' escapes; E'...' also backslash escapes), "..." identifiers and $tag$...$tag$
    dollar quoting. A ';' inside any of these is text, not a statement separator. Unterminated
    constructs raise UnsafeQueryError.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        ch, nxt = sql[i], sql[i + 1:i + 2]
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif ch == "/" and nxt == "*":
            depth, i = 1, i + 2
            while depth:
                if i >= n:
                    raise UnsafeQueryError("unterminated /* comment")
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
        elif ch in "'\"":
            backslash = (ch == "'" and i > 0 and sql[i - 1] in "eE" and not (i > 1 and _is_word_char(sql[i - 2])))
            j = i + 1
            while True:
                if j >= n:
                    raise UnsafeQueryError(f"unterminated {ch} quote")
                if backslash and sql[j] == "\\":
                    j += 2
                elif sql[j] == ch and sql[j + 1:j + 2] == ch:
                    j += 2
                elif sql[j] == ch:
                    break
                else:
                    j += 1
            out.append(" ")
            i = j + 1
        elif ch == "$" and not (i > 0 and _is_word_char(sql[i - 1])) and (m := _DOLLAR_TAG.match(sql, i)):
            end = sql.find(m.group(0), m.end())
            if end < 0:
                raise UnsafeQueryError("unterminated dollar quote")
            out.append(" ")
            i = end + len(m.group(0))
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check_single_statement(sql: str) -> None:
    """Raise UnsafeQueryError unless ``sql`` is exactly one non-empty statement (one trailing ';' allowed)."""
    code = statement_code(sql).strip()
    if code.endswith(";"):
        code = code[:-1].rstrip()
    if not code:
        raise UnsafeQueryError("empty SQL statement")
    if ";" in code:
        raise UnsafeQueryError("multiple SQL statements are not allowed (';' before the end of the statement)")


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str                       # "date", "int" or "choice"
    choices: tuple[str, ...] = ()
    default: str | None = None

    @property
    def required(self) -> bool:
        return self.default is None

    def convert(self, value):
        try:
            if self.type == "date":
                return value if isinstance(value, dt.date) and not isinstance(value, dt.datetime) \
                    else dt.date.fromisoformat(str(value))
            if self.type == "int":
                return int(value)
        except (TypeError, ValueError):
            raise QueryParameterError(f"parameter {self.name}: {value!r} is not a valid {self.type}") from None
        if str(value) not in self.choices:
            raise QueryParameterError(f"parameter {self.name}: {value!r} is not one of {list(self.choices)}")
        return str(value)


def parse_params(spec: str) -> tuple[ParamSpec, ...]:
    out = []
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        m = _PARAM.match(part)
        if not m:
            raise ValueError(f"invalid parameter declaration {part!r}")
        kind = "choice" if m.group("choices") else m.group("type")
        choices = tuple(m.group("choices").split("|")) if m.group("choices") else ()
        p = ParamSpec(m.group("name"), kind, choices, m.group("default"))
        if p.default is not None:
            p.convert(p.default)  # a default must itself be valid
        out.append(p)
    return tuple(out)


@dataclass(frozen=True)
class Query:
    name: str
    group: str
    path: Path
    grain: str
    key: tuple[str, ...]
    purpose: str
    competencies: str
    notes: str = ""
    params: tuple[ParamSpec, ...] = ()
    sql: str = field(default="", repr=False)

    def bind(self, values: dict | None = None) -> dict:
        """Validated bind values for this query (defaults applied; unknown or missing -> error)."""
        values = dict(values or {})
        unknown = sorted(set(values) - {p.name for p in self.params})
        if unknown:
            raise QueryParameterError(f"{self.name}: unknown parameter(s) {unknown}")
        bound = {}
        for p in self.params:
            if p.name not in values and p.required:
                raise QueryParameterError(f"{self.name}: required parameter {p.name!r} missing "
                                          f"(e.g. --param {p.name}=2018-09-01)")
            bound[p.name] = p.convert(values.get(p.name, p.default))
        for rule, description in PARAM_CONSTRAINTS.get(self.name, ()):
            if not rule(bound):
                raise QueryParameterError(f"{self.name}: invalid parameters {bound}: requires {description}")
        return bound


# Cross-parameter rules checked by Query.bind (after each value is validated on its own)
PARAM_CONSTRAINTS = {
    "product_sales_pareto": [
        (lambda p: 0 < p["a_threshold_pct"] < p["b_threshold_pct"] <= 100,
         "0 < a_threshold_pct < b_threshold_pct <= 100"),
    ],
}


def parse_header(sql: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    current = None
    for line in sql.splitlines():
        if not line.startswith("--"):
            break
        m = _FIELD.match(line)
        if m:
            current = m.group(1)
            meta[current] = m.group(2).strip()
        elif current:
            meta[current] += " " + line.lstrip("-").strip()
    return meta


def load_queries(query_dir: Path = QUERY_DIR) -> list[Query]:
    queries = []
    for path in sorted(Path(query_dir).glob("*/[0-9][0-9]_*.sql")):
        sql = path.read_text(encoding="utf-8")
        meta = parse_header(sql)
        missing = [f for f in ("name", "grain", "key", "purpose", "competencies") if not meta.get(f)]
        if missing:
            raise ValueError(f"{path.name}: header misses {missing}")
        try:
            check_single_statement(sql)
        except UnsafeQueryError as exc:
            raise ValueError(f"{path.name}: {exc}") from None
        params = parse_params(meta.get("params", ""))
        used =set(re.findall(r"(?<![:\w]):([a-z_][a-z0-9_]*)\b", sql))
        if used != {p.name for p in params}:
            raise ValueError(f"{path.name}: bind parameters in SQL {sorted(used)} differ from the declared "
                             f"{sorted(p.name for p in params)}")
        queries.append(Query(name=meta["name"], group=path.parent.name, path=path, grain=meta["grain"],
                             key=tuple(k.strip() for k in meta["key"].split(",")), purpose=meta["purpose"],
                             competencies=meta["competencies"], notes=meta.get("notes", ""), params=params, sql=sql))
    names = [q.name for q in queries]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate query names: {sorted({n for n in names if names.count(n) > 1})}")
    return queries


def get_query(name: str) -> Query:
    for q in load_queries():
        if q.name == name:
            return q
    raise UnknownQueryError(f"unknown query {name!r}; available: {[q.name for q in load_queries()]}")


def _render(q: Query, conn, warehouse_schema: str, analytics_schema: str) -> str:
    quote = conn.dialect.identifier_preparer.quote
    return q.sql.format(w=quote(warehouse_schema), a=quote(analytics_schema))


def _read_only(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Execute in a READ ONLY transaction that is always rolled back; ``params`` are bound values.

    ``sql`` must be a single statement (checked before anything is sent to the server).
    """
    check_single_statement(sql)
    trans = conn.begin()
    try:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        result = conn.execute(text(sql), params or {})
        return pd.DataFrame(result.fetchall(), columns=list(result.keys()))
    finally:
        trans.rollback()


def run_query(name: str, engine: Engine | None = None, warehouse_schema: str = WAREHOUSE_SCHEMA,
              analytics_schema: str = ANALYTICS_SCHEMA, params: dict | None = None) -> pd.DataFrame:
    q = get_query(name)
    bound = q.bind(params)  # validate before connecting
    own = engine is None
    engine = engine or get_engine()
    try:
        with engine.connect() as conn:
            return _read_only(conn, _render(q, conn, warehouse_schema, analytics_schema), bound)
    finally:
        if own:
            engine.dispose()


# ----------------------------------------------------------------------------- reconciliation
def _dec(v) -> Decimal:
    """Exact comparison value: SQL NULL / NaN -> 0; numpy scalars (from pandas) -> Python numbers first."""
    if hasattr(v, "item"):
        v = v.item()
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal(0)
    return v if isinstance(v, Decimal) else Decimal(str(v))


# D-06 delivery eligibility and the 27 federative units, used by the independent delivery checks
_ELIGIBLE = "FROM {w}.fact_orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL"
_UFS = ", ".join(f"'{uf}'" for uf in sorted({"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
                                             "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}))

# RFM eligibility as of :reference_date, derived INDEPENDENTLY of customer_rfm (which reads
# fact_orders.has_items, purchase_date, items_price_total and casts the delivery timestamp to a date):
# here the order lines are the source - an order "has items" because it has order lines, its value is
# the sum of its line totals, its status and purchase timestamp come from the lines' order context - and
# both cut-offs are half-open timestamp ranges (< the day after the reference date). Delivered scope:
# status 'delivered' AND a delivery timestamp before the end of the reference date (NULL never matches).
# FROM fact_order_lines with an EXISTS filter: one row per line, never multiplied by a join.
_RFM_END = "CAST(:reference_date AS date) + 1"
_RFM_LINES = ("FROM {w}.fact_order_lines l WHERE l.order_purchase_timestamp < " + _RFM_END + " "
              "AND (CAST(:order_scope AS text) = 'all' OR (l.order_status = 'delivered' AND EXISTS ("
              "SELECT 1 FROM {w}.fact_orders o WHERE o.order_id = l.order_id "
              "AND o.order_delivered_customer_date < " + _RFM_END + ")))")

# Candidate-window monthly item-based sales, independently from fact_order_lines (Gold in_candidate_window),
# and per (month, category) through the many-to-one join to dim_products
_WINDOW_MONTHS = ("SELECT to_char(purchase_date, 'YYYY-MM') AS ym, sum(line_price_total) AS s "
                  "FROM {w}.fact_order_lines WHERE in_candidate_window GROUP BY 1")
_WINDOW_CATEGORY_MONTHS = ("SELECT to_char(l.purchase_date, 'YYYY-MM') AS ym, p.category_reporting AS cat, "
                           "sum(l.line_price_total) AS s FROM {w}.fact_order_lines l "
                           "JOIN {w}.dim_products p ON p.product_id = l.product_id WHERE l.in_candidate_window GROUP BY 1, 2")

# query name -> list of (description, function(result DataFrame) -> value, SQL on the warehouse tables)
RECONCILIATIONS: dict = {
    "sales_best_worst_months": [
        ("best month sales = largest candidate-window monthly fact_order_lines total",
         lambda df: _dec(df.loc[df["rank_best"] == 1, "items_price_total"].iloc[0]),
         f"SELECT coalesce(max(s), 0) FROM ({_WINDOW_MONTHS}) m"),
        ("worst month sales = smallest candidate-window monthly total (months without sales = 0)",
         lambda df: _dec(df.loc[df["rank_worst"] == 1, "items_price_total"].iloc[0]),
         "SELECT coalesce(min(coalesce(m.s, 0)), 0) FROM (SELECT DISTINCT year_month FROM {w}.dim_dates "
         "WHERE in_candidate_window AND date BETWEEN (SELECT min(purchase_date) FROM {w}.fact_orders) "
         f"AND (SELECT max(purchase_date) FROM {{w}}.fact_orders)) d LEFT JOIN ({_WINDOW_MONTHS}) m ON m.ym = d.year_month"),
    ],
    "category_monthly_ranking": [
        ("each month's top category sales = largest (month, category) fact_order_lines total, summed over months",
         lambda df: sum(map(_dec, df.loc[df["row_number_in_month"] == 1, "items_price_total"])),
         f"SELECT coalesce(sum(mx), 0) FROM (SELECT ym, max(s) AS mx FROM ({_WINDOW_CATEGORY_MONTHS}) c GROUP BY ym) x"),
        ("each month's top category share = its sales / the month's fact_order_lines total (largest share)",
         lambda df: _dec(df.loc[df["row_number_in_month"] == 1, "share_of_month_pct"].max()),
         f"SELECT round(max(100 * c.mx / m.s), 2) FROM (SELECT ym, max(s) AS mx FROM ({_WINDOW_CATEGORY_MONTHS}) x "
         f"GROUP BY ym) c JOIN ({_WINDOW_MONTHS}) m ON m.ym = c.ym"),
        ("months reported = candidate-window months with category sales",
         lambda df: int(df["year_month"].nunique()), f"SELECT count(*) FROM ({_WINDOW_MONTHS}) m"),
        ("months whose reported shares exceed 100% (rounding tolerance 0.005 per row)",
         lambda df: int(sum(1 for _, g in df.groupby("year_month")
                            if sum(map(_dec, g["share_of_month_pct"])) > 100 + Decimal("0.005") * len(g))),
         "SELECT 0"),
    ],
    "sales_monthly_trend": [
        ("sum of monthly items_price_total = fact_order_lines total",
         lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("last items running total = fact_order_lines total",
         lambda df: _dec(df["items_price_running_total"].iloc[-1]),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("sum of monthly payment_total = fact_payments total",
         lambda df: sum(map(_dec, df["payment_total"])),
         "SELECT coalesce(sum(payment_value), 0) FROM {w}.fact_payments"),
        ("sum of monthly n_orders = fact_orders rows",
         lambda df: int(df["n_orders"].sum()), "SELECT count(*) FROM {w}.fact_orders"),
    ],
    "sales_daily_moving_averages": [
        ("one row per day between first and last purchase",
         lambda df: len(df), "SELECT max(purchase_date) - min(purchase_date) + 1 FROM {w}.fact_orders"),
    ],
    "sales_by_weekday": [
        ("weekday items totals = candidate-window fact_order_lines total",
         lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines WHERE in_candidate_window"),
    ],
    "customer_purchase_summary": [
        ("one row per person of dim_customers", lambda df: len(df), "SELECT count(*) FROM {w}.dim_customers"),
        ("sum of n_orders = fact_orders rows", lambda df: int(df["n_orders"].sum()), "SELECT count(*) FROM {w}.fact_orders"),
        ("sum of spending = fact_order_lines total (no double counting)",
         lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("repeat customers = persons with more than one order", lambda df: int(df["is_repeat_customer"].sum()),
         "SELECT count(*) FROM (SELECT 1 FROM {w}.fact_orders GROUP BY customer_unique_id HAVING count(*) > 1) x"),
    ],
    "repeat_customer_analysis": [
        ("grand total customers = persons with orders",
         lambda df: int(df.loc[df["customer_segment"] == "all", "n_customers"].iloc[0]),
         "SELECT count(DISTINCT customer_unique_id) FROM {w}.fact_orders"),
        ("frequency groups add up to the grand total",
         lambda df: int(df.loc[df["n_orders"].notna(), "n_customers"].sum()),
         "SELECT count(DISTINCT customer_unique_id) FROM {w}.fact_orders"),
        ("grand total orders = fact_orders rows",
         lambda df: int(df.loc[df["customer_segment"] == "all", "n_orders_total"].iloc[0]),
         "SELECT count(*) FROM {w}.fact_orders"),
        ("grand total item-based sales = fact_order_lines total",
         lambda df: _dec(df.loc[df["customer_segment"] == "all", "items_price_total"].iloc[0]),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
    ],
    "customer_spending_ranking": [
        ("rank 1 spending = largest per-person fact_order_lines total",
         lambda df: _dec(df["items_price_total"].iloc[0]),
         "SELECT max(s) FROM (SELECT sum(line_price_total) s FROM {w}.fact_order_lines GROUP BY customer_unique_id) x"),
        ("ranked population = persons with order lines", lambda df: int(df["n_ranked_customers"].iloc[0]),
         "SELECT count(DISTINCT customer_unique_id) FROM {w}.fact_order_lines"),
    ],
    "interpurchase_intervals": [
        ("intervals = purchase events minus persons (each person's first event has no interval)",
         lambda df: len(df),
         "SELECT count(*) - count(DISTINCT customer_unique_id) FROM "
         "(SELECT DISTINCT customer_unique_id, order_purchase_timestamp FROM {w}.fact_orders) e"),
    ],
    "top_sellers_by_sales": [
        ("one row per seller of dim_sellers", lambda df: len(df), "SELECT count(*) FROM {w}.dim_sellers"),
        ("sum of seller sales = fact_order_lines total", lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("sum of seller freight = fact_order_lines freight", lambda df: sum(map(_dec, df["items_freight_total"])),
         "SELECT coalesce(sum(freight_total), 0) FROM {w}.fact_order_lines"),
        ("sum of seller units = fact_order_lines quantity", lambda df: int(df["units"].sum()),
         "SELECT coalesce(sum(quantity), 0) FROM {w}.fact_order_lines"),
        ("rank 1 seller sales = largest per-seller total", lambda df: _dec(df["items_price_total"].iloc[0]),
         "SELECT coalesce(max(s), 0) FROM (SELECT sum(line_price_total) s FROM {w}.fact_order_lines GROUP BY seller_id) x"),
        ("shared-order counts = multi-seller orders x their sellers",
         lambda df: int(df["n_orders_shared_with_other_sellers"].sum()),
         "SELECT coalesce(sum(n), 0) FROM (SELECT count(DISTINCT seller_id) n FROM {w}.fact_order_lines "
         "GROUP BY order_id HAVING count(DISTINCT seller_id) > 1) x"),
    ],
    "best_selling_products": [
        ("one row per product of dim_products", lambda df: len(df), "SELECT count(*) FROM {w}.dim_products"),
        ("sum of product units = fact_order_lines quantity", lambda df: int(df["units"].sum()),
         "SELECT coalesce(sum(quantity), 0) FROM {w}.fact_order_lines"),
        ("sum of product sales = fact_order_lines total", lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("never-sold products = products without order lines", lambda df: int(df["never_sold"].sum()),
         "SELECT count(*) FROM {w}.dim_products p WHERE NOT EXISTS "
         "(SELECT 1 FROM {w}.fact_order_lines l WHERE l.product_id = p.product_id)"),
    ],
    "category_performance_ranking": [
        ("sum of category products = dim_products rows", lambda df: int(df["n_products"].sum()),
         "SELECT count(*) FROM {w}.dim_products"),
        ("sum of category sales = fact_order_lines total", lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("sum of category units = fact_order_lines quantity", lambda df: int(df["units"].sum()),
         "SELECT coalesce(sum(quantity), 0) FROM {w}.fact_order_lines"),
        ("categories = distinct category_reporting values", lambda df: len(df),
         "SELECT count(DISTINCT category_reporting) FROM {w}.dim_products"),
    ],
    "product_sales_pareto": [
        ("population = products with item-based sales > 0", lambda df: len(df),
         "SELECT count(*) FROM (SELECT product_id FROM {w}.fact_order_lines GROUP BY 1 HAVING sum(line_price_total) > 0) x"),
        ("sum of product sales = fact_order_lines total", lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("last cumulative share = 100%", lambda df: _dec(df["cumulative_share_pct"].iloc[-1]), "SELECT 100.0000"),
        ("last cumulative product percentage = 100%", lambda df: _dec(df["cumulative_pct_of_products"].iloc[-1]),
         "SELECT 100.0000"),
    ],
    "seller_product_mix": [
        ("one row per (seller, product) with sales", lambda df: len(df),
         "SELECT count(*) FROM (SELECT DISTINCT seller_id, product_id FROM {w}.fact_order_lines) x"),
        ("sum of combination sales = fact_order_lines total", lambda df: sum(map(_dec, df["items_price_total"])),
         "SELECT coalesce(sum(line_price_total), 0) FROM {w}.fact_order_lines"),
        ("sum of combination units = fact_order_lines quantity", lambda df: int(df["units"].sum()),
         "SELECT coalesce(sum(quantity), 0) FROM {w}.fact_order_lines"),
        ("products sold by several sellers", lambda df: int(df.loc[df["n_sellers_of_product"] > 1, "product_id"].nunique()),
         "SELECT count(*) FROM (SELECT product_id FROM {w}.fact_order_lines GROUP BY 1 HAVING count(DISTINCT seller_id) > 1) x"),
    ],
    "delivery_monthly_performance": [
        ("sum of n_delivered = D-06 eligible orders", lambda df: int(df["n_delivered"].sum()), f"SELECT count(*) {_ELIGIBLE}"),
        ("sum of n_late = eligible late orders (Gold is_late)", lambda df: int(df["n_late"].sum()),
         f"SELECT count(*) {_ELIGIBLE} AND is_late"),
        ("sum of n_early = eligible orders delivered before the estimated date", lambda df: int(df["n_early"].sum()),
         f"SELECT count(*) {_ELIGIBLE} AND delivery_vs_estimate_days < 0"),
        ("one row per calendar month between the first and last delivery month", lambda df: len(df),
         "SELECT (extract(year FROM age(date_trunc('month', max(order_delivered_customer_date)), "
         "date_trunc('month', min(order_delivered_customer_date)))) * 12 + extract(month FROM age("
         "date_trunc('month', max(order_delivered_customer_date)), date_trunc('month', min(order_delivered_customer_date)))) + 1)::int "
         f"{_ELIGIBLE}"),
    ],
    "delivery_state_performance": [
        ("one row per customer state with orders", lambda df: len(df),
         "SELECT count(DISTINCT customer_state_std) FROM {w}.fact_orders"),
        ("sum of n_delivered = D-06 eligible orders", lambda df: int(df["n_delivered"].sum()), f"SELECT count(*) {_ELIGIBLE}"),
        ("sum of n_late = eligible late orders", lambda df: int(df["n_late"].sum()), f"SELECT count(*) {_ELIGIBLE} AND is_late"),
        ("fastest state's median = minimum per-state median",
         lambda df: _dec(df.loc[df["speed_rank"] == 1, "median_delivery_days"].iloc[0]),
         "SELECT round(min(m)::numeric, 2) FROM (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY purchase_to_delivery_days) m "
         f"{_ELIGIBLE} GROUP BY customer_state_std) x"),
    ],
    "delivery_region_ranking": [
        ("sum of n_delivered = D-06 eligible orders", lambda df: int(df["n_delivered"].sum()), f"SELECT count(*) {_ELIGIBLE}"),
        ("sum of n_late = eligible late orders", lambda df: int(df["n_late"].sum()), f"SELECT count(*) {_ELIGIBLE} AND is_late"),
        ("'Unknown' region = eligible orders outside the 27 federative units",
         lambda df: int(df.loc[df["region"] == "Unknown", "n_delivered"].iloc[0]),
         f"SELECT count(*) {_ELIGIBLE} AND customer_state_std NOT IN ({_UFS})"),
        ("mapped states cover every federative unit with eligible orders", lambda df: int(df.loc[df["region"] != "Unknown", "n_states"].sum()),
         f"SELECT count(DISTINCT customer_state_std) {_ELIGIBLE} AND customer_state_std IN ({_UFS})"),
    ],
    "estimated_vs_actual_delivery": [
        ("one row per D-06 eligible order", lambda df: len(df), f"SELECT count(*) {_ELIGIBLE}"),
        ("sum of delay_days = sum of Gold delivery_vs_estimate_days", lambda df: int(df["delay_days"].sum()),
         f"SELECT coalesce(sum(delivery_vs_estimate_days), 0)::bigint {_ELIGIBLE}"),
        ("late class = eligible Gold is_late", lambda df: int((df["delivery_class"] == "late").sum()),
         f"SELECT count(*) {_ELIGIBLE} AND is_late"),
        ("delay = actual_days - estimated_days on every row (violations)",
         lambda df: int((df["delay_days"] != df["actual_days"] - df["estimated_days"]).sum()), "SELECT 0"),
    ],
    "delivery_delay_distribution": [
        ("sum of bucket counts = D-06 eligible orders (exhaustive)", lambda df: int(df["n_orders"].sum()),
         f"SELECT count(*) {_ELIGIBLE}"),
        ("late buckets = eligible late orders", lambda df: int(df.loc[df["delivery_class"] == "late", "n_orders"].sum()),
         f"SELECT count(*) {_ELIGIBLE} AND is_late"),
        ("on-estimated-day bucket = eligible orders with delay 0",
         lambda df: int(df.loc[df["delivery_class"] == "on_estimated_day", "n_orders"].sum()),
         f"SELECT count(*) {_ELIGIBLE} AND delivery_vs_estimate_days = 0"),
        ("last cumulative percentage = 100%", lambda df: _dec(df["cumulative_pct"].iloc[-1]), "SELECT 100.00"),
    ],
    "customer_rfm": [
        ("population = persons with an order purchased before the end of the reference date", lambda df: len(df),
         f"SELECT count(DISTINCT customer_unique_id) FROM {{w}}.fact_orders WHERE order_purchase_timestamp < {_RFM_END}"),
        ("eligible persons = persons with eligible order lines", lambda df: int(df["has_eligible_purchase"].sum()),
         f"SELECT count(DISTINCT l.customer_unique_id) {_RFM_LINES}"),
        ("sum of frequency = distinct orders with eligible order lines", lambda df: int(df["frequency"].fillna(0).sum()),
         f"SELECT count(DISTINCT l.order_id) {_RFM_LINES}"),
        ("sum of monetary = fact_order_lines total of eligible orders", lambda df: sum(map(_dec, df["monetary"])),
         f"SELECT coalesce(sum(l.line_price_total), 0) {_RFM_LINES}"),
    ],
}


# Parameters used by verify_queries for queries with required parameters: derived from the data
# (never from the clock), so verification is reproducible.
def _last_delivered_purchase(conn, w: str) -> dict:
    day = _read_only(conn, f"SELECT max(purchase_date) FROM {w}.fact_orders WHERE order_status = 'delivered'").iat[0, 0]
    return {"reference_date": day}


VERIFY_PARAMS = {"customer_rfm": _last_delivered_purchase}


def _bind_subset(sql: str, bound: dict) -> dict:
    names = set(re.findall(r"(?<![:\w]):([a-z_][a-z0-9_]*)\b", sql))
    return {k: v for k, v in bound.items() if k in names}


def run_checks(conn, q: Query, df: pd.DataFrame, bound: dict, w: str, a: str) -> tuple[list[dict], list[str]]:
    """Run the reconciliation checks of ``q`` on its result ``df``; returns (checks, problems).

    ``w`` / ``a`` are the quoted schema names. A check whose function or SQL raises is recorded as a
    failed check (with its error) and the remaining checks still run.
    """
    checks, problems = [], []
    for desc, fn, sql in RECONCILIATIONS.get(q.name, []):
        try:
            got = fn(df)
            check_sql = sql.format(w=w, a=a)
            want = _read_only(conn, check_sql, _bind_subset(check_sql, bound)).iat[0, 0]
            ok = _dec(got) == _dec(want)
        except Exception as exc:  # report the broken check and continue with the others
            error = f"{type(exc).__name__}: {(str(exc).strip().splitlines() or [''])[0]}"
            checks.append({"check": desc, "query": None, "tables": None, "ok": False, "error": error})
            problems.append(f"{q.name}: {desc}: check failed with {error}")
            continue
        checks.append({"check": desc, "query": str(got), "tables": str(want), "ok": ok})
        if not ok:
            problems.append(f"{q.name}: {desc}: query {got} != tables {want}")
    return checks, problems


def verify_queries(engine: Engine | None = None, warehouse_schema: str = WAREHOUSE_SCHEMA,
                   analytics_schema: str = ANALYTICS_SCHEMA) -> dict:
    own = engine is None
    engine = engine or get_engine()
    results, problems = [], []
    try:
        with engine.connect() as conn:
            quote = conn.dialect.identifier_preparer.quote
            w, a = quote(warehouse_schema), quote(analytics_schema)
            for q in load_queries():
                entry = {"name": q.name, "group": q.group, "grain": q.grain, "rows": None, "checks": []}
                results.append(entry)
                try:
                    bound = q.bind(VERIFY_PARAMS[q.name](conn, w) if q.name in VERIFY_PARAMS else None)
                    entry["params"] = {k: str(v) for k, v in bound.items()}
                    df = _read_only(conn, _render(q, conn, warehouse_schema, analytics_schema), bound)
                except Exception as exc:  # report and continue with the other queries
                    problems.append(f"{q.name}: failed to execute ({type(exc).__name__}: {str(exc).splitlines()[0]})")
                    continue
                entry["rows"] = len(df)
                if df.empty:
                    problems.append(f"{q.name}: returned no rows")
                    continue
                missing_key = [k for k in q.key if k not in df.columns]
                if missing_key:
                    problems.append(f"{q.name}: declared key columns {missing_key} not in the result")
                elif df.duplicated(list(q.key)).any():
                    problems.append(f"{q.name}: declared key {q.key} is not unique ({int(df.duplicated(list(q.key)).sum())} duplicates)")
                checks, check_problems = run_checks(conn, q, df, bound, w, a)
                entry["checks"] = checks
                problems.extend(check_problems)
    finally:
        if own:
            engine.dispose()
    return {"ok": not problems, "problems": problems, "queries": results}
