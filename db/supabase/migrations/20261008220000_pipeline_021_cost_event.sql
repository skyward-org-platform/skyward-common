-- 20261008220000_pipeline_021_cost_event.sql
--
-- What a pipeline run COST, recorded against the client. One row per paid
-- run of a module step, per provider.
--
-- Why (Adam, 2026-10-08): the dashboard's "Spent so far" only knew the
-- Library's runs (library.runs.estimated_usd, a quote). Phase 3 spent
-- ~$104 on Insofast's US National stage (DataForSEO + OpenAI) and none of
-- it reached the client's figure. keyword_analysis.step_status holds each
-- step's LATEST run only, so a rerun overwrites the earlier spend; this
-- table keeps every run.
--
-- Written by the pipeline (Phase 3 first; any module may use it). The
-- natural key is (job_id, step, provider): re-recording the same run
-- updates its row, a rerun has a new job_id and adds one. A run that was
-- stopped before it recorded its own cost is entered by hand with
-- status 'stopped' and a note saying how the figure was measured.
--
-- actual_usd is MEASURED (DataForSEO balance before/after or its per-job
-- cost; LLM tokens x price). NULL means not measured, never zero. The
-- estimates are what was shown before the run, for comparison.
--
-- No change-log trigger: machine-written records, like run_progress.
-- Grants: none written here. Default privileges on this schema give
-- adam / pipeline_bot write and dashboard_ro read. Apply as postgres, or
-- those defaults do not fire.

create table pipeline.cost_event (
    cost_event_id          uuid primary key default gen_random_uuid(),
    domain_id              bigint not null
                               references meta.site(domain_id)
                               on delete cascade,
    client_id              bigint not null,
    project_id             bigint
                               references meta.projects(project_id),
    -- The module as the pipeline names it, e.g. 'phase_3'.
    module                 text not null,
    -- The module's step, e.g. corpus, categorize, serp, competition,
    -- local_serp, search_volume.
    step                   text not null,
    -- 'national' or a market's id; null for a step that is not per lane.
    lane                   text,
    country_code           integer,
    provider               text not null
                               check (provider in ('dataforseo','openai','other')),
    -- The BigQuery run's job_id (the run that spent the money).
    job_id                 text not null,
    estimate_expected_usd  numeric(12,6),
    estimate_upper_usd     numeric(12,6),
    actual_usd             numeric(12,6)
                               check (actual_usd is null or actual_usd >= 0),
    -- How actual_usd was measured, in words: 'balance before/after',
    -- 'per-job cost', 'tokens x price', 'from the run log'.
    measured_how           text,
    status                 text not null
                               check (status in
                                   ('success','partial','failed','stopped')),
    occurred_at            timestamptz not null,
    recorded_at            timestamptz not null default now(),
    notes                  text,
    unique (job_id, step, provider)
);

create index cost_event_domain_idx on pipeline.cost_event (domain_id, occurred_at);

comment on table pipeline.cost_event is
    'Measured spend per paid pipeline run, against the client. One row per '
    '(job_id, step, provider). The dashboard''s site spend sums actual_usd.';
comment on column pipeline.cost_event.actual_usd is
    'Measured cost in USD. NULL = not measured, never zero.';
