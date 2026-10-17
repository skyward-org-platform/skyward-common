-- 20261001120000_tsa_001_target_and_check_result.sql
--
-- Phase 2, the technical audit, gets its own schema the way Phase 1 got
-- `wqa`. Agreed with Adam 2026-09-30 (skyward-seo-pipeline spec
-- docs/superpowers/specs/2026-09-30-phase-2-tsa-design.md). Phase 2 only:
-- nothing here touches a table Phase 1 reads or writes.
--
-- tsa.target: every page Phase 2 CONSIDERED, whether it was kept, and if
-- not, which rule dropped it and why. The exclusion is data rather than a
-- sentence in a doc, because the workbook, the doc and every task's scope
-- line all have to say the same number.
--
-- tsa.check_result: one row per check per page per version, url NULL for a
-- sitewide check. Every result is stored, passes included, so a score can
-- always be traced back to the checks behind it.
--
-- Both are FULL SNAPSHOTS per version: `p2-tsa targets` writes the whole
-- set and `p2-tsa checks` grades the whole set, so one version holds
-- everything. Versions are numbered per (domain_id, project_id).
--
-- Changelog on update and delete only, as on wqa.page_verdict: a run's
-- first write is large and reproducible; recovery is about overwrites.
--
-- Grants: the per-person roles (meta_017/meta_019) and the dashboard's
-- read-only role, with default privileges so tsa_002's tables inherit them.
-- Apply as postgres.

create schema if not exists tsa;

grant usage on schema tsa to adam, pipeline_bot, dashboard_ro;
alter default privileges in schema tsa
    grant select, insert, update, delete on tables to adam, pipeline_bot;
alter default privileges in schema tsa
    grant usage, select on sequences to adam, pipeline_bot;
alter default privileges in schema tsa
    grant select on tables to dashboard_ro;

create table tsa.target (
    target_id        uuid primary key default gen_random_uuid(),
    domain_id        bigint not null references meta.site(domain_id)
                         on delete cascade,
    project_id       bigint references meta.projects(project_id),
    version          integer not null,
    job_id           text,
    url              text not null,
    page_path        text,
    included         boolean not null,
    exclusion_rule   text check (exclusion_rule is null or exclusion_rule in
                         ('doubled-slash','clean-twin','non-200','off-host',
                          'malformed')),
    exclusion_reason text,
    p1_action        text not null,
    p1_version       integer not null,
    priority_tier    text,
    page_type        text,
    service_category text,
    market           text,
    notes            text,
    sources          text[] not null default '{}',
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    constraint target_natural_key
        unique nulls not distinct (domain_id, project_id, version, url),
    constraint target_excluded_says_why check (
        included or (exclusion_rule is not null
                     and exclusion_reason is not null))
);
create index target_latest on tsa.target (domain_id, project_id, version desc);

create table tsa.check_result (
    check_result_id  uuid primary key default gen_random_uuid(),
    domain_id        bigint not null references meta.site(domain_id)
                         on delete cascade,
    project_id       bigint references meta.projects(project_id),
    version          integer not null,
    job_id           text,
    check_id         text not null,
    -- The registry's name, stored so the dashboard never needs the registry.
    check_name       text,
    url              text,
    scope            text not null check (scope in ('page','site')),
    status           text not null check (status in
                         ('pass','fail','not applicable','not measurable')),
    measured         jsonb not null default '{}'::jsonb,
    detail           text,
    reason           text,
    kw_dependency    text check (kw_dependency is null or kw_dependency in
                         ('Fix Now','Fix Now, Revisit','Phase 3 Dependent')),
    subscore         text check (subscore is null or subscore in
                         ('indexation','schema','linking','onpage','speed')),
    priority         text,
    source_jobs      text[] not null default '{}',
    -- A person's judgement beside the measurement. The effective status is
    -- the reviewer's when set. Measured columns are never hand-edited.
    reviewer_status  text check (reviewer_status is null or reviewer_status in
                         ('pass','fail','not applicable','not measurable',
                          'wont fix')),
    reviewer_note    text,
    notes            text,
    sources          text[] not null default '{}',
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    constraint check_result_natural_key
        unique nulls not distinct (domain_id, project_id, version, check_id,
                                   url),
    constraint check_result_scope_matches_url check (
        (scope = 'site') = (url is null)),
    constraint check_result_override_says_why check (
        reviewer_status is null or reviewer_note is not null)
);
create index check_result_latest
    on tsa.check_result (domain_id, project_id, version desc);
create index check_result_by_check
    on tsa.check_result (domain_id, project_id, version, check_id);

create trigger log_change after update or delete on tsa.target
    for each row execute function pipeline.log_change();
create trigger log_change after update or delete on tsa.check_result
    for each row execute function pipeline.log_change();

grant select, insert, update, delete on tsa.target, tsa.check_result
    to adam, pipeline_bot;
grant select on tsa.target, tsa.check_result to dashboard_ro;
