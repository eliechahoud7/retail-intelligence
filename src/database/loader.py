"""Load the Gold Parquet outputs into the PostgreSQL warehouse (Task 5.2.4; design W-13, Q1).

One ``load_warehouse`` call is ONE database transaction as the application role:

1. the Gold run is located and verified *before* anything is written (``locate_gold``);
2. ``TRUNCATE`` of the eight Gold tables (``load_run`` history is kept);
3. ``COPY`` of every table in foreign-key order (bulk load, no row-by-row inserts);
4. full validation of the loaded data against Gold *inside the transaction* (``compare``);
5. one ``warehouse.load_run`` row, then COMMIT.

Any error at any step rolls everything back: the previous warehouse contents stay intact and
no load-history row is written.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from psycopg import sql
from sqlalchemy.engine import Engine

from src.analytical.pipeline import DEFAULT_ANALYTICAL_DIR, content_hash
from src.clean.lineage import rel
from src.clean.pipeline import DEFAULT_MANIFEST_DIR
from src.ingestion import sha256_file
from src.staging import read_lineage, read_parquet

from .compare import compare_with_gold
from .connection import get_engine
from .warehouse import LOAD_ORDER, LOAD_RUN, WAREHOUSE_SCHEMA, expected_tables


class GoldSourceError(Exception):
    """The Gold outputs are missing, inconsistent or do not match their manifest."""


class LoadValidationError(Exception):
    """The loaded data differs from Gold; the transaction was rolled back."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(f"{len(problems)} validation problem(s): " + "; ".join(problems[:5]))


@dataclass
class GoldSource:
    run_id: str
    manifest_path: Path
    manifest_sha256: str
    frames: dict[str, pd.DataFrame]
    content_hashes: dict[str, str]
    git: dict = field(default_factory=dict)

    @property
    def row_counts(self) -> dict[str, int]:
        return {n: len(f) for n, f in self.frames.items()}


