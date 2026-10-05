-- 20261005173000_keyword_analysis_002_pipeline_writers.sql
--
-- Let the pipeline write the Phase 3 dashboard cache.
--
-- WHY
-- keyword_analysis_001 granted the dashboard read access and nothing else.
-- The pipeline connects as a per-person login (adam) or pipeline_bot, not
-- postgres, so its first write failed loudly, exactly as
-- meta_019_per_person_logins_missing_schemas says a new schema should:
--
--     p3 workbook -> permission denied for schema keyword_analysis
--
-- Same grants meta_019 gave wqa and vector: the pipeline reads and writes
-- the schema it owns; the dashboard stays read-only.

grant usage on schema keyword_analysis to adam, pipeline_bot;

grant select, insert, update, delete
    on all tables in schema keyword_analysis
    to adam, pipeline_bot;

grant usage, select on all sequences in schema keyword_analysis
    to adam, pipeline_bot;

alter default privileges in schema keyword_analysis
    grant select, insert, update, delete on tables to adam, pipeline_bot;

alter default privileges in schema keyword_analysis
    grant usage, select on sequences to adam, pipeline_bot;
