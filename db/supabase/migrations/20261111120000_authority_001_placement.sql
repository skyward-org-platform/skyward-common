-- 20261111120000_authority_001_placement.sql
--
-- The Authority section gets its own schema, the way Phase 1 got `wqa` and
-- Phase 2 got `tsa`. This first migration adds Ask 1 of Paul's Authority data
-- spec (draft 2026-10-27, three columns added 2026-11-09): the placement log.
--
-- authority.placement: one row per link Skyward tries to get, from prospect
-- to live (or lost). For now the input is a Google Sheet ("Authority placement
-- log"), Paul's call on 2026-11-10, since no single place tracks placements
-- yet. scripts/sync_authority_placement.py upserts the sheet into this table
-- by placement_id. The dashboard only reads it.
--
-- quality is a GENERATED column, so the rule lives in one place and can't
-- drift: live AND dofollow AND spam_score < 40 AND site_us_traffic >= 1000.
-- Unknown counts as "not counted" (false), never as null. Only quality rows
-- count toward a link target (Paul, 2026-11-09).
--
-- Columns beyond the spec, both nullable:
--   notes            free text from the sheet (seed provenance, fixes)
--   metrics_read_at  the date spam_score and site_us_traffic were read. The
--                    spec wants them "at went_live"; for links that were live
--                    before this table existed, the first read is later, and
--                    this column says so.
--
-- work_item_id is text with no foreign key: the spec says text, and today's
-- values are the dashboard's work-export ids (w079...), not
-- pipeline.work_item uuids.
--
-- Grants follow tsa_001: the per-person roles plus the dashboard's read-only
-- role, with default privileges so later authority.* tables inherit them.
-- Changelog on update and delete, as on tsa.*. Apply as postgres. The runner
-- (scripts/apply_migrations.py) executes this file inside one transaction,
-- so there is no BEGIN/COMMIT here; one would defeat --dry-run.

create schema if not exists authority;

grant usage on schema authority to adam, pipeline_bot, dashboard_ro;
alter default privileges in schema authority
    grant select, insert, update, delete on tables to adam, pipeline_bot;
alter default privileges in schema authority
    grant usage, select on sequences to adam, pipeline_bot;
alter default privileges in schema authority
    grant select on tables to dashboard_ro;

create table authority.placement (
    placement_id     uuid primary key default gen_random_uuid(),
    domain_id        bigint not null references meta.site(domain_id)
                         on delete cascade,
    -- The bare host that links to the client, e.g. visitma.com. Null only
    -- while a paid package has not named its sites yet.
    site             text check (site is null or (site = lower(site)
                         and site !~ '^https?://' and site !~ '/')),
    url_live         text,
    target_page      text,
    page_group       text check (page_group is null or page_group in
                         ('home','pricing','location','service','blog','other')),
    anchor           text,
    anchor_kind      text check (anchor_kind is null or anchor_kind in
                         ('brand','general','partial','exact')),
    type             text check (type is null or type in
                         ('owned','earned','paid')),
    tactic           text,
    tier             text check (tier is null or tier in ('1','2a','2b','2c')),
    vendor           text,
    cost_usd         numeric(10,2) check (cost_usd is null or cost_usd >= 0),
    status           text not null check (status in
                         ('prospect','contacted','replied','agreed','live',
                          'lost','declined')),
    contacted_at     date,
    replied_at       date,
    went_live        date,
    check_30d        boolean,
    check_90d        boolean,
    dofollow         boolean,
    work_item_id     text,
    spam_score       integer check (spam_score is null
                         or spam_score between 0 and 100),
    site_us_traffic  integer check (site_us_traffic is null
                         or site_us_traffic >= 0),
    metrics_read_at  date,
    quality          boolean generated always as (
                         coalesce(status = 'live'
                                  and dofollow
                                  and spam_score < 40
                                  and site_us_traffic >= 1000, false)
                     ) stored,
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    constraint placement_live_has_site check (
        status <> 'live' or site is not null)
);

create index placement_by_client on authority.placement (domain_id, status);
create index placement_by_site on authority.placement (site);

comment on table authority.placement is
    'One row per link Skyward tries to get. Loaded from the Authority '
    'placement log Google Sheet by scripts/sync_authority_placement.py.';
comment on column authority.placement.quality is
    'live AND dofollow AND spam_score < 40 AND site_us_traffic >= 1000. '
    'Only quality rows count toward a link target (Paul, 2026-11-09).';

grant select, insert, update, delete on authority.placement
    to adam, pipeline_bot;
grant select on authority.placement to dashboard_ro;

create trigger log_change after update or delete on authority.placement
    for each row execute function pipeline.log_change();
