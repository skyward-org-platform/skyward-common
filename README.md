# skyward-common

Shared Python infrastructure for all Skyward projects. Provides config loading, BigQuery client, DataForSEO API client, Meta/DataHub data layer, multi-provider LLM abstraction with stateful sessions, CLI tools, and Slack notifications.

## Installation

### From GitHub Packages (production)

```bash
uv pip install skyward-common --index-url https://YOUR_REGISTRY_URL
```

### Editable install (development)

```bash
git clone https://github.com/skyward-org-platform/skyward-common.git
cd skyward-common
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

## Quick Start

```python
from skyward.config import load_config
cfg = load_config()

# BigQuery
from skyward.data.bigquery import BigQueryClient
bq = BigQueryClient(project_id=cfg.datahub_project_id, credentials_info=cfg.datahub_credentials)

# Meta tables / DataHub
from skyward.data.hub import DataHub
hub = DataHub(bq)
clients = hub.list_clients()

# LLM — one interface, any provider
from skyward.llm import get_provider, calculate_cost

provider = get_provider("openai")  # uses OPENAI_API_KEY from env
result, in_tok, out_tok = provider.call(
    messages=[{"role": "user", "content": "Hello"}],
    model="gpt-4o-mini",
)

# Same call, different provider — just change the name
provider = get_provider("anthropic")  # uses ANTHROPIC_API_KEY from env
result, in_tok, out_tok = provider.call(
    messages=[{"role": "user", "content": "Hello"}],
    model="claude-sonnet-4-20250514",
)
```

## DataForSEO

Class-based client with one lazily-initialized endpoint accessor per API family. All calls automatically stamp `job_id`, `upload_id`, `ingest_timestamp`, `domain_id`, `domain`, `task_id`, and `endpoint_mode` onto returned rows and (optionally) upload them to BigQuery.

```python
from skyward.config import load_config
from skyward.data.dataforseo import ClientConfig, DataForSEOClient
from skyward.functions import generate_job_id

cfg = load_config()
client = DataForSEOClient(
    username=cfg.dataforseo_username,
    password=cfg.dataforseo_password,
    config=ClientConfig(debug=True),
)

# Pre-flight balance check
balance = client.get_balance()
print(f"${balance['balance']:.2f} remaining (lifetime spent ${balance['total']:.2f})")

# Fetch keywords a domain ranks for (paginated automatically)
job_id = generate_job_id()
df = await client.dataforseo_labs_google_ranked_keywords.live_all(
    targets=["example.com"],
    domain="example.com",
    job_id=job_id,
    limit_per_domain=10000,  # caller-side cap; silently truncates if exceeded
    upload=True,             # appends to BigQuery + logs upload event
)
```

Key per-endpoint knobs:
- `batch_size` — items per API request (multi-item endpoints) OR concurrency pool size (single-target endpoints)
- `limit` (backlinks-backlinks) — max backlinks per target per call, default 100, API max 1000
- `limit_per_domain` / `page_size` (ranked_keywords) — how many keywords to pull and how many per paginated call
- `upload=True/False` — toggle BigQuery write-through

Standard (async) mode via `post_all()` is supported on `serp-google-organic` and `keywords_data-google_ads-search_volume`. All other endpoints are live-only.

## LLM Providers

All providers share the same `call()` interface:

```python
result, in_tok, out_tok = provider.call(
    messages=[...],
    model="model-name",
    response_model=MyPydanticModel,  # optional — omit for plain text
    temperature=0.7,                 # optional
    max_tokens=1000,                 # optional
    **provider_kwargs,               # forwarded to underlying SDK
)
```

| Provider | `get_provider()` name | Env var | Default model |
|----------|----------------------|---------|---------------|
| OpenAI | `"openai"` | `OPENAI_API_KEY` | gpt-4o-mini |
| Google Gemini | `"gemini"` | `GEMINI_API_KEY` | gemini-2.5-flash |
| Perplexity | `"perplexity"` | `PERPLEXITY_API_KEY` | sonar |
| Anthropic | `"anthropic"` | `ANTHROPIC_API_KEY` | claude-sonnet-4-20250514 |
| xAI (Grok) | `"grok"` | `XAI_API_KEY` | grok-3-mini |

### Structured Output

Pass a Pydantic model to get parsed responses:

```python
from pydantic import BaseModel

class CityInfo(BaseModel):
    city: str
    country: str
    population_millions: float

