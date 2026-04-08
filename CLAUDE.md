# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

`skyward-common` is a private Python package providing shared infrastructure for all Skyward projects. It contains config loading, BigQuery client, DataForSEO API client, Meta/DataHub data layer, multi-provider LLM abstraction, Slack notifications, and shared utilities.

Other Skyward repos (`skyward-seo`, `skyward-ai-faqs`, `skyward-data-hub-admin`) import from this package.

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

provider = get_provider("openai", openai_client=client)
result, in_tokens, out_tokens = provider.call_structured(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    response_model=MyPydanticModel,
    model="gpt-4o",
)
```

Supported providers: `openai`, `gemini`, `perplexity`.

## BigQuery Batch Operations

- **Reads:** Use `WHERE ... IN UNNEST(@params)` to pull all needed data in one call.
- **Writes (UPDATE):** Use a single `MERGE` / `UPDATE ... FROM` join instead of one UPDATE per row.
- **Writes (INSERT):** Use `load_table_from_dataframe()` with `WRITE_APPEND` — never one INSERT per row.

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
| `src/skyward/llm/` | Multi-provider LLM abstraction (OpenAI, Gemini, Perplexity) with cost tracking |
| `src/skyward/notifications/` | Slack webhook integration |
| `src/skyward/functions.py` | Shared utilities (Google Sheets upload, URL parsing, date helpers) |
| `scripts/` | One-time scripts (Meta table seeding, schema migrations) |
| `tests/` | Pytest suite using FakeBQClient fixtures |

## Meta Tables

The `Meta` dataset in BigQuery (`data-hub-468216.Meta`) stores client/domain/project relationships. `MetaClient` and `DataHub` are the Python interfaces.

**Tables:** `clients`, `domains`, `client_domains`, `projects`, `project_domains`, `client_datasets`, `table_catalog`

**ID convention:** All IDs are auto-incremented integers via `get_next_id()`.
