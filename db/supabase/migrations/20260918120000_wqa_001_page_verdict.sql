-- 20260918120000_wqa_001_page_verdict.sql
-- Phase 1's decision for every URL, one row per URL per version.
--
-- WHY THIS IS IN SUPABASE AND THE AGGREGATE IS NOT
--
-- Supabase holds what the operator dashboard may show and what an
-- operator may edit. BigQuery holds bulk. wqa_output's 49 columns are
-- the evidence behind a verdict and stay in BigQuery; this table is the
-- verdict itself, which is what a dashboard renders and what a person
-- overrides. It also retires SEOPipeline.page_inventory, which carried
-- no job_id, no version, and a string project_id nothing could join on.
--
-- THE FIRST VERSIONED TABLE IN SUPABASE
--
-- Every other table here is one current row per site, so upsert means
-- "correct the fact". This one means "add this run's answer and keep the
-- last one", so version is part of the natural key. Readers resolve
-- latest per URL, never a project-wide max: a run that re-triages part
-- of a site legitimately leaves rows at different versions.
--
-- THE TRIGGER FIRES ON UPDATE AND DELETE, NOT INSERT
--
-- pipeline.log_change snapshots whole rows, and its own migration notes
-- the assumption that these tables are small: 129 market rows at the
-- largest. A BusBank run inserts 3,068 verdicts. Recovery is about an
-- overwrite destroying a value, and a run's first write is reproducible
-- from BigQuery, so inserts are not logged and overwrites are.
create schema if not exists wqa;

create table wqa.page_verdict (
    -- lineage
    domain_id               bigint      not null references meta.site(domain_id) on delete cascade,
    project_id              bigint      not null,
    version                 integer     not null,
    job_id                  text        not null,
    -- identity
    url                     text        not null,
    page_path               text,
    is_primary_url          boolean,
    -- the decision
    action                  text        not null
                            check (action in (
                                'Optimize', 'Restore', 'Redirect', 'Consolidate',
                                'Remove', 'Evaluate', 'Investigate',
                                'Non-addressable', 'Non-indexable', 'Leave as 404')),
    logic                   text        not null,
    priority_tier           text,
    -- labels, Phase 1 assigns page type and service category; market is
    -- relabelled at Phase 3 stage 4 against the final market list
    page_type               text,
    service_category        text,
    market                  text,
    -- redirect resolution, Section 4.6d
    redirect_final_url      text,
    redirect_final_status   integer,
    redirect_hops           integer,
    redirect_loop           boolean,
    -- review round, Section 7.5
    reviewer_decision       text,
    reviewer_note           text,
    -- the signals the SOP requires exposing so a verdict is checkable
    status_code             integer,
    indexability            text,
    indexability_status     text,
    in_sitemap              boolean,
    word_count              integer,
    inlinks                 integer,
    outlinks                integer,
    page_depth              integer,
    current_title           text,
    meta_description        text,
    h1                      text,
    canonical_link_element  text,
    google_indexed          boolean,
    canonical_mismatch      boolean,
    sessions                numeric,
    average_impressions     numeric,
    average_ctr             numeric,
    conversions             numeric,
    total_revenue           numeric,
    backlinks               integer,
    referring_domains       integer,
    best_tv_keyword         text,
    best_tv_kw_sv           integer,
    best_tv_kw_rank         integer,
    data_sources            text,
    -- house convention
    notes                   text,
    sources                 text[],
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),
    constraint page_verdict_natural_key
        unique (domain_id, project_id, version, url)
);

comment on table wqa.page_verdict is
    'Phase 1 triage: one verdict per URL per version. The evidence behind '
    'each verdict stays in BigQuery wqa_output; this is the decision.';

comment on column wqa.page_verdict.version is
    'Run version. Part of the natural key: readers resolve latest per URL, '
    'never a project-wide max.';

create index page_verdict_site_version
    on wqa.page_verdict (domain_id, project_id, version desc);
create index page_verdict_action
    on wqa.page_verdict (domain_id, action);

-- Update and delete only. See the header.
create trigger log_change
    after update or delete on wqa.page_verdict
    for each row execute function pipeline.log_change();
