-- 20261005200000_keyword_analysis_004_detail_from_bigquery.sql
--
-- The deep detail moves to BigQuery; the cache keeps the pointers to it.
--
-- WHY
-- Adam, 2026-10-05: Supabase holds what the workbook needs plus the
-- headline columns. The cluster and keyword pages load the rest (every
-- score and fact, the vote, national and local results, rankings) from
-- BigQuery on demand, with a loading bar, because caching it all would be
-- ~300 MB per build at busbank's size.
--
-- So the columns and tables that were to hold that detail go (all empty:
-- nothing ever wrote them), and two pointers come in. serp-google-organic
-- is 240 GB, clustered by job_id, not keyword: a page reading a keyword's
-- results WITHOUT its job scans ~4 GB (about $0.03 a view); WITH it, a few
-- MB. The build resolves each keyword's newest results job once (one scan,
-- about $0.03 a build) and every page view after is cheap.

alter table keyword_analysis.keyword
    drop column facts,
    drop column categorize,
    drop column scores,
    drop column rankings,
    -- The national results job this build read for the keyword.
    add column serp_job_id text;

-- Per location code of the market: {"<code>": "<job_id>"}.
alter table keyword_analysis.keyword_market
    add column serp_jobs jsonb not null default '{}'::jsonb;

drop table keyword_analysis.keyword_serp;
drop table keyword_analysis.url_authority;
