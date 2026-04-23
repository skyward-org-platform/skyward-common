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

## `MetaClient.get_next_id()` — race condition on concurrent writes

**Status:** Outstanding — requires a design decision before fixing.

### The problem

`get_next_id(table, id_column)` allocates integer IDs for Meta tables by reading `MAX(id_column)` and returning `max + 1`. There is a gap between the SELECT and the downstream INSERT where another writer can read the same max and allocate colliding IDs.

```
Process A:  SELECT MAX(domain_id) → 47
Process A:  allocate 48, 49
Process A:  INSERT rows with domain_id = 48, 49
Process B:  SELECT MAX(domain_id) → 47 (A's rows not yet visible)
Process B:  allocate 48, 49
Process B:  INSERT rows with domain_id = 48, 49   ← DUPLICATE
```

Affected tables: `Meta.clients`, `Meta.domains`, `Meta.projects` — any table allocating IDs via `get_next_id`.

**Within a single call** it's safe — methods like `add_domains` read the max once, allocate all new IDs in Python, and do one bulk INSERT. The race is **between** concurrent callers (two admin-portal users at once, two scripts running in parallel, etc.).

### Root cause

BigQuery has no atomic `sequence` / "increment-and-get" primitive like Postgres. Any SELECT-MAX-then-INSERT pattern is racey unless writes are serialized at the application layer.

The `_max_ids` cache on `MetaClient` is unrelated — it's used by `get_max_id()` (zero-padding for display), not by `get_next_id()` for allocation.

### Severity

- `Meta.clients`, `Meta.projects` — low frequency. Collision is possible but unlikely in practice.
- `Meta.domains` — higher risk. Two admins adding domains via the portal, or two scripts running in parallel, can collide.

### Mitigations while unfixed

- Do not run multiple ID-allocating scripts against the same Meta table concurrently.
- For writes via the skyward-platform admin portal: the FastAPI backend is a single process, so serialization at that layer is feasible if the race becomes a real problem (wrap `get_next_id` + INSERT in an `asyncio.Lock`).
- Any new code that allocates IDs should do the bulk work in one call (allocate once, insert all rows) rather than looping `get_next_id` + INSERT per row.

### Fix options (not yet chosen)

1. **UUIDs instead of integer IDs** — eliminates the race. Big schema migration for every existing row and every downstream join.
2. **`MERGE` against a `Meta.id_counters` table** — atomic increment in a single statement. Still subject to BigQuery's eventual-consistency quirks for subsequent reads.
3. **Application-layer serialization** — `asyncio.Lock` in the portal; unhelpful for scripts or non-portal writers.
4. **Optimistic insert + collision-detect + retry** — requires a post-insert uniqueness check.

Pick one when it's time to fix. Until then, document and avoid concurrent writers.

---

## Removed: `Meta.company_domains` / `Meta.project_companies` references

**Status:** Resolved (in this PR).

An earlier schema used `Meta.company_domains` and `Meta.project_companies` (with a `companies` concept). Those tables were dropped during the clients/domains migration but references lingered in `DataHub.get_project_data`'s `use_domain_lookup=True` branch. The broken branch has been removed along with the `use_domain_lookup` and `role` parameters on `get_project_data`.

If you have downstream code that called `get_project_data(..., use_domain_lookup=True)` or `get_project_data(..., role=...)`, those calls need to be updated — the parameters no longer exist.
