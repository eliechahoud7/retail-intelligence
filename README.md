# Retail Intelligence — Customer, Sales & Demand Analytics

Analytics project on the Olist Brazilian E-Commerce dataset (9 CSV files).

## Project structure

```
retail-intelligence/
├── data/
│   ├── raw/            # 9 original Olist CSVs — read-only, never modified
│   ├── staging/        # Task 4.2 — typed Parquet copies + derived structures (not versioned)
│   └── processed/
│       ├── clean/      # Task 4.3 — flagged + standardised tables at source grain (not versioned)
│       └── _manifest/  # one JSON run manifest per clean run (not versioned)
├── notebooks/          # 01 inventory · 02 relationships · 03 data quality
├── reports/            # data_preparation_strategy.md, ingestion / staging / clean reports
├── src/
│   ├── ingestion/      # Task 4.1 — raw data ingestion layer
│   ├── staging/        # Task 4.2 — staging layer (Parquet)
│   └── clean/          # Task 4.3 — clean layer (Parquet)
├── tests/              # automated tests (unittest)
├── dashboards/
├── models/
└── requirements.txt
```

## Setup

The project uses Python 3.12 and the virtual environment in `.venv`:

```powershell
py -3.12 -m venv .venv                      # only if .venv does not exist yet
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

All commands below are run **from the project root** with the `.venv` interpreter. Activating the environment (`.venv\Scripts\Activate.ps1`) lets you type `python` instead of `.venv\Scripts\python.exe`.

## Raw data ingestion (Task 4.1)

The ingestion layer loads the nine raw CSV files read-only, applies an explicit per-column schema, verifies file integrity, and reports the structure of every dataset. It performs **no cleaning or transformation**. See `reports/data_preparation_strategy.md` for the layer design.

### Run

```powershell
.venv\Scripts\python.exe -m src.ingestion
```

Options:

| Option | Purpose |
|---|---|
| `--report-json PATH` | Also write a machine-readable report, e.g. `reports/ingestion/ingestion_report.json` |
| `--raw-dir PATH` | Read raw files from another directory (default `data/raw`) |
| `--skip-baseline-check` | Do not compare checksums with the approved baseline. Intended only for test fixtures or alternative data |

Exit code `0` means all nine datasets loaded and the raw files were verified unchanged. Exit code `1` means an ingestion error, which is printed to stderr.

### What a run does

1. Checks that all nine expected files exist, reporting every missing file at once.
2. Computes the SHA-256 checksum of each raw file and compares it with the approved baseline (strategy check V01).
3. Checks each file's header against the declared schema: exact column names, in order.
4. Loads each file with explicit dtypes (`src/ingestion/schema.py`):
   - identifiers, **zip-code prefixes** (leading zeros preserved), free text and date/time columns as `string`;
   - whole numbers as nullable `Int64`;
   - decimal amounts and coordinates as `float64`.
5. Recomputes the checksums to prove nothing was modified.
6. Prints, for every dataset: name, rows, columns, column names, dtypes and missing-value counts.

### Loading conventions

- Only **empty fields** are treated as missing. Literal text such as `NA` or `null` stays as text.
- The encoding is `utf-8-sig`. `product_category_name_translation.csv` begins with a UTF-8 byte-order mark, which is stripped on read; the file itself is not changed.
- Date parsing (strategy D-03) and renaming the misspelled `*_lenght` columns (D-05) are **staging** steps, so they are not done here.

### Use from Python

```python
from src.ingestion import ingest, profile_result

