-- 20260927140000_wqa_007_audit_version_config_and_aggregate.sql
--
-- Which pipeline run a workbook belongs to, the config it ran with, and
-- the measured audit it was derived from.
--
-- THE VERSION AN OPERATOR MEANS IS THE RUN'S, NOT THE VERDICT'S.
--
-- Adam, 2026-09-27: "if we're making intermittent changes to the page
-- verdicts, all of those changes are going to be within the same version.
-- Only when we run a new full pipeline run does the version increment."
--
-- That is the AGGREGATE's version. `wqa_output` gets a new version per
-- full Phase 1 run and nothing else moves it. The page_verdict version
-- does not behave that way: triage mints one, and review mints another to
-- apply reviewer decisions, so plasry went v2 -> v5 inside a single
-- audit of aggregate v11. Showing that number would make a month of
-- edits look like four different audits.
--
-- It is also the number the Drive version folder already uses
-- (`V11 | 2026-09-21`), so storing it here is what lets the dashboard,
-- the folder and the operator agree on what "version 11" means.
--
-- DUPLICATED ACROSS A BUILD'S SIXTEEN TAB ROWS, deliberately. A build is
-- sixteen rows that share these two values; a separate build table would
-- normalise it at the cost of a join on every read and a restructure of
-- code that works. A config is a few kilobytes and there is one build per
-- run per site.

alter table wqa.workbook_tab
    -- The `wqa_output` version this workbook's verdicts were derived
    -- from: the pipeline run, as an operator counts them. Null on the
    -- builds stored before this migration, which nobody can now
    -- attribute -- a guess would be worse than the gap.
    add column if not exists aggregate_version integer,
    -- The resolved config the run used: the YAML plus what the run
    -- settled at the time (the window it measured, the flags it ran
    -- with). Stored so an operator can answer "what did this run
    -- actually do" without reading a file on somebody's laptop.
    add column if not exists config jsonb;

create index if not exists workbook_tab_by_audit
    on wqa.workbook_tab (domain_id, aggregate_version desc);

-- ---------------------------------------------------------------------
-- The measured audit itself.
--
-- `wqa_output` lives in BigQuery, and nothing in a dashboard request path
-- may read BigQuery -- that is the rule the whole app is built on. So the
-- rows are mirrored here, per run, for the base-audit tab.
--
-- A MEASUREMENT, NEVER EDITED. The verdict layer is where judgement
-- lives and where an operator edits; this is what the crawl, GA4, GSC and
-- DataForSEO found on a date. Editing it would have us claim we measured
-- something we did not, which is why `p1-wqa edit` refuses every measured
-- column. Nothing writes here except the mirror.
--
-- Same shape as the workbook: a snapshot row carrying the heading order,
-- and one row per URL keyed by those headings. The aggregate's columns
-- change as the pipeline gains sources, and a table with 47 typed columns
-- would need a migration every time one is added.
-- ---------------------------------------------------------------------
create table if not exists wqa.aggregate_snapshot (
    aggregate_snapshot_id uuid primary key default gen_random_uuid(),

    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    project_id      bigint not null,
    -- The `wqa_output` version. One per full Phase 1 run.
    version         integer not null,
    -- The run that mirrored it, for lineage back to the pipeline.
    job_id          text,

    columns         jsonb not null default '[]'::jsonb,
    row_count       integer not null default 0,
    -- When the pipeline measured it, which is not when we mirrored it.
    measured_at     timestamptz,
    created_at      timestamptz not null default now(),

    constraint aggregate_snapshot_natural_key
        unique (domain_id, project_id, version)
);

create index if not exists aggregate_snapshot_latest
    on wqa.aggregate_snapshot (domain_id, version desc);

create table if not exists wqa.aggregate_row (
    aggregate_row_id uuid primary key default gen_random_uuid(),

    aggregate_snapshot_id uuid not null
                        references wqa.aggregate_snapshot(aggregate_snapshot_id)
                        on delete cascade,
    -- Denormalised, as everywhere else here: a read scoped to a site
    -- should not need the join to be safe.
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,

    -- EVERY URL FORM IS ITS OWN ROW, which is the audit's whole point:
    -- http and https, with and without www, with and without a trailing
    -- slash are different pages to a crawler and are kept apart.
    url             text not null,
    row_number      integer not null,
    cells           jsonb not null,

    created_at      timestamptz not null default now(),

    constraint aggregate_row_natural_key
        unique (aggregate_snapshot_id, url)
);

create index if not exists aggregate_row_by_snapshot
    on wqa.aggregate_row (aggregate_snapshot_id, row_number);
create index if not exists aggregate_row_cells
    on wqa.aggregate_row using gin (cells jsonb_path_ops);

grant select on wqa.aggregate_snapshot, wqa.aggregate_row to dashboard_ro;
