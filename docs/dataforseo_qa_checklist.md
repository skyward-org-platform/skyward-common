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
- **Review timestamp:**
- **Fields added to schema:**
- **Fields intentionally skipped (with reason):**
- **Follow-ups:**
- **Signed off:** ☐

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