# ----------------------------------------------------------------------------- 1. locate and verify Gold
def locate_gold(gold_dir: Path = DEFAULT_ANALYTICAL_DIR, manifest_dir: Path = DEFAULT_MANIFEST_DIR) -> GoldSource:
    gold_dir, manifest_dir = Path(gold_dir), Path(manifest_dir)
    missing = [n for n in LOAD_ORDER if not (gold_dir / f"{n}.parquet").is_file()]
    if missing:
        raise GoldSourceError(f"Gold outputs missing in {gold_dir}: {missing}. Run `python -m src.analytical` first.")
    run_ids = {n: read_lineage(gold_dir / f"{n}.parquet").get("run_id") for n in LOAD_ORDER}
    if len(set(run_ids.values())) != 1 or None in run_ids.values():
        raise GoldSourceError(f"Gold files do not come from one analytical run: {run_ids}")
    run_id = next(iter(run_ids.values()))
    manifest_path = manifest_dir / f"{run_id}.json"
    if not manifest_path.is_file():
        raise GoldSourceError(f"manifest {manifest_path} for Gold run {run_id} not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("layer") != "analytical" or manifest.get("success") is not True:
        raise GoldSourceError(f"manifest {manifest_path.name} is not a successful analytical run")
    outputs = {o["dataset"]: o for o in manifest["outputs"]}

    frames, hashes, problems = {}, {}, []
    for n in LOAD_ORDER:
        path = gold_dir / f"{n}.parquet"
        o = outputs.get(n)
        if o is None:
            problems.append(f"{n}: not in manifest")
            continue
        if sha256_file(path) != o["sha256"]:
            problems.append(f"{n}: file SHA-256 differs from the manifest")
        df = read_parquet(path)
        if content_hash(df) != o["content_sha256"] or len(df) != o["rows"]:
            problems.append(f"{n}: content hash or row count differs from the manifest")
        frames[n], hashes[n] = df, o["content_sha256"]
    problems += _schema_problems(frames)
    if problems:
        raise GoldSourceError("; ".join(problems))
    return GoldSource(run_id=run_id, manifest_path=manifest_path, manifest_sha256=sha256_file(manifest_path),
                      frames=frames, content_hashes=hashes, git=manifest.get("git") or {})


def _schema_problems(frames: dict[str, pd.DataFrame]) -> list[str]:
    """Columns must equal the warehouse design; money must be exact cents; date keys midnight."""
    out = []
    for n, e in expected_tables().items():
        if n == LOAD_RUN:
            continue
        df = frames[n]
        if list(df.columns) != [c for c, _, _ in e.columns]:
            out.append(f"{n}: Gold columns differ from the warehouse design")
            continue
        for c, pg, nullable in e.columns:
            s = df[c]
            if not nullable and s.isna().any():
                out.append(f"{n}.{c}: {int(s.isna().sum())} missing values in a NOT NULL column")
            if pg == "numeric(12,2)":
                v = s.dropna().to_numpy(dtype=float)
                if (np.abs(v * 100 - np.round(v * 100)) > 1e-6).any():
                    out.append(f"{n}.{c}: values finer than one cent (would be altered by numeric(12,2))")
            if pg == "date" and not (s.dropna() == s.dropna().dt.normalize()).all():
                out.append(f"{n}.{c}: non-midnight values in a date column")
    return out


# ----------------------------------------------------------------------------- 2-3. conversion and COPY
def column_values(s: pd.Series, pg: str) -> list:
    """Python values for one column; every missing value (NaN, NaT, NA) becomes None (SQL NULL)."""
    missing = s.isna().to_numpy()
    if pg == "numeric(12,2)":
        vals = [Decimal(f"{v:.2f}") for v in s.to_numpy(dtype=float, na_value=0.0)]
    elif pg == "date":
        vals = [t.date() for t in s.fillna(pd.Timestamp(0)).dt.to_pydatetime()]
    elif pg.startswith("timestamp"):
        vals = list(s.fillna(pd.Timestamp(0)).dt.to_pydatetime())
    elif pg == "integer[]":
        vals = [[int(x) for x in a] if a is not None else None for a in s]
    elif pg == "integer":
        vals = s.astype("Int64").fillna(0).astype("int64").tolist()
    elif pg == "double precision":
        vals = s.to_numpy(dtype=float, na_value=0.0).tolist()
    elif pg == "boolean":
        vals = s.astype("boolean").fillna(False).astype(bool).tolist()
    else:  # text
        vals = s.astype("string").fillna("").tolist()
    return [None if m else v for v, m in zip(vals, missing)]


def table_rows(df: pd.DataFrame, columns) -> list[tuple]:
    cols = [column_values(df[c], pg) for c, pg, _ in columns]
    return list(zip(*cols))


def copy_table(raw_conn, schema: str, table: str, df: pd.DataFrame) -> int:
    columns = expected_tables()[table].columns
    stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(schema), sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(c) for c, _, _ in columns))
    rows = table_rows(df, columns)
    with raw_conn.cursor() as cur, cur.copy(stmt) as copy:
        for row in rows:
            copy.write_row(row)
    return len(rows)


# ----------------------------------------------------------------------------- the transaction
def load_warehouse(gold: GoldSource | None = None, engine: Engine | None = None,
                   schema: str = WAREHOUSE_SCHEMA, check_baselines: bool = True) -> dict:
    """Atomically replace the warehouse contents with ``gold`` and record the load in ``load_run``."""
    gold = gold or locate_gold()
    own = engine is None
    engine = engine or get_engine()
    started = datetime.now(timezone.utc)
    load_id = uuid.uuid4().hex[:12]
    try:
        with engine.begin() as conn:  # COMMIT only if every statement and the validation succeed
            raw = conn.connection.dbapi_connection
            targets = sql.SQL(", ").join(sql.Identifier(schema, t) for t in LOAD_ORDER)
            raw.execute(sql.SQL("TRUNCATE {}").format(targets))
            loaded = {t: copy_table(raw, schema, t, gold.frames[t]) for t in LOAD_ORDER}
            validation = compare_with_gold(conn, gold.frames, schema, check_baselines=check_baselines)
            if not validation["ok"]:
                raise LoadValidationError(validation["problems"])
            finished = datetime.now(timezone.utc)
            raw.execute(sql.SQL("INSERT INTO {} (load_id, gold_run_id, gold_manifest_path, gold_manifest_sha256, "
                                "row_counts, content_hashes, started_at, finished_at) "
                                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)").format(sql.Identifier(schema, LOAD_RUN)),
                        (load_id, gold.run_id, rel(gold.manifest_path), gold.manifest_sha256,
                         json.dumps(gold.row_counts), json.dumps(gold.content_hashes), started, finished))
        return {"load_id": load_id, "gold_run_id": gold.run_id, "rows_loaded": loaded,
                "started_at": started, "finished_at": finished, "validation": validation}
    finally:
        if own:
            engine.dispose()
