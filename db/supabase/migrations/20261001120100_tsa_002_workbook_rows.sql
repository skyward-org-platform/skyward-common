-- 20261001120100_tsa_002_workbook_rows.sql
--
-- Phase 2's workbook, stored as the builder built it. Same shape and same
-- reason as wqa_006 (plus wqa_007's aggregate_version and config): the
-- builder is the only thing that knows what the workbook is, so it stores
-- what it made, and the dashboard and the .xlsx both render those rows.
-- Phase 2 only.
--
-- Two columns Phase 1's tables do not have:
--   tab_color  navy / green / yellow / orange / blue / grey, the SOP's
--              colour for the sheet (issue sheets by keyword dependency)
--   check_id   set on a per-check issue sheet, so the dashboard can link
--              the sheet to that check
--
-- Replaced whole per version (delete then insert in one transaction), like
-- Phase 1's store, so a rebuild never leaves rows from a shrunken tab.
-- Grants come from tsa_001's default privileges; repeated explicitly.

create table tsa.workbook_tab (
    workbook_tab_id   uuid primary key default gen_random_uuid(),
    domain_id         bigint not null references meta.site(domain_id)
                          on delete cascade,
    project_id        bigint not null,
    version           integer not null,
    job_id            text,
    slug              text not null,
    number            text not null,
    name              text not null,
    position          integer not null,
    tab_color         text check (tab_color is null or tab_color in
                          ('navy','green','yellow','orange','blue','grey')),
    check_id          text,
    what              text,
    note              text,
    columns           jsonb not null default '[]'::jsonb,
    row_count         integer not null default 0,
    aggregate_version integer,
    -- What the run was told, as resolved: the dashboard's Config tab reads
    -- this, because run configs live in BigQuery, which it never reads.
    config            jsonb,
    created_at        timestamptz not null default now(),
    constraint workbook_tab_natural_key
        unique (domain_id, project_id, version, slug)
);
create index workbook_tab_latest
    on tsa.workbook_tab (domain_id, project_id, version desc);

create table tsa.workbook_row (
    workbook_row_id uuid primary key default gen_random_uuid(),
    workbook_tab_id uuid not null references tsa.workbook_tab(workbook_tab_id)
                        on delete cascade,
    domain_id       bigint not null references meta.site(domain_id)
                        on delete cascade,
    row_number      integer not null,
    cells           jsonb not null,
    created_at      timestamptz not null default now(),
    constraint workbook_row_natural_key
        unique (workbook_tab_id, row_number)
);
create index workbook_row_by_tab
    on tsa.workbook_row (workbook_tab_id, row_number);
create index workbook_row_cells
    on tsa.workbook_row using gin (cells jsonb_path_ops);

grant select, insert, update, delete on tsa.workbook_tab, tsa.workbook_row
    to adam, pipeline_bot;
grant select on tsa.workbook_tab, tsa.workbook_row to dashboard_ro;
