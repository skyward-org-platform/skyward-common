# Changelog

All notable changes to `skyward-common` are recorded here, newest first. This file is the
index: each entry is short, and links to a fuller document under `docs/releases/` where one
exists. Versions follow semantic versioning and match the tag on the GitHub Release.

A version only reaches consumers when a **GitHub Release** is published. Tagging alone does
not publish anything (see `.github/workflows/publish.yml`, which triggers on
`release: [published]`).

## [1.6.1] - 2026-09-16

Full notes: [docs/releases/v1.6.1.md](docs/releases/v1.6.1.md)

DataForSEO cost tracking. Automatic, with no consumer code changes required beyond handling
two new fail-fast errors.

### Added
- **Cost log.** Every billed DataForSEO task streams a row into `DataForSEO.cost_log`, tied
  to `job_id`, the `upload_id` of the save window its rows landed in, and the DFS `task_id`.
  Read totals with `client.get_job_cost(job_id)`.
- **Cost estimates.** `endpoint.estimate_cost(targets, **kwargs)` returns a worst-case
  `max_usd` (list price + 10%) and an `avg_usd` from observed actuals.
- **Progress.** `client.get_job_progress(job_id)` reports status and percent complete per
  endpoint, readable from another process while the job runs.
- **Balance guard.** `InsufficientBalanceError` before a run starts and at every data save.
  Overridable with `ignore_balance_check=True`.
- **Location catalog.** `client.get_locations(...)` reads a cached catalog of every DFS
  location code, with per-endpoint-family support flags. `InvalidLocationError` guards the
  obvious mistakes.
- **Periodic saves.** Large runs save to BigQuery in windows sized by expected rows, so a
  crash loses at most one window. Override with `upload_batch_rows=`.
- A zero-cost marker row in `cost_log` when a billed but unparseable 2xx forces a retry, so
  spend that cannot be attributed is visible rather than silent.

### Fixed
- `keywords_data_google_ads_search_volume` and `ranked_keywords` now send `language_code`
  from config instead of always sending English. **This can change your results** if you
  relied on the old hardcoded default.
- Cost and job-run rows carry a stable BigQuery `insertId`, so a retry after a lost
  acknowledgement cannot double-count spend or corrupt run counts.
- A mid-run low-balance stop now reaches the caller instead of returning a normal result.
- A failing final save no longer overwrites the run's real cause of death, nor masks the
  original exception from the consumer.
- Writers that accepted rows after a run closed now refuse them loudly instead of losing
  them silently, and late cost rows are still written so spend is never under-counted.

### Changed
- A large run now emits **several** `upload_id` values rather than one, so anything keyed on
  "one upload_id per run" in `Logs.upload_events` changes shape. Lookups by `job_id` are
  unaffected.

### Known limits
See the "Known limits" section of [docs/releases/v1.6.1.md](docs/releases/v1.6.1.md). In
short: the balance guard protects a single run and reserves nothing, so concurrent runs on
one account can overdraw it between them; `cost_log` is best-effort attribution rather than a
billing ledger; and `backlinks_bulk_pages_summary` can exceed its estimate when fed
malformed URLs.

## [1.6.0] - tagged 2026-07, never published

The tag `v1.6.0` exists on the remote but **no GitHub Release was ever created for it**, so
it never reached GitHub Packages. Consumers went from 1.5.1 straight to 1.6.1, which means
everything below ships for the first time as part of 1.6.1.

### Added
- P0 schema migration: `meta.site` and `meta.data_access` namespaces, with read and write
  parity and a backfill from the tables they replace.
- `meta.country` and `meta.language` lookups, seeded from DataForSEO.
- Natural keys so the populate scripts are safely re-runnable.

### Fixed
- Collector: parallel `task_get` fetching with jittered backoff on rate limits.
- Collector: canonical writes flush on a smaller, env-tunable batch, with memory reported in
  the heartbeat.

## [1.5.1] - 2026-06-16

DataForSEO standard-mode collector, `LLMResult` for all LLM providers, and the site-to-site
competitor remodel. Consumer migration notes:
[docs/releases/v1.5.1-consumer-migration-checklist.md](docs/releases/v1.5.1-consumer-migration-checklist.md)

## Earlier versions

1.5.0 and earlier predate this changelog. See the
[GitHub Releases](https://github.com/skyward-org-platform/skyward-common/releases) page.
