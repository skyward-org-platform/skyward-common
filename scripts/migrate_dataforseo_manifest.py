"""Declarative migration manifest for the DataForSEO table rename + standardization.

Each Migration entry tells scripts/migrate_dataforseo_tables.py what to do for
one logical table. Three shapes:

1. Rename: old_name is set; new_name differs. Backs up old, creates new,
   copies data, drops old.
2. In-place schema update: old_name == new_name. Backs up, ALTER TABLE ADD
   COLUMN for new metadata columns.
3. New table: old_name is None. Just creates the new table.

Historical-row defaults when copying old data:
- endpoint_mode = "live" (all legacy rows were live calls)
- task_id = NULL (not captured historically)
- domain_id = NULL (can't retroactively resolve)
- domain = preserved if old table has `domain` column, else NULL
"""

from __future__ import annotations

from dataclasses import dataclass


BACKUP_SUFFIX = "_backup_04-20-2026"


@dataclass(frozen=True)
class Migration:
    """One table's migration config."""

    new_name: str
    new_schema: str  # SQL column list (starts with METADATA_BLOCK_SQL, then endpoint cols)
    old_name: str | None = None
    drop_project_id: bool = False
    preserve_domain_column: bool = True
    clustering_fields: tuple[str, ...] = ("domain_id", "job_id")
    partition_field: str = "ingest_timestamp"


# The metadata block that every new table starts with.
METADATA_BLOCK_SQL = """\
  job_id STRING NOT NULL,
  upload_id STRING NOT NULL,
  ingest_timestamp TIMESTAMP NOT NULL,
  domain_id INT64,
  domain STRING,
  task_id STRING,
  endpoint_mode STRING NOT NULL,
"""


# -----------------------------------------------------------------------------
# Per-endpoint column definitions.
# Types are derived from each endpoint's _cast_types() method. Columns not
# explicitly cast default to STRING. `task_id` and (where it would collide)
# `domain` are excluded from endpoint-specific cols because they live in the
# metadata block.
# -----------------------------------------------------------------------------

BACKLINKS_BACKLINKS_COLS = """\
  url STRING, domain STRING, type STRING, item_type STRING, attributes STRING,
  domain_from STRING, url_from STRING, url_from_https BOOL, tld_from STRING,
  domain_from_rank INT64, domain_from_platform_type STRING, domain_from_is_ip BOOL,
  domain_from_ip STRING, domain_from_country STRING, domain_to STRING,
  backlink_to STRING, backlink_to_https BOOL, backlink_to_status_code INT64,
  backlink_to_spam_score INT64, backlink_to_redirect_target STRING,
  dofollow BOOL, backlink_spam_score INT64, is_broken BOOL, is_indirect_link BOOL,
  indirect_link_path STRING, anchor STRING, alt STRING, image_url STRING,
  text_pre STRING, text_post STRING, semantic_location STRING,
  first_seen TIMESTAMP, prev_seen TIMESTAMP, last_seen TIMESTAMP,
  is_new BOOL, is_lost BOOL, rank INT64, page_from_rank INT64,
  page_from_keywords_count_top_3 INT64, page_from_keywords_count_top_10 INT64,
  page_from_keywords_count_top_100 INT64, page_from_title STRING,
  page_from_status_code INT64, page_from_external_links INT64,
  page_from_internal_links INT64, page_from_size INT64,
  page_from_encoding STRING, page_from_language STRING,
  links_count INT64, group_count INT64
"""

BACKLINKS_BULK_PAGES_SUMMARY_COLS = """\
  url STRING, backlinks INT64,
  referring_domains INT64, referring_domains_nofollow INT64,
  referring_main_domains INT64, referring_main_domains_nofollow INT64,
  rank INT64, main_domain_rank INT64,
  spam_score FLOAT64, referring_ips INT64, referring_subnets INT64,
  referring_pages INT64, referring_pages_nofollow INT64,
  dofollow INT64, nofollow INT64, referring_links_attributes STRING,
  broken_backlinks INT64, broken_pages INT64,
  first_seen TIMESTAMP, lost_date TIMESTAMP
"""

BACKLINKS_SUMMARY_COLS = """\
  target STRING, rank INT64, backlinks INT64, backlinks_spam_score INT64,
  target_spam_score INT64, crawled_pages INT64,
  referring_domains INT64, referring_domains_nofollow INT64,
  referring_main_domains INT64, referring_main_domains_nofollow INT64,
  referring_ips INT64, referring_subnets INT64,
  referring_pages INT64, referring_pages_nofollow INT64,
  referring_links_tld STRING, referring_links_types STRING,
  referring_links_attributes STRING, referring_links_platform_types STRING,
  referring_links_semantic_locations STRING, referring_links_countries STRING,
  internal_links_count INT64, external_links_count INT64,
  broken_backlinks INT64, broken_pages INT64,
  first_seen TIMESTAMP, lost_date TIMESTAMP
"""