result, in_tok, out_tok = provider.call(
    messages=[{"role": "user", "content": "Tell me about Tokyo"}],
    model="gpt-4o-mini",
    response_model=CityInfo,
)
# result is a CityInfo instance
print(result.city)  # "Tokyo"
```

### Stateful Sessions

`LLMSession` wraps any provider with conversation history and automatic summarization:

```python
from skyward.llm import get_provider
from skyward.llm.session import LLMSession

provider = get_provider("openai")
session = LLMSession(
    provider,
    system_prompt="You are an SEO expert.",
    summarize_after_tokens=50_000,  # auto-compress after 50k tokens
)

session.send("Our client sells luxury watches.", model="gpt-4o-mini")
session.send("What keywords should we target?", model="gpt-4o-mini")
# ^ remembers the client context from the first message

# Check state
print(session.messages)           # full conversation history
print(session.total_input_tokens) # cumulative token usage
session.clear()                   # reset everything
```

Summarization modes:
- `summarize_after_tokens=50000` — compress when total tokens exceed threshold
- `summarize_after_messages=20` — compress when message count exceeds threshold
- `summarize_fn=my_function` — custom function that receives messages, returns compressed messages

## CLI

```bash
# Client management
skyward meta list-clients --counts
skyward meta add-client --name "Acme Corp"
skyward meta list-domains --client-id 1
skyward meta search-domains --query "example"

# Project management
skyward meta list-projects --client-id 1
skyward meta add-project --client-id 1 --type seo_pipeline --name "Q2 Audit"

# LLM calls
skyward llm call --provider openai --model gpt-4o-mini --message "What is SEO?"
skyward llm call --provider anthropic --model claude-sonnet-4-20250514 --message "What is SEO?"

# Interactive chat
skyward llm chat --provider openai --model gpt-4o-mini --system "You are an SEO expert."

# Cost estimation
skyward llm cost --provider openai --model gpt-4o-mini --input 5000 --output 2000
skyward llm estimate --provider openai --model gpt-4o-mini --items 500 --input-per 3000 --output-per 1000

# Upload logs
skyward bq search-uploads --client-id 1 --limit 10
```

## Package Modules

| Module | Import | Description |
|--------|--------|-------------|
| **Config** | `from skyward.config import load_config` | Central `.env` loader, returns `Settings` dataclass |
| **BigQuery** | `from skyward.data.bigquery import BigQueryClient` | BQ wrapper with upload logging |
| **DataForSEO** | `from skyward.data.dataforseo import DataForSEOClient` | Multi-endpoint SEO data API client |
| **MetaClient** | `from skyward.data.meta import MetaClient` | CRUD for Meta tables (clients, domains, projects) |
| **DataHub** | `from skyward.data.hub import DataHub` | Extends MetaClient with data access and catalog |
| **LLM** | `from skyward.llm import get_provider, LLMSession` | 5 providers with unified interface + sessions |
| **Costs** | `from skyward.llm import calculate_cost, format_cost` | Token cost tracking for all providers |
| **Notifications** | `from skyward.notifications import send_slack` | Slack webhook integration |
| **Utilities** | `from skyward.functions import get_domain, upload_df_to_google_sheets` | Shared helpers |

## Configuration

### Google Cloud — Application Default Credentials

For local development, authenticate once:

```bash
gcloud auth application-default login
```

For CI or production, use a service account:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

Or set `GCP_DATAHUB_CREDENTIALS=secrets/your-sa.json` in `.env`.

### Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|----------|----------|-------------|
| `GCP_DATAHUB_PROJECT_ID` | Yes | BigQuery project ID |
| `GCP_DATAHUB_CREDENTIALS` | No | Path to SA JSON (empty = use ADC) |
| `DATAFORSEO_API_LOGIN` | For DataForSEO | API login |
| `DATAFORSEO_API_PASSWORD` | For DataForSEO | API password |
| `OPENAI_API_KEY` | For OpenAI | OpenAI API key |
| `GEMINI_API_KEY` | For Gemini | Google Gemini API key |
| `PERPLEXITY_API_KEY` | For Perplexity | Perplexity API key |
| `ANTHROPIC_API_KEY` | For Anthropic | Anthropic (Claude) API key |
| `XAI_API_KEY` | For Grok | xAI (Grok) API key |
| `SLACK_WEBHOOK_*` | For notifications | Slack webhook URLs |

## Testing

```bash
uv run python -m pytest tests/ -v
```

Tests use mock BQ fixtures — no real credentials needed. Live LLM tests are gated behind API key env vars and skipped when keys aren't set.

## Used By

- **skyward-seo** — SEO Pipeline, PAA Downloader, KGA, WQA
- **skyward-ai-faqs** — AI FAQ generation system
- **skyward-data-hub-admin** — Internal portal (FastAPI + Next.js)
