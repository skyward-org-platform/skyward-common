-- 20261005190000_keyword_analysis_003_build_sources.sql
--
-- Record which BigQuery run of every step a build was made from.
--
-- WHY
-- Adam, 2026-10-05: Supabase holds what the workbook needs plus the
-- headline columns; the cluster and keyword pages load their deeper detail
-- (every score and fact, the vote, national and local results, rankings)
-- from BigQuery on demand. For an OLDER build to show its own detail, not
-- today's, the build must say exactly which runs it read. BigQuery's
-- Phase 3 tables are versioned and never overwritten, so those pins
-- reproduce any kept build exactly.
--
-- Shape (written by p3 workbook):
--   {"project_id": 21, "country_code": 2840,
--    "keyword_scores_version": 10, "facts_version": 1, "facts_source": "lane",
--    "categorize_job_id": "...",
--    "lanes": {"national": {"cluster_job_id": "...", "search_volume_job_id": "..."},
--              "<market_id>": {"cluster_job_id": "...", "search_volume_job_id": "...",
--                              "local_serp": {"<code>": "<job_id>"}}}}

alter table keyword_analysis.build
    add column sources jsonb not null default '{}'::jsonb;
