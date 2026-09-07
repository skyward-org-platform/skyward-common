-- 20260907120100_meta_007_site.sql
-- One row per domain we actually work on.
--
-- Separate from meta.domains because domains holds 820 rows against 22
-- clients, so most are competitors. Fifteen mostly-null operational
-- columns on a registry would be wrong.
create table meta.site (
    domain_id              bigint primary key
                               references meta.domains(domain_id),
    client_id              bigint not null
                               references meta.clients(client_id),
    project_id             bigint references meta.projects(project_id),
    parent_domain_id       bigint references meta.domains(domain_id),
    engagement_status      text not null
                               check (engagement_status in
                                   ('client','prospect')),
    lifecycle_status       text not null default 'active'
                               check (lifecycle_status in
                                   ('active','paused','offboarded')),
    engagement_start       date,
    industry               text,
    title_brand            text,
    title_brand_abbrev     text,
    drive_client_folder_id text,
    drive_site_folder_id   text,
    clickup_space_id       text,
    clickup_folder_id      text,
    clickup_list_id        text,
    clickup_task_id        text,
    account_manager        text,
    source                 text not null,
    notes                  text,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),
    check (parent_domain_id is null or parent_domain_id <> domain_id)
);

create index site_client_idx on meta.site (client_id);
create index site_parent_idx on meta.site (parent_domain_id);

comment on column meta.site.engagement_status is
    'Drives three things at once: which Drive root deliverables land '
    'in, whether a ClickUp list exists, and whether the workbook '
    'ships an Implementation Tasks tab.';

comment on column meta.site.title_brand_abbrev is
    'Appended to page titles. Distinct from meta.clients.abbreviation, '
    'which drives ClickUp naming.';
