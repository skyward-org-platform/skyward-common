# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

`skyward-common` is a private Python package providing shared infrastructure for all Skyward projects. It contains config loading, BigQuery client, DataForSEO API client, Meta/DataHub data layer, multi-provider LLM abstraction, Slack notifications, and shared utilities.

Other Skyward repos (`skyward-seo-pipeline`, `skyward-platform`) import from this package.

## Setup

This project uses **UV** for dependency management.

```bash
# Create venv and install in editable mode
uv venv .venv
source .venv/bin/activate        # Linux / Mac
.venv\Scripts\activate           # Windows CMD
uv pip install -e .
```

**Adding new packages:**
```bash
uv pip install <package>
```
Then add the package to `pyproject.toml` under `[project] dependencies` with a minimum version floor (e.g., `pandas>=2.0.0`). Do NOT use exact pins (`==`).

## Package Structure

All imports use the `skyward` namespace:

```python
from skyward.config import load_config, Settings
from skyward.data.bigquery import BigQueryClient
from skyward.data.dataforseo import DataForSEOClient, ClientConfig
from skyward.data.meta import MetaClient
from skyward.data.hub import DataHub
from skyward.llm import get_provider, calculate_cost, format_cost
from skyward.notifications import send_slack
from skyward.functions import upload_df_to_google_sheets, get_domain, get_path
```

Source layout: `src/skyward/` (standard Python package `src` layout).

## Configuration

Credentials come from environment variables. For local dev, a `.env` file is loaded automatically if found. Load in code:

```python
from skyward.config import load_config
cfg = load_config()
```

**Google Cloud auth:** For local dev, run `gcloud auth application-default login` once. Leave `GCP_DATAHUB_CREDENTIALS` empty in `.env` to use ADC. For CI/production, set the path to a service account JSON file.

## Rules

- ALWAYS use the DataForSEO client at `skyward.data.dataforseo` for all API calls. There is no legacy client in this repo.
- ALWAYS use `BigQueryClient` (`skyward.data.bigquery`) for all data warehouse operations.
- ALWAYS log uploads via `bq_client.log_upload_event()` after successful data insertion.
- ALWAYS use `skyward.llm` providers for LLM calls — never raw API clients directly.
- ALWAYS return `(result, input_tokens, output_tokens)` from LLM calls for token tracking.
- ALWAYS use batch reads and writes with BigQuery. NEVER issue individual DML statements per row in a loop.

## LLM Call Pattern

All LLM calls must use the provider abstraction with retry logic and structured outputs:

```python
from skyward.llm import get_provider

provider = get_provider("openai")  # reads OPENAI_API_KEY from env
result, in_tokens, out_tokens = provider.call(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    model="gpt-4o",
    response_model=MyPydanticModel,  # optional — omit for plain text
)
```

Supported providers: `openai`, `gemini`, `perplexity`, `anthropic`, `grok`. All share the same `call()` signature.

## BigQuery Batch Operations

- **Reads:** Use `WHERE ... IN UNNEST(@params)` to pull all needed data in one call.
- **Writes (UPDATE):** Use a single `MERGE` / `UPDATE ... FROM` join instead of one UPDATE per row.
- **Writes (INSERT):** Use `load_table_from_dataframe()` with `WRITE_APPEND` — never one INSERT per row.

## DataForSEO

All 11 DataForSEO endpoint tables in `data-hub-468216.DataForSEO` now use the standardized schema (7-column metadata block: `job_id`, `upload_id`, `ingest_timestamp`, `domain_id`, `domain`, `task_id`, `endpoint_mode`, then endpoint-specific columns). Historical rows were migrated and metadata backfilled; see `scripts/migrate_dataforseo_tables.py` and related scripts in `scripts/` for the one-time migration tools.

**Key knobs when calling endpoints:**

- `batch_size` — (1) for multi-item endpoints like `keyword_overview`, `search_intent`, `search_volume`, `serp-google-organic`, `bulk_pages_summary`: items per API request. (2) For single-target endpoints like `backlinks-backlinks`, `backlinks-summary`, `ranked_keywords.live_all([domains])`, `keyword_suggestions`, `related_keywords`: `ThreadPoolExecutor(max_workers=batch_size)` — controls concurrency, not request size.
- `limit` (backlinks-backlinks) — max backlinks per target per call. Default 100, API max 1000. No `offset` pagination implemented; callers must raise `limit` if they need more than 100 (and cannot exceed 1000 per call).
- `limit_per_domain` (ranked_keywords) — caller-side cap on total keywords pulled across paginated calls. Default 10000. Silently truncates when exceeded — no warning, no error.
- `page_size` (ranked_keywords) — keywords per paginated API call. Default 1000 (API max). Lower values force more pagination loops — useful for testing.
- Default filter on `backlinks-backlinks` is `[["dofollow", "=", True]]` — nofollow backlinks excluded unless caller passes `filters=[]` or custom filters.

**Account balance check** — `DataForSEOClient.get_balance()` returns `{balance, total, raw}`. Use it as a pre-flight guard before expensive fetches.

**Standard (POST/GET) mode** — only `serp-google-organic` and `keywords_data-google_ads-search_volume` support the async `task_post` + `task_get` workflow via `post_all()`. All other endpoints are live-only.

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_llm_costs.py -v

# Run a single test
python -m pytest tests/test_meta_edge_cases.py::TestCleanDomain::test_https_prefix -v
```

Tests use `FakeBQClient` fixtures in `tests/conftest.py` — no real BQ connection needed.

## Publishing

This package is published to GitHub Packages. To publish a new version:

1. Update the version in `pyproject.toml` and `src/skyward/__init__.py`
2. Commit and push
3. Create a GitHub Release with a tag (e.g., `v1.1.0`)
4. The GitHub Action builds and publishes automatically

## Directory Map

| Path | Description |
|------|-------------|
| `src/skyward/config/` | Central config loader (`load_config()`, `Settings` dataclass) |
| `src/skyward/data/bigquery/` | BigQuery client wrapper with upload logging |
| `src/skyward/data/dataforseo/` | DataForSEO API client (class-based, lazy endpoints) |
| `src/skyward/data/meta/` | MetaClient — CRUD for Meta tables (clients, domains, projects, datasets) |
| `src/skyward/data/hub/` | DataHub — extends MetaClient with data access and catalog management |
| `src/skyward/llm/` | Multi-provider LLM abstraction (OpenAI, Gemini, Perplexity, Anthropic, Grok) with cost tracking |
| `src/skyward/notifications/` | Slack webhook integration |
| `src/skyward/functions.py` | Shared utilities (Google Sheets upload, URL parsing, date helpers) |
| `scripts/` | One-time scripts (Meta table seeding, DataForSEO schema migrations, live QA driver) |
| `tests/` | Pytest suite using FakeBQClient fixtures |

## Meta Tables

The `Meta` dataset in BigQuery (`data-hub-468216.Meta`) stores client/domain/project relationships. `MetaClient` and `DataHub` are the Python interfaces.

**Tables:** `clients`, `domains`, `client_domains`, `projects`, `project_domains`, `client_datasets`, `dataset_catalog`, `table_catalog`

**ID convention:** All IDs are auto-incremented integers via `get_next_id()`.

## Known Issues

- `ranked_keywords._fetch_domain_keywords` triggers a pandas `FutureWarning` on `pd.concat` when pagination appends empty/all-NA result frames. Cosmetic only — final DataFrame is correct. Fix by filtering empty frames out of `results` before the concat.
