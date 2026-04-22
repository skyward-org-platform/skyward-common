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

## Adding a new endpoint

1. Apply the rule above to generate all four names.
2. Create `src/skyward/data/dataforseo/endpoints/<snake_name>.py` with one
   class subclassing `BaseEndpoint` (see `endpoints/backlinks_summary.py`
   for a small canonical example).
3. Add a property on `DataForSEOClient` that lazy-imports the class.
4. Add one entry to `scripts/migrate_dataforseo_manifest.py` describing
   the BQ table schema (derived from the endpoint's `_get_schema()` and
   `_cast_types()`).
5. Run the migration script with `--only <new_table_name>` to create the
   BQ table.
6. Add a smoke test and a live test in `tests/live/test_dataforseo_live.py`.

## Non-canonical tables

Tables in the `DataForSEO` dataset that are NOT part of this convention
(documented for awareness, untouched by the Phase 1 migration):

- `chatgpt_llm_scraper` — not a DataForSEO endpoint; custom scraper data.
- `serp_enriched` — derived/enriched from `serp-google-organic`, not a raw API call.
- `serp_google_organic_live_advanced_backup` — historical backup, unclear origin.

These are open items for Paul to review.
