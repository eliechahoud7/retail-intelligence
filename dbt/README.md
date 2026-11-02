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

## Rules

- Never create, replace or delete objects in `warehouse` or `analytics`.
- Reviews stay at review-and-order grain; no order-level review score (Q-04).
- Payment-based and item-based revenue are never combined (Q-03).
- Reviews and payments are never attributed to sellers.
- No incremental models; no re-implementation of the Gold build, the views or the SQL queries.