SERP_GOOGLE_ORGANIC_COLS = """\
  keyword STRING, serp_datetime TIMESTAMP, se_domain STRING,
  location_code INT64, language_code STRING, device STRING, os STRING,
  se_results_count INT64, check_url STRING,
  item_types STRING, refinement_chips STRING,
  item_type STRING, rank_group INT64,
  rank_absolute INT64, page INT64, position STRING,
  data STRING, item STRING
"""

DATAFORSEO_LABS_KEYWORD_SUGGESTIONS_COLS = """\
  se_type STRING, seed_keyword STRING, keyword STRING,
  location_code INT64, language_code STRING,
  search_volume INT64, competition FLOAT64, competition_level STRING,
  cpc FLOAT64, low_top_of_page_bid FLOAT64, high_top_of_page_bid FLOAT64,
  categories STRING, keyword_difficulty INT64,
  detected_language STRING, is_another_language STRING, words_count INT64,
  main_intent STRING, foreign_intent STRING,
  avg_backlinks FLOAT64, avg_dofollow FLOAT64, avg_referring_pages FLOAT64,
  avg_referring_domains FLOAT64, avg_referring_main_domains FLOAT64,
  avg_rank FLOAT64, avg_main_domain_rank FLOAT64
"""

DATAFORSEO_LABS_RELATED_KEYWORDS_COLS = """\
  seed_keyword STRING, related_keyword STRING, depth INT64,
  location_code INT64, language_code STRING,
  search_volume INT64, cpc FLOAT64, competition FLOAT64,
  competition_level STRING, low_top_of_page_bid FLOAT64,
  high_top_of_page_bid FLOAT64, keyword_difficulty INT64,
  detected_language STRING, is_other_language STRING,
  serp_item_types STRING, se_results_count INT64,
  serp_last_updated_time TIMESTAMP,
  backlinks FLOAT64, dofollow FLOAT64, referring_pages FLOAT64,
  referring_domains FLOAT64, referring_main_domains FLOAT64,
  main_domain_rank FLOAT64, search_intent_main STRING
"""

DATAFORSEO_LABS_RANKED_KEYWORDS_COLS = """\
  keyword STRING, rank INT64, url STRING, search_volume INT64,
  keyword_difficulty INT64, national_location_code INT64,
  traffic_volume FLOAT64, cost_per_click FLOAT64,
  keyword_location_code INT64, language_code STRING, main_domain STRING,
  cpc_raw FLOAT64, low_top_of_page_bid FLOAT64, high_top_of_page_bid FLOAT64,
  competition FLOAT64, competition_level STRING, categories STRING,
  monthly_searches STRING,
  search_volume_trend_monthly INT64, search_volume_trend_quarterly INT64,
  search_volume_trend_yearly INT64,
  rank_absolute INT64, position STRING, serp_keyword_difficulty STRING,
  serp_item_types STRING, se_results_count INT64,
  main_intent STRING, foreign_intent STRING,
  avg_backlinks FLOAT64, avg_referring_domains FLOAT64,
  avg_referring_main_domains FLOAT64, avg_rank FLOAT64,
  avg_main_domain_rank FLOAT64,
  company_id STRING, filters STRING
"""

DATAFORSEO_LABS_KEYWORD_OVERVIEW_COLS = """\
  keyword STRING, location_code INT64,
  search_volume INT64, competition FLOAT64, competition_level STRING,
  cpc FLOAT64, low_top_of_page_bid FLOAT64, high_top_of_page_bid FLOAT64,
  categories STRING, monthly_searches STRING,
  keyword_difficulty INT64, detected_language STRING,
  is_another_language BOOL,
  main_intent STRING, foreign_intent STRING,
  ad_position_min FLOAT64, ad_position_max FLOAT64, ad_position_average FLOAT64,
  cpc_min FLOAT64, cpc_max FLOAT64, cpc_average FLOAT64,
  daily_impressions_min FLOAT64, daily_impressions_max FLOAT64,
  daily_impressions_average FLOAT64
"""