result = ingest()                      # raises an IngestionError subclass on any problem
orders = result.frames["orders"]
profiles = profile_result(result)
```

## Staging layer (Task 4.2)

The staging layer converts the nine ingested datasets into typed Parquet files with **the same rows and the same values**. It also builds three derived structures approved in the preparation strategy. Ingestion is reused, not duplicated: staging calls `src.ingestion.ingest()`.

### Run

```powershell
.venv\Scripts\python.exe -m src.ingestion      # optional: inspect raw data first
.venv\Scripts\python.exe -m src.staging
```

| Option | Purpose |
|---|---|
| `--out-dir PATH` | Parquet output directory (default `data/staging`) |
| `--report-dir PATH` | Report directory (default `reports/staging`) |
| `--raw-dir PATH` | Raw CSV directory (default `data/raw`) |
| `--skip-baseline-check` | Skip the baseline checksum and regression checks. Intended for test fixtures only |

Exit code `0` means every blocking and regression check passed and all outputs were written and verified. Exit code `1` means a failure. If a check fails before writing, **no Parquet files are written**, but the report is still produced.

### Outputs (`data/staging/`)

| File | Rows | Content |
|---|---:|---|
| `customers`, `geolocation`, `orders`, `order_items`, `payments`, `reviews`, `products`, `sellers`, `translation` `.parquet` | same as raw | Typed 1:1 copies of the raw files |
| `geolocation_rows.parquet` | 738,332 | One row per distinct full geolocation row, with `occurrence_count` |
| `geolocation_points.parquet` | 720,154 | One row per distinct (zip, lat, lng), with `raw_row_count`, `n_full_rows`, `n_city_spellings`, `n_states`, `dq_outside_brazil`. No centroids |
| `products_metadata_flags.parquet` | 32,951 | `product_id` plus 8 field-level and 3 row-level missingness flags (strategy 4.3) |

Each Parquet file carries lineage in its metadata: run ID, git commit, source file and its SHA-256, row counts, transformations and column renames. Read it with `src.staging.read_lineage(path)`.

### Transformations (strategy references)

- **Dates (D-03):** the 8 date/time columns are parsed with the explicit format `%Y-%m-%d %H:%M:%S` and stay timezone-naive. Any value that fails to parse is counted, and a non-zero count blocks the run.
- **Renames (D-05):** `product_name_lenght` → `product_name_length` and `product_description_lenght` → `product_description_length`. No other column is renamed.
- **Categories (3.3):** `order_status`, `payment_type` and the three `*_state` columns become `category`.
- **Unchanged:** zip prefixes stay 5-character strings and identifiers stay strings. Missing values are preserved, with nothing filled or dropped.

### Not in staging

These are deferred because the strategy places them elsewhere:
- ZIP centroids (`zip_lookup`);
- standardised `city_key` columns;
- order-item quantity aggregation (D-15, analytical layer);
- all other `dq_*` flags (clean layer).

### Report

`reports/staging/staging_report.md` and `staging_report.json` list, for every output:
- source dataset, file and SHA-256;
- input and output row counts;
- schema and missing values per column;
- the transformations applied.

They also record every check result (blocking, regression and warning), the execution timestamp and the git commit hash.

## Clean layer (Task 4.3)

The clean layer reads the **staging Parquet outputs**, never the raw CSVs. It writes each source table at its source grain: the same rows, in the same order, with every staged column unchanged. Standardised copies and `dq_*` data-quality flags are appended as new columns. It also builds two reference tables. Nothing is deleted, imputed, de-duplicated or aggregated (strategy P2–P4, D-06–D-12, D-16).

### Run

```powershell
.venv\Scripts\python.exe -m src.staging      # prerequisite: staging outputs in data/staging
.venv\Scripts\python.exe -m src.clean
```

| Option | Purpose |
|---|---|
| `--staging-dir PATH` | Staging Parquet directory (default `data/staging`) |
| `--out-dir PATH` | Clean Parquet output directory (default `data/processed/clean`) |
| `--manifest-dir PATH` | Run manifest directory (default `data/processed/_manifest`) |
| `--report-dir PATH` | Report directory (default `reports/clean`) |
| `--raw-dir PATH` | Raw CSV directory. Only checksummed, never parsed (default `data/raw`) |
| `--skip-baseline-check` | Skip the baseline checksum and regression checks. Intended for test fixtures only |

Exit code `0` means every blocking and regression check passed and all outputs were written and read back identically. Exit code `1` means a failure. The run is refused before writing if:
- staging is missing;
- staging was built from raw files that differ from the current ones (lineage SHA-256 check);
- a project override in `category_map` no longer matches the source translation.

### Outputs (`data/processed/clean/`)

| File | Rows | Added columns |
|---|---:|---|
| `customers` | 99,441 | `city_key`, `customer_state_std`, `lat_centroid`, `lng_centroid`, `dq_no_geolocation` |
| `sellers` | 3,095 | as customers (`seller_state_std`), plus `dq_city_malformed` |
| `geolocation` | 1,000,163 | `geolocation_city_std` (URL-encoded apostrophe decoded), `city_key`, `geolocation_state_std` |
| `orders` | 99,441 | delivered-order gap flags (D-06), chronology/status flags (D-08), `dq_same_person_same_second` (D-07) |
| `order_items` | 112,650 | `dq_shipping_limit_out_of_range`. Source grain `(order_id, order_item_id)` kept; quantity is derived in the analytical layer (D-15) |
| `payments` | 103,886 | `dq_payment_zero`, `dq_installments_zero` (the recorded 0 is kept, D-16) |
| `reviews` | 99,224 | `has_comment_title/message`, `dq_review_id_shared`, `dq_review_content_duplicate`, `dq_review_before_purchase`, `dq_review_before_delivery` |
| `products` | 32,951 | the 11 staging metadata flags (joined from `products_metadata_flags`, not recomputed), `dq_weight_zero` + `weight_g_valid`, `dq_density_suspicious`, `dq_dims_16x2x11`, `category_en_clean`, `category_reporting` |
| `translation` | 71 | none (corrections live in `category_map`) |
| `category_map` | 73 | Reference table: source and corrected English label per Portuguese category, with `is_added_by_project` and a `note` |
| `zip_lookup` | 19,015 | Reference table: one row per zip. Unweighted component-wise median centroid over the distinct in-Brazil points, point counts, `dq_zip_multi_state`, `dq_zip_no_valid_point` |

Every added column's dtype, source column(s), rule and strategy decision are declared in `src/clean/schema.py` (`ADDED_COLUMNS`). They are also embedded in each Parquet file's lineage metadata (`src.staging.read_lineage(path)`).

**Project-owned labels** are in `src/clean/reference.py`, versioned with change notes:
- the 4 English typo corrections;
- the 2 added translations, with provisional wording pending Q-05;
- the `uncategorized` reporting label, provisional, Q-05.

### Not in the clean layer

These are deferred to the analytical layer by the strategy:
- order-line quantity (D-15);
- order-level payment and item summaries and `dq_payment_items_mismatch` (D-13);
- delivery durations (D-08 item 3);
- the analysis window (Q-11);
- warnings W01 and W05.

### Lineage and reports

- `data/processed/_manifest/<run_id>.json` (D-14) contains:
  - run ID and timestamps;
  - git commit and a SHA-256 of `src/`;
  - Python and library versions and parameters;
  - the input registry: raw files and the staging files with their staging run ID;
  - processing steps with row counts;
  - the output registry: path, grain, rows, schema, file SHA-256, and a run-independent content SHA-256 that is identical across repeated runs.
- `reports/clean/clean_report.md` and `clean_report.json` list every output, its added columns, flag counts, schema and missing values, and every check result.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

- **Unit tests** use temporary fixture files. They cover missing files, header mismatches, parse failures, leading-zero preservation, missing-value handling, the byte-order mark, checksums and the CLI.
- **Clean-layer tests** (`tests/test_clean.py`) cover:
  - each flag rule, city/state standardisation, `category_map` and `zip_lookup` on small frames;
  - the full raw → staging → clean pipeline on fixtures: outputs, manifest, unchanged source columns, repeatability, blocking on stale staging, and CLI exit codes;
  - a real-data run into a temporary directory.
- **Integration tests** load the real raw data read-only. They compare row counts, dtypes, missing values, zip formats and checksums with the baselines measured in notebooks 01–03, and are skipped if `data/raw/` is absent.
