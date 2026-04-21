# DataForSEO QA Checklist

## How to use this doc

For each endpoint, we have 1–2 live tests in `tests/live/test_dataforseo_live.py`. The audit workflow per endpoint:

1. **Run** the live test(s): `uv run pytest tests/live/test_dataforseo_live.py::<test_name> -v --run-live`. The auto-capture fixture in `tests/live/conftest.py` dumps each HTTP response to `tests/live/responses/<test_name>__<slug>__<ts>_<n>.json`.
2. **Dispatch** a review agent to compare the raw JSON vs. the endpoint's `_parse_response` / `_get_schema` / `_cast_types` logic, and the row it produces vs. the BQ table schema in the migration manifest.
3. **Apply** any fixes (bugs, dropped fields, dead code, type mis-casts).
4. **Check off** the test in the checkbox list below. When all tests for an endpoint are checked, mark the endpoint **Signed off**.

**Progress so far:** 1 of 14 tests reviewed (backlinks_backlinks/live_small). 0 of 11 endpoints signed off.

---

## backlinks_backlinks

**BQ table:** `data-hub-468216.DataForSEO.backlinks-backlinks`

### Tests to review
- [x] `test_backlinks_backlinks_live_small` — reviewed 2026-04-21
- [ ] `test_backlinks_backlinks_live_all_3_targets`

### Review notes

**Bugs fixed (commit `cb49e5c`):**
1. Premature `json.dumps` on `domain_from_platform_type` / `indirect_link_path` — `json.dumps(None)` produces the literal string `"null"` in the row. `_cast_types.stringify_cols` already handles these correctly. Dropped the `json.dumps()` wrappers in `_parse_response`.
2. Dead `"domain": item.get("domain_to")` line — unconditionally overwritten by `_stamp_fetch_metadata` downstream. Deleted.
3. Missing `original` (bool) — the only per-item API boolean the parser dropped. Added to row dict, schema, and `bool_cols`.

**Fields intentionally skipped:**
- `tasks[0].result[0].target` — redundant; we stamp `url = target` on the row.
- `tasks[0].result[0].{mode, custom_mode}` — null in samples, no per-row use.
- `tasks[0].result[0].{total_count, items_count}` — request-level counters, not per-item.
- `tasks[0].result[0].search_after_token` — pagination cursor; not for per-row table.
- Top-level `cost`, `time`, `status_code`, `version`, task-level diagnostics — request metadata; skip.

**Follow-ups (open):**
- Consider capturing `tasks[0].result[0].total_count` in a summary table or upload log if we ever want to query "total dofollow backlinks per target."
- Type nit: `domain_from_platform_type` is stored as JSON-stringified text (e.g. `'["unknown"]'`). If we ever want to query platform types, a BQ `REPEATED STRING` field would be more ergonomic. Defer until there's a use case.

**Audit sanity checks (all passed):**
- Endpoint URL, `tasks[0].path`, status codes match `v3/backlinks/backlinks/live`.
- Every `_get_schema` column produced by `_parse_response` or metadata stamping chain.
- INT / BOOL / TIMESTAMP casts match live data shapes.
- All 7 metadata columns (`task_id`, `domain_id`, `domain`, `endpoint_mode`, `ingest_timestamp`, `upload_id`, `job_id`) stamped before upload.

### Endpoint signed off: ☐

---

## backlinks_bulk_pages_summary

**BQ table:** `data-hub-468216.DataForSEO.backlinks-bulk_pages_summary`

### Tests to review
- [ ] `test_backlinks_bulk_pages_summary_live_all`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## backlinks_summary

**BQ table:** `data-hub-468216.DataForSEO.backlinks-summary`

### Tests to review
- [ ] `test_backlinks_summary_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## serp_google_organic

**BQ table:** `data-hub-468216.DataForSEO.serp-google-organic`

### Tests to review
- [ ] `test_serp_google_organic_live_small` (live mode)
- [ ] `test_serp_google_organic_standard_post` (standard POST/GET mode)

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## dataforseo_labs_google_keyword_suggestions

**BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-keyword_suggestions`

### Tests to review
- [ ] `test_dataforseo_labs_google_keyword_suggestions_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## dataforseo_labs_google_related_keywords

**BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-related_keywords`

### Tests to review
- [ ] `test_dataforseo_labs_google_related_keywords_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## dataforseo_labs_google_ranked_keywords

**BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-ranked_keywords`

### Tests to review
- [ ] `test_dataforseo_labs_google_ranked_keywords_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## dataforseo_labs_google_keyword_overview

**BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-keyword_overview`

### Tests to review
- [ ] `test_dataforseo_labs_google_keyword_overview_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## dataforseo_labs_google_search_intent

**BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-search_intent`

### Tests to review
- [ ] `test_dataforseo_labs_google_search_intent_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## dataforseo_labs_google_domain_rank_overview

**BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-domain_rank_overview`

### Tests to review
- [ ] `test_dataforseo_labs_google_domain_rank_overview_live_small`

### Review notes
_(pending review)_

### Endpoint signed off: ☐

---

## keywords_data_google_ads_search_volume

**BQ table:** `data-hub-468216.DataForSEO.keywords_data-google_ads-search_volume`

### Tests to review
- [ ] `test_keywords_data_search_volume_live_small` (live mode)
- [ ] `test_keywords_data_search_volume_standard_post_all` (standard POST/GET mode)

### Review notes
_(pending review)_

### Endpoint signed off: ☐
