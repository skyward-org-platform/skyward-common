# DataForSEO QA Checklist

## How to use this doc

For each endpoint, we have 1–2 live tests in `tests/live/test_dataforseo_live.py`. The audit workflow per endpoint:

1. **Run** the live test(s): `uv run pytest tests/live/test_dataforseo_live.py::<test_name> -v --run-live`. The auto-capture fixture in `tests/live/conftest.py` dumps each HTTP response to `tests/live/responses/<test_name>__<slug>__<ts>_<n>.json`.
2. **Dispatch** a review agent to compare the raw JSON vs. the endpoint's `_parse_response` / `_get_schema` / `_cast_types` logic, and the row it produces vs. the BQ table schema in the migration manifest.
3. **Apply** any fixes (bugs, dropped fields, dead code, type mis-casts).
4. **Check off** the test in the checkbox list below. When all tests for an endpoint are checked, mark the endpoint **Signed off**.

**Progress so far:** 2 of 14 tests reviewed (backlinks_backlinks/live_small, backlinks_backlinks/live_all_3_targets). 0 of 11 endpoints signed off.

---

## backlinks_backlinks

**BQ table:** `data-hub-468216.DataForSEO.backlinks-backlinks`

### Tests to review
- [x] `test_backlinks_backlinks_live_small` — reviewed 2026-04-21
- [x] `test_backlinks_backlinks_live_all_3_targets` — reviewed 2026-04-21

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

**live_all additional findings (2026-04-21):**

No additional issues beyond what the `live_small` audit already flagged; `cb49e5c` fixes verified on all 3 targets' responses.

Detail:
- **`cb49e5c` fixes confirmed.** Ran each of the 3 responses through `_parse_response` + `_cast_types` in a REPL:
  - `original` column present, pre-cast dtype `bool`, post-cast `boolean`. Mixed True/False values across responses (example.com: 1/5 True; example.org: 1/5 True; example.net: 0/5 True) — not all-null, so the column is pulling its weight.
  - `domain_from_platform_type` stays a Python `list | None` in the parsed row (no premature `json.dumps`); `_cast_types.stringify_cols` correctly converts lists to JSON text (`'["unknown"]'`, `'["cms", "blogs"]'`) and leaves `None` alone.
  - `indirect_link_path` behaves the same — list of dicts when populated (1 row in response 001, 1 row in response 002), `None` otherwise.
  - `"domain"` key is NOT in the parsed row dict; dead line successfully removed. `_stamp_fetch_metadata` will own this column downstream.
- **Batch orchestration sanity.** The 3 responses represent 3 independent API calls: distinct `tasks[0].id` values (`6b49c4f340e9`, `e90a74715a58`, `861b61d77b20`), distinct `data.target` values, all `status_code: 20000` / `"Ok."`. `_fetch_live` is called per-target by `live_all` through the `ThreadPoolExecutor`, each yields its own df, then `pd.concat` + `_stamp_fetch_metadata` gives every row `domain="example.com"` / `domain_id=<seeded>` (test passes `domain=SEEDED_TEST_DOMAIN`). This is the intended behavior: `domain` is the *caller's* context, not the per-row `domain_to`.
- **Cross-target consistency.** Schema is uniform across all 3 responses — same key set, same shapes, same null patterns. No target-specific fields appeared in one response but not others. `example.com`'s response in this test is byte-identical to yesterday's `live_small` response for every per-item field; only `tasks[0].id`, top-level `time`, and `search_after_token` differ (as expected for a fresh API call).
- **New field shapes exercised (not seen in `live_small`).**
  - `domain_from_platform_type` with **multiple** values: `["cms", "blogs"]` (example.net) — the `json.dumps` stringification handles it fine.
  - `domain_from_platform_type = ["organization"]` (example.org) — a platform-type token not seen in the single-target audit; stringifies correctly.
  - `attributes = ["noopener"]` / `["external"]` (example.org, example.net) — previously always null in live_small; same `stringify_cols` path applies. No regression.
  - `item_type = "image"` with populated `image_url` + non-null `alt` (example.net row 3) — previously all `item_type` in live_small were `canonical`/`redirect`/`anchor`. Parser handles `image_url`/`alt` as plain string passthrough; no casts needed. Good.
  - `semantic_location` values include `section`, `details` (new); both pass through as strings.
- **Response-time variance.** Per-task `time` spans 0.0095s (example.com) → 1.5005s (example.net), a ~150x spread. Not a bug, just worth noting the ThreadPoolExecutor handles the straggler fine — total wall time is bounded by the slowest target, not the sum.
- **`total_count` spread.** `example.com=13,205,228`, `example.org=49,715`, `example.net=2,378`. We intentionally skip capturing this (see "Fields intentionally skipped"); follow-up still stands to reconsider if analysts start asking about this.
- **Dedupe.** `_get_dedupe_keys = ["url_from", "backlink_to", "item_type", "anchor"]`. Across the 15 total rows in this test the 3 targets have distinct `backlink_to` hosts, so no cross-target collisions are possible. Within `example.net`, rows 2/4/5 share `domain_from=ifingerstudio.com` with different `url_from` paths → still distinct rows on `url_from`. No duplicates, no anomalies. Intended behavior confirmed.
- **Anomalies: none.** No status_message warnings, no partial failures, no `tasks_error > 0`. All 3 tasks returned 5 items each as requested by `limit=5`.

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
