# dbt project (Task 5.4)

dbt adds documented, tested analytical models on top of the existing PostgreSQL warehouse. It does
**not** rebuild Gold: the eight warehouse tables (Python pipeline + loader) and the seven
`analytics` views (`src/database/views.py`) stay owned by the Python code and are read by dbt as
sources.

| Schema | Owner | dbt access |
|---|---|---|
| `warehouse`, `analytics` | Python pipeline | read only (protected in `macros/generate_schema_name.sql`) |
| `dbt_staging` | dbt | views |
| `dbt_intermediate` | dbt | views |
| `dbt_marts` | dbt | tables (full rebuild; the Gold load is a full refresh, W-13) |

## Setup (once)

dbt lives in its own environment so the project environment (`.venv`) and its tests are untouched:

```
py -3.12 -m venv .venv-dbt
.venv-dbt\Scripts\python.exe -m pip install -r requirements-dbt-lock.txt
```

## Running dbt

Always through the wrapper, from the project root:

```
.venv\Scripts\python.exe -m src.database dbt debug
.venv\Scripts\python.exe -m src.database dbt <any dbt arguments>
```

The wrapper reads the connection settings from `.env` / the environment (as every other project
command does), passes them to the dbt process only, connects as the application role
(`POSTGRES_USER`) and runs in this directory, so `target/` and `logs/` stay here (both git-ignored).
`profiles.yml` contains no credentials.

## Sources

`models/sources/` declares the existing PostgreSQL objects as dbt sources (read-only):

| File | Contents |
|---|---|
| `_warehouse__sources.yml` | source `warehouse`: the 8 Gold tables + `load_run` (grain, keys, 13 FKs, every column) |
| `_analytics__sources.yml` | source `analytics`: the 7 views of `src/database/sql/views/` (grain, every column) |
| `_sources__docs.md` | shared doc blocks: project overview, interpretation rules (Q-03, Q-04, Q-11, D-06/D-08) |

Source tests (primary keys, the composite keys via `tests/generic/unique_combination_of_columns.sql`,
foreign keys, view grains) restate what PostgreSQL and `verify-views` already enforce; they are
read-only SELECTs. No source freshness is configured (closed historical extract; reloads have no
schedule). Browse the documentation with `... dbt docs generate` then `... dbt docs serve`.

## Staging

`models/staging/` holds one thin model per warehouse source, materialised as a **view** in schema
**`dbt_staging`** (configured in `dbt_project.yml`):

| Model | Source |
|---|---|
| `stg_dim_customers` | `warehouse.dim_customers` |
| `stg_dim_dates` | `warehouse.dim_dates` |
| `stg_dim_products` | `warehouse.dim_products` |
| `stg_dim_sellers` | `warehouse.dim_sellers` |
| `stg_fact_orders` | `warehouse.fact_orders` |
| `stg_fact_order_lines` | `warehouse.fact_order_lines` |
| `stg_fact_payments` | `warehouse.fact_payments` |
| `stg_fact_reviews` | `warehouse.fact_reviews` |
| `stg_load_run` | `warehouse.load_run` |

Each model selects every source column **explicitly** (no `SELECT *`), in source order, with the
same names and types, and applies no filter, join, cast or business rule - the grain, keys and rows
are the source's. Downstream models read `stg_` models, not the sources. This is enforced by two
generic tests on every model (`tests/generic/`):

- `matches_source_columns` - same column names, order and exact PostgreSQL types as the source;
- `matches_source_rows` - same multiset of rows as the source (`EXCEPT ALL` both ways): nothing
  filtered, duplicated or changed.

Plus primary-key, composite-grain and seven foreign-key relationship tests
(`models/staging/_staging__models.yml`). Run with `... dbt build --select staging`.

## Rules

- Never create, replace or delete objects in `warehouse` or `analytics`.
- Reviews stay at review-and-order grain; no order-level review score (Q-04).
- Payment-based and item-based revenue are never combined (Q-03).
- Reviews and payments are never attributed to sellers.
- No incremental models; no re-implementation of the Gold build, the views or the SQL queries.