DATAFORSEO_LABS_SEARCH_INTENT_COLS = """\
  keyword STRING, search_intent STRING, intent_probability FLOAT64,
  secondary_intents STRING, language_code STRING
"""

DATAFORSEO_LABS_DOMAIN_RANK_OVERVIEW_COLS = """\
  target STRING, location_code INT64, language_code STRING,
  organic_count INT64, organic_etv FLOAT64, organic_impressions_etv FLOAT64,
  organic_estimated_paid_traffic_cost FLOAT64,
  organic_is_new INT64, organic_is_up INT64, organic_is_down INT64,
  organic_is_lost INT64,
  organic_pos_1 INT64, organic_pos_2_3 INT64, organic_pos_4_10 INT64,
  organic_pos_11_20 INT64, organic_pos_21_30 INT64, organic_pos_31_40 INT64,
  organic_pos_41_50 INT64, organic_pos_51_60 INT64, organic_pos_61_70 INT64,
  organic_pos_71_80 INT64, organic_pos_81_90 INT64, organic_pos_91_100 INT64,
  paid_count INT64, paid_etv FLOAT64, paid_impressions_etv FLOAT64,
  paid_estimated_paid_traffic_cost FLOAT64,
  paid_is_new INT64, paid_is_up INT64, paid_is_down INT64, paid_is_lost INT64,
  local_pack_count INT64, local_pack_etv FLOAT64,
  featured_snippet_count INT64, featured_snippet_etv FLOAT64
"""

KEYWORDS_DATA_SEARCH_VOLUME_COLS = """\
  keyword STRING, local_search_volume INT64, local_location_code INT64
"""


MIGRATIONS: list[Migration] = [
    Migration(
        old_name="backlinks_backlinks_live",
        new_name="backlinks-backlinks",
        new_schema=METADATA_BLOCK_SQL + BACKLINKS_BACKLINKS_COLS,
    ),
    Migration(
        old_name="backlinks_bulk_pages_summary_live",
        new_name="backlinks-bulk_pages_summary",
        new_schema=METADATA_BLOCK_SQL + BACKLINKS_BULK_PAGES_SUMMARY_COLS,
        drop_project_id=True,
    ),
    Migration(
        old_name="backlinks_summary_live",
        new_name="backlinks-summary",
        new_schema=METADATA_BLOCK_SQL + BACKLINKS_SUMMARY_COLS,
        drop_project_id=True,
    ),
    Migration(
        old_name="serp_google_organic_live_advanced",
        new_name="serp-google-organic",
        new_schema=METADATA_BLOCK_SQL + SERP_GOOGLE_ORGANIC_COLS,
        drop_project_id=True,
    ),
    Migration(
        old_name="google_keyword-suggestions_live",
        new_name="dataforseo_labs-google-keyword_suggestions",
        new_schema=METADATA_BLOCK_SQL + DATAFORSEO_LABS_KEYWORD_SUGGESTIONS_COLS,
    ),
    Migration(
        old_name="google_related-keywords_live",
        new_name="dataforseo_labs-google-related_keywords",
        new_schema=METADATA_BLOCK_SQL + DATAFORSEO_LABS_RELATED_KEYWORDS_COLS,
    ),
    Migration(
        # In-place schema update only — name already canonical
        old_name="dataforseo_labs-google-ranked_keywords",
        new_name="dataforseo_labs-google-ranked_keywords",
        new_schema=METADATA_BLOCK_SQL + DATAFORSEO_LABS_RANKED_KEYWORDS_COLS,
    ),
    Migration(
        old_name="dataforseo_labs_google_keyword_overview",
        new_name="dataforseo_labs-google-keyword_overview",
        new_schema=METADATA_BLOCK_SQL + DATAFORSEO_LABS_KEYWORD_OVERVIEW_COLS,
    ),
    Migration(
        old_name="dataforseo_labs_google_search_intent",
        new_name="dataforseo_labs-google-search_intent",
        new_schema=METADATA_BLOCK_SQL + DATAFORSEO_LABS_SEARCH_INTENT_COLS,
    ),
    Migration(
        # New table — no old_name
        old_name=None,
        new_name="dataforseo_labs-google-domain_rank_overview",
        new_schema=METADATA_BLOCK_SQL + DATAFORSEO_LABS_DOMAIN_RANK_OVERVIEW_COLS,
    ),
    Migration(
        old_name="keyword_data-google_ads-search_volume",
        new_name="keywords_data-google_ads-search_volume",
        new_schema=METADATA_BLOCK_SQL + KEYWORDS_DATA_SEARCH_VOLUME_COLS,
    ),
]
