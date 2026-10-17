-- 20261014193000_p3_002_pipeline_writers.sql
--
-- Let the pipeline read and write Phase 3's operator decisions (p3_001).
--
-- WHY
-- p3_001 granted the dashboard read access and nothing else. The pipeline
-- connects as a per-person login (adam) or pipeline_bot, not postgres, so
-- its first read failed loudly, the same way keyword_analysis_002 records:
--
--     p3 keywords-add -> permission denied for schema p3
--
-- Same grants as keyword_analysis_002. Every write is still change-logged by
-- each table's trigger (p3_001).
--
-- Apply as postgres from skyward-common's .env, with Adam's OK.

grant usage on schema p3 to adam, pipeline_bot;
grant select, insert, update, delete
    on all tables in schema p3
    to adam, pipeline_bot;
grant usage, select on all sequences in schema p3
    to adam, pipeline_bot;
alter default privileges in schema p3
    grant select, insert, update, delete on tables to adam, pipeline_bot;
alter default privileges in schema p3
    grant usage, select on sequences to adam, pipeline_bot;
