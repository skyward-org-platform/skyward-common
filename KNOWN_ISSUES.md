# Known Issues

Open issues in `skyward-common` that callers should be aware of. Each section describes the problem, the affected surface, and a recommended workaround.

---

## `Logs.upload_events` — `client_id` and `project_id` not reliably populated

**Status:** Outstanding — requires pipeline-level fix.

### The problem

The `Logs.upload_events` table has `client_id` and `project_id` columns (both nullable), but the upload pipelines do not consistently populate them. As a result, rows in the log can have:

- `client_id` set, `project_id` set
- `client_id` set, `project_id` null
- `client_id` null, `project_id` null
- (and any other combination)

There is no guarantee that *any* given upload carries a correct `client_id` or `project_id`. **Treat both fields as best-effort.**

### Root cause

The upload pipelines (scripts that call `BigQueryClient.log_upload_event()`) are inconsistent about passing `client_id` / `project_id`. Some pipelines don't have that context at ingest time; others have it but don't thread it through. This is a pipeline-layer issue, not a schema issue.

### Fix scope (future work)

- Thread `client_id` / `project_id` through every upload pipeline so every event carries the correct values.
- Backfill historical rows where the value can be recovered via domain lookup or job_id → project mapping.
- Once the pipeline side is fixed, lift the soft-deprecations listed below.

### Affected DataHub methods

These methods filter `Logs.upload_events` by `client_id` or `project_id` and will therefore miss any upload where the field is null. Their docstrings carry warnings matching this issue.

| Method | Unreliable because |
|---|---|
| `search_uploads(client_id=..., project_id=...)` | Filters `upload_events` by those fields |
| `get_upload_summary(client_id)` | Filters `upload_events` by `client_id` |
| `get_client_data(..., use_domain_lookup=False)` *(default path)* | Joins through `upload_events` filtered by `client_id` |
| `get_project_data(...)` | Joins through `upload_events` filtered by `project_id` |
| `get_available_datasets(client_id=...)` *(when filter used)* | Filters `upload_events` by `client_id` |
| `get_available_tables(client_id=...)` *(when filter used)* | Filters `upload_events` by `client_id` |

### What still works

- `search_uploads(...)` without the `client_id` / `project_id` filters — filtering by `job_id`, `upload_id`, `dataset`, `table`, or `since` is reliable.
- `preview_upload(upload_id)` — keyed on `upload_id`, which is required on every row.
- `get_client_data(..., use_domain_lookup=True)` — for tables in `DataHub.DOMAIN_TABLES`, this path goes through `Meta.domains` + `Meta.client_domains` (both reliable) and does not depend on `upload_events` at all.
- `list_tables()`, `reindex_catalog()` — read `Meta.table_catalog` / `INFORMATION_SCHEMA`, no dependency on the log.
- `get_ga4_datasets()`, `get_gsc_datasets()` — no dependency on the log.
- Everything inherited from `MetaClient` — reads the real `Meta.*` tables.

### Recommended workaround

- When you need a complete client-scoped view of data in a domain-based table (ranked_keywords, backlinks_*), use `get_client_data(..., use_domain_lookup=True)`.
- For tables without a `domain` column, there is currently no reliable way to scope by client or project through DataHub. Query the table directly if you have the job_ids, or wait for the pipeline fix.

---

## Removed: `Meta.company_domains` / `Meta.project_companies` references

**Status:** Resolved (in this PR).

An earlier schema used `Meta.company_domains` and `Meta.project_companies` (with a `companies` concept). Those tables were dropped during the clients/domains migration but references lingered in `DataHub.get_project_data`'s `use_domain_lookup=True` branch. The broken branch has been removed along with the `use_domain_lookup` and `role` parameters on `get_project_data`.

If you have downstream code that called `get_project_data(..., use_domain_lookup=True)` or `get_project_data(..., role=...)`, those calls need to be updated — the parameters no longer exist.
