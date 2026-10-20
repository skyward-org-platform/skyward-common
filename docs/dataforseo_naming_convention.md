# DataForSEO Naming Convention

The rule, with an example and a full applied table. This is the canonical
reference for adding new endpoints or discussing table naming.

## Rule

Given a DataForSEO API path `v3/<folder1>/<folder2>/<folder3>/<mode>`:

| Layer          | Transform                                                 | Example (input: `v3/dataforseo_labs/google/domain_rank_overview/live`) |
|----------------|-----------------------------------------------------------|-----------------------------------------------------------------------|
| BQ dataset     | Fixed                                                     | `DataForSEO`                                                          |
| BQ table       | `<folder1>-<folder2>-<folder3>`, drop `<mode>` suffix     | `dataforseo_labs-google-domain_rank_overview`                         |
| Client property| BQ table name with hyphens → underscores                  | `client.dataforseo_labs_google_domain_rank_overview`                  |
| Python class   | PascalCase of the property                                | `DataforseoLabsGoogleDomainRankOverview`                              |

## Rules (in order of precedence)

1. **Use DataForSEO's canonical path verbatim.** No pluralizing, no shortening.
   `keywords_data` (plural) wins over `keyword_data` (singular).
2. **Drop the trailing mode** (`/live`, `/task_post`, `/advanced`, `/regular`)
   from the BQ table name. The table is the logical dataset; the mode is the
   fetch mechanism (tracked in the `endpoint_mode` column per row).
3. **Hyphens separate path segments** in BQ table names; **underscores stay
   inside segments**.
4. **Keep the full path prefix** — don't drop `dataforseo_labs` from some
   tables and not others.

## Full endpoint → table map

| DataForSEO API path                                 | BQ table                                              | Python class                                    |
|-----------------------------------------------------|-------------------------------------------------------|-------------------------------------------------|
| `backlinks/backlinks/live`                          | `backlinks-backlinks`                                 | `BacklinksBacklinks`                            |
| `backlinks/bulk_pages_summary/live`                 | `backlinks-bulk_pages_summary`                        | `BacklinksBulkPagesSummary`                     |
| `backlinks/summary/live`                            | `backlinks-summary`                                   | `BacklinksSummary`                              |
| `serp/google/organic/live/advanced`                 | `serp-google-organic`                                 | `SerpGoogleOrganic`                             |
| `dataforseo_labs/google/keyword_suggestions/live`   | `dataforseo_labs-google-keyword_suggestions`          | `DataforseoLabsGoogleKeywordSuggestions`        |
| `dataforseo_labs/google/related_keywords/live`      | `dataforseo_labs-google-related_keywords`             | `DataforseoLabsGoogleRelatedKeywords`           |
| `dataforseo_labs/google/ranked_keywords/live`       | `dataforseo_labs-google-ranked_keywords`              | `DataforseoLabsGoogleRankedKeywords`            |
| `dataforseo_labs/google/keyword_overview/live`      | `dataforseo_labs-google-keyword_overview`             | `DataforseoLabsGoogleKeywordOverview`           |
| `dataforseo_labs/google/search_intent/live`         | `dataforseo_labs-google-search_intent`                | `DataforseoLabsGoogleSearchIntent`              |
| `dataforseo_labs/google/domain_rank_overview/live`  | `dataforseo_labs-google-domain_rank_overview`         | `DataforseoLabsGoogleDomainRankOverview`        |
| `keywords_data/google_ads/search_volume/live`       | `keywords_data-google_ads-search_volume`              | `KeywordsDataGoogleAdsSearchVolume`             |
| `ai_optimization/chat_gpt/llm_scraper/live/advanced`| `ai_optimization-chat_gpt-llm_scraper`                | `AiOptimizationChatGptLlmScraper`               |

## Adding a new endpoint

1. Apply the rule above to generate all four names.
2. Create `src/skyward/data/dataforseo/endpoints/<snake_name>.py` with one
   class subclassing `BaseEndpoint` (see `endpoints/backlinks_summary.py`
   for a small canonical example).
3. Add a property on `DataForSEOClient` that lazy-imports the class.
4. Add one entry to `scripts/migrate_dataforseo_manifest.py` describing
   the BQ table schema (derived from the endpoint's `_get_schema()` and
   `_cast_types()`), and bump the count in `tests/test_migration_manifest.py`.
5. Create the BQ table from that entry (dry-run first). The upload path never
   creates tables.
6. Add a smoke test and a live test in `tests/live/test_dataforseo_live.py`.

Since v1.6.1 an endpoint also has to plug into cost tracking and the guards:

7. **Rate card.** Add a `Rate` per mode to `DEFAULT_RATES` in `cost_rates.py`, keyed by
   the module file name, and add the key to `tests/test_dfs_cost_rates.py`. Without a rate
   the estimate is $0 and the balance guard is off (a warning says so). Then re-seed with
   `scripts/seed_dfs_cost_estimates.py`.
8. **`plan()`.** Override it to return requests, billable items and worst-case rows.
9. **Location flag.** `location_flag` is chosen by key prefix in `base.py`. A new API
   family gets no location check unless it overrides the property and, if it has its own
   location list, adds a source to `locations.SOURCES`.
10. **Cost log payload keys.** `cost_log._TARGET_KEYS` lists the payload fields that are
    inputs, not price drivers. A new input field must be added there or its text is
    written into `cost_log.price_inputs`.
11. **Run lifecycle.** Go through `_start_run` and `run.run_unit` so cost rows, save
    windows, the balance guard and `job_runs` rows all happen. Pass the v1.6.1 arguments
    through: `balance_buffer`, `ignore_balance_check`, `ignore_location_check`,
    `upload_batch_rows`.
12. **Standard mode.** Set `POST_URL`, `READY_URL`, `GET_URL` and
    `COLLECTOR_ENDPOINT_KEY` (equal to the module name), make `_parse_response` work with
    no target, add the client attribute to `collector/allowlist.py`, and redeploy the
    collector VM.

## Non-canonical tables

Tables in the `DataForSEO` dataset that are NOT part of this convention
(documented for awareness, untouched by the Phase 1 migration):

- `chatgpt_llm_scraper` — not a DataForSEO endpoint; custom scraper data. Unrelated to
  the canonical `ai_optimization-chat_gpt-llm_scraper` table, despite the similar name.
- `serp_enriched` — derived/enriched from `serp-google-organic`, not a raw API call.
- `serp_google_organic_live_advanced_backup` — historical backup, unclear origin.

These are open items for Paul to review.
