-- 20261014194500_p3_003_registry_conventions.sql
--
-- Bring the p3 tables (p3_001) in line with every other kb-brand registered
-- table, which the registry's live cross-check (tests/infra/knowledge_base/
-- test_specs_match_database.py in skyward-seo-pipeline) enforces:
--
-- 1. a uuid primary key with gen_random_uuid(), not a bigserial id;
-- 2. the natural key as a UNIQUE CONSTRAINT, not a unique index: the
--    generic kb-brand upsert's ON CONFLICT target is the constraint;
-- 3. cluster_override's key holds keyword, which is NULL on a rename, so
--    its constraint is NULLS NOT DISTINCT (two renames of one cluster in one
--    build are the same row), replacing p3_001's coalesce(keyword,'') index.
--
-- The tables are empty (checked 2026-10-14 before applying), so the id
-- columns are dropped and re-added. The change-log triggers are untouched.
--
-- Apply as postgres from skyward-common's .env, with Adam's OK.

alter table p3.keyword_override drop column keyword_override_id;
alter table p3.keyword_override add column keyword_override_id uuid primary key default gen_random_uuid();
drop index p3.keyword_override_uniq;
alter table p3.keyword_override add constraint keyword_override_key
    unique (domain_id, country_code, keyword);

alter table p3.prompt drop column prompt_row_id;
alter table p3.prompt add column prompt_row_id uuid primary key default gen_random_uuid();
drop index p3.prompt_uniq;
alter table p3.prompt add constraint prompt_key_uniq
    unique (domain_id, country_code, prompt_key);

alter table p3.category_override drop column category_override_id;
alter table p3.category_override add column category_override_id uuid primary key default gen_random_uuid();
drop index p3.category_override_uniq;
alter table p3.category_override add constraint category_override_key
    unique (domain_id, country_code, keyword, layer);

alter table p3.cluster_override drop column cluster_override_id;
alter table p3.cluster_override add column cluster_override_id uuid primary key default gen_random_uuid();
drop index p3.cluster_override_uniq;
alter table p3.cluster_override add constraint cluster_override_key
    unique nulls not distinct (domain_id, build_id, lane_key, kind, keyword, cluster_id);

alter table p3.decision drop column decision_id;
alter table p3.decision add column decision_id uuid primary key default gen_random_uuid();
drop index p3.decision_uniq;
alter table p3.decision add constraint decision_key_uniq
    unique (domain_id, decision_key);
