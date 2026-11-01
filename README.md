# Retail Intelligence — Customer, Sales & Demand Analytics

Analytics project on the Olist Brazilian E-Commerce dataset (9 CSV files).

## Project structure

```
retail-intelligence/
├── data/
│   ├── raw/            # 9 original Olist CSVs — read-only, never modified
│   └── processed/      # pipeline outputs (staging / clean / analytical) — later tasks
├── notebooks/          # 01 inventory · 02 relationships · 03 data quality
├── reports/            # data_preparation_strategy.md, ingestion reports
├── src/
│   └── ingestion/      # Task 4.1 — raw data ingestion layer
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

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

- **Unit tests** use temporary fixture files. They cover missing files, header mismatches, parse failures, leading-zero preservation, missing-value handling, the byte-order mark, checksums and the CLI.
- **Integration tests** load the real raw data read-only. They compare row counts, dtypes, missing values, zip formats and checksums with the baselines measured in notebooks 01–03, and are skipped if `data/raw/` is absent.
