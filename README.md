# skyward-common

Shared Python infrastructure for all Skyward projects. Provides config loading, BigQuery client, DataForSEO API client, Meta/DataHub data layer, multi-provider LLM abstraction, and Slack notifications.

## Installation

### From GitHub Packages (production)

```bash
uv pip install skyward-common --index-url https://YOUR_REGISTRY_URL
```

### Editable install (development)

```bash
git clone https://github.com/YOUR_ORG/skyward-common.git
cd skyward-common
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

## Quick Start

```python
import os

# Load config
from skyward.config import load_config
cfg = load_config()

# BigQuery (uses ADC if no credentials in .env)
from skyward.data.bigquery import BigQueryClient
bq = BigQueryClient(project_id=cfg.datahub_project_id, credentials_info=cfg.datahub_credentials)

# DataForSEO
from skyward.data.dataforseo import DataForSEOClient, ClientConfig
d4seo = DataForSEOClient(
    username=cfg.dataforseo_username,
    password=cfg.dataforseo_password,
)

# Meta tables / DataHub
from skyward.data.hub import DataHub
hub = DataHub(bq)
clients = hub.list_clients()

# LLM providers
from openai import OpenAI
from skyward.llm import get_provider, calculate_cost

provider = get_provider("openai", openai_client=OpenAI(api_key=cfg.openai_key))
result, in_tok, out_tok = provider.call_text(
    messages=[{"role": "user", "content": "Hello"}],
    model="gpt-4o",
)
print(f"Cost: {calculate_cost(in_tok, out_tok, 'gpt-4o', 'openai')}")

# Notifications
from skyward.notifications import send_slack
send_slack("general", "Pipeline complete!")
```

## Package Modules

| Module | Import | Description |
|--------|--------|-------------|
| **Config** | `from skyward.config import load_config` | Central `.env` loader, returns `Settings` dataclass |
| **BigQuery** | `from skyward.data.bigquery import BigQueryClient` | BQ wrapper with upload logging |
| **DataForSEO** | `from skyward.data.dataforseo import DataForSEOClient` | Multi-endpoint SEO data API client |
| **MetaClient** | `from skyward.data.meta import MetaClient` | CRUD for Meta tables (clients, domains, projects) |
| **DataHub** | `from skyward.data.hub import DataHub` | Extends MetaClient with data access and catalog |
| **LLM** | `from skyward.llm import get_provider, calculate_cost` | OpenAI, Gemini, Perplexity with cost tracking |
| **Notifications** | `from skyward.notifications import send_slack` | Slack webhook integration |
| **Utilities** | `from skyward.functions import get_domain, upload_df_to_google_sheets` | Shared helpers |

## Testing

```bash
python -m pytest tests/ -v
```

Tests use mock BQ fixtures — no real credentials needed.

## Configuration

### Google Cloud (BigQuery / Drive) — Application Default Credentials

For local development, authenticate once:

```bash
gcloud auth application-default login
```

This saves a refresh token to `~/.config/gcloud/application_default_credentials.json`. All Google client libraries find it automatically. It persists across reboots — you only need to run it once.

For CI or production, use a service account:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

Or set `GCP_DATAHUB_CREDENTIALS=secrets/your-sa.json` in `.env` (path relative to project root).

### Environment Variables

Copy `.env.example` to `.env` and fill in:

- `GCP_DATAHUB_PROJECT_ID` — BigQuery project ID (always required)
- `GCP_DATAHUB_CREDENTIALS` — leave empty for ADC, or path to service account JSON
- `DATAFORSEO_API_LOGIN` / `DATAFORSEO_API_PASSWORD` — DataForSEO API
- `OPENAI_API_KEY` — OpenAI
- `GEMINI_API_KEY` — Google Gemini
- `PERPLEXITY_API_KEY` — Perplexity
- `SLACK_WEBHOOK_*` — Slack webhooks

## Used By

- **skyward-seo** — SEO Pipeline, PAA Downloader, KGA, WQA
- **skyward-ai-faqs** — AI FAQ generation system
- **skyward-data-hub-admin** — Internal portal (FastAPI + Next.js)
