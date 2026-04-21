# DataForSEO QA Checklist

## How to use this doc

After a live test run (`pytest tests/live/ -m live --run-live`), Layer B of
the QA approach requires walking through each endpoint one at a time and
comparing the **raw DataForSEO JSON response** (at `tests/live/responses/<endpoint>_*.json`)
to the **BQ table schema**. Any fields DataForSEO returns that we drop should
be either added to the schema or documented as intentionally-skipped.

**Rules:**
- One endpoint per review pass. Don't advance to the next until the current
  is signed off.
- Fill in each endpoint's section with the review timestamp, any schema
  additions, any intentional skips with reasons, and follow-up work.

---

## backlinks_backlinks

- **Raw response:** `tests/live/responses/backlinks-backlinks_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.backlinks-backlinks`
- **Review timestamp:** 2026-04-21
- **Fields added to schema:** None recommended as required. One optional candidate: `original` (bool) — see Follow-ups.
- **Fields intentionally skipped (with reason):**
  - **Per-item fields dropped from the parser:**
    - `original` (bool) — the only per-item API field the parser drops outright. Looks like an accidental omission rather than an intentional skip; the other per-item booleans (`is_new`, `is_lost`, `dofollow`, `is_broken`, `is_indirect_link`) are all captured. Reasonable to add for completeness. Sample values in live data: `true` on the first item, `false` on the others.
  - **Top-level response metadata not captured:**
    - `tasks[0].result[0].target` — redundant; we already stamp `url = target` in the row.
    - `tasks[0].result[0].mode`, `custom_mode` — null in both samples and not documented as useful per-row context.
    - `tasks[0].result[0].total_count`, `items_count` — request-level counters, not per-item facts; fine to skip.
    - `tasks[0].result[0].search_after_token` — pagination cursor; belongs in a pagination workflow, not a per-row table.
    - `tasks[0].cost`, `time`, `status_code`, `status_message`, top-level `cost`/`time`/`version` — request diagnostics; skip.
- **Follow-ups:**
  1. **Add `original` to the row dict, schema, and `bool_cols` in `_cast_types`.** Currently dropped; no reason to omit it.
  2. **Fix premature `json.dumps` in `_parse_response` for `domain_from_platform_type` and `indirect_link_path`.** The parser writes `json.dumps(item.get("domain_from_platform_type"))` and `json.dumps(item.get("indirect_link_path"))` at row-build time. When the API returns `null`, this produces the literal string `"null"` in the row instead of Python `None`, which will land in BQ as the string `"null"` (not SQL NULL). Worse, `_cast_types.stringify_cols` is already designed to handle these same two columns correctly (only stringifies if value is `list`/`dict`, passes `None` through). Recommendation: remove the `json.dumps(...)` wrappers in `_parse_response` and rely solely on the `stringify_cols` loop in `_cast_types`. This also matches how `attributes` is handled (raw pass-through in parse, stringify in cast). Live data confirms the bug matters: 4 of 5 items have `domain_from_platform_type = null` and `indirect_link_path = null`.
  3. **Dead line: `"domain": item.get("domain_to")` in `_parse_response`.** `_stamp_fetch_metadata` runs after `_parse_response` inside `live()` and unconditionally overwrites `df["domain"]` with the resolved target domain (or `pd.NA`). So the per-row `domain_to` assigned to the `domain` column is always clobbered. The column `domain_to` is already captured separately. Recommend dropping the `"domain": item.get("domain_to")` line to eliminate confusion — the domain column will still be stamped correctly by the base class.
  4. **Consider adding `tasks[0].result[0].total_count` to an auxiliary place (job log or a summary table) if the user ever wants to know "how many dofollow backlinks does the target have in total." It's returned per-query for free (13,205,228 in the live sample). Not a per-row concern, so it doesn't belong in this schema; noting for completeness.
  5. **Type nit (not a bug):** `domain_from_platform_type` is stored as JSON-stringified text (e.g. `'["unknown"]'`). BQ schema type is presumably STRING. That's fine for now, but if we ever want to query platform types, a `REPEATED STRING` field would be more ergonomic. Defer until there's a use case.
  6. **Response stability:** The two captured JSONs are byte-identical except for `tasks[0].id` and the `search_after_token` (which embeds the task id). DataForSEO returned the same cached result set for both runs — expected behavior, not an anomaly. Useful to know that re-runs of the test will dedupe cleanly on the `(url_from, backlink_to, item_type, anchor)` dedupe key.
- **Signed off:** ☐

**Audit details (for reference):**
- **Endpoint validity:** `LIVE_URL = "backlinks/backlinks/live"` matches the response's `tasks[0].path = ["v3", "backlinks", "backlinks", "live"]`. Top-level and task-level `status_code = 20000`, `status_message = "Ok."`, `tasks_error = 0`. `tasks[0].data` echoes `{api: "backlinks", function: "backlinks", target: "https://example.com", limit: 5, filters: [["dofollow", "=", true]]}` — matches `_build_payload`. No warnings, no missing `result`.
- **Schema ↔ row-dict coverage:** Every column in `_get_schema()` is produced by `_parse_response` (verified by direct comparison). No orphan columns, no orphan row-dict keys beyond `task_id` (stamped per the base class contract).
- **Type casts vs. live data:** INT cols all contain ints; BOOL cols all contain Python `True`/`False` (not string `"true"`); TS cols use DataForSEO's `YYYY-MM-DD HH:MM:SS +00:00` format which `pd.to_datetime(..., utc=True)` parses cleanly. No numeric-as-STRING or STRING-as-numeric mis-casts detected.
- **Metadata stamping chain:** `task_id` (parser) → `domain_id`, `domain`, `endpoint_mode` (`_stamp_fetch_metadata` via `live()`) → `ingest_timestamp`, `upload_id`, `job_id` (`upload()`). All seven BQ metadata columns are produced before `load_table_from_dataframe`.

---

## backlinks_bulk_pages_summary

- **Raw response:** `tests/live/responses/backlinks-bulk_pages_summary_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.backlinks-bulk_pages_summary`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## backlinks_summary

- **Raw response:** `tests/live/responses/backlinks-summary_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.backlinks-summary`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## serp_google_organic

- **Raw response:** `tests/live/responses/serp-google-organic_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.serp-google-organic`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## dataforseo_labs_google_keyword_suggestions

- **Raw response:** `tests/live/responses/dataforseo_labs-google-keyword_suggestions_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-keyword_suggestions`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## dataforseo_labs_google_related_keywords

- **Raw response:** `tests/live/responses/dataforseo_labs-google-related_keywords_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-related_keywords`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## dataforseo_labs_google_ranked_keywords

- **Raw response:** `tests/live/responses/dataforseo_labs-google-ranked_keywords_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-ranked_keywords`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## dataforseo_labs_google_keyword_overview

- **Raw response:** `tests/live/responses/dataforseo_labs-google-keyword_overview_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-keyword_overview`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## dataforseo_labs_google_search_intent

- **Raw response:** `tests/live/responses/dataforseo_labs-google-search_intent_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-search_intent`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## dataforseo_labs_google_domain_rank_overview

- **Raw response:** `tests/live/responses/dataforseo_labs-google-domain_rank_overview_live_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.dataforseo_labs-google-domain_rank_overview`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

---

## keywords_data_google_ads_search_volume

- **Raw response:** `tests/live/responses/keywords_data-google_ads-search_volume_{live,standard}_*.json`
- **BQ table:** `data-hub-468216.DataForSEO.keywords_data-google_ads-search_volume`
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐
