-- authority_002: what counts toward the target, and dated metric readings.
--
-- Paul's answers on authority_001 (11 Nov 2026):
--   1. Links the client had before Skyward started don't count toward the
--      target. They still show as existing quality links.
--   2. type stays owned / earned / paid. Paid links never count, and a live
--      paid link must carry rel="sponsored".
--   3. Every spam / traffic reading keeps its date. They are read again at the
--      30- and 90-day checks.
--
-- placement.spam_score / site_us_traffic / metrics_read_at stay as the FIRST
-- reading (at go-live, or the first read for a link that predates this table),
-- and quality keeps using it. Every reading, the first included, is also kept
-- in authority.placement_reading, one row per link per checkpoint.

alter table authority.placement
    -- TRUE when the link was live before the engagement started (sheet-owned).
    add column pre_existing boolean not null default false,
    -- rel="sponsored" seen on the live link; null until checked (sheet-owned).
    add column sponsored boolean,
    add constraint placement_paid_live_is_sponsored
        check (type is distinct from 'paid' or status <> 'live' or coalesce(sponsored, false)),
    -- quality, minus pre-existing and paid links. A generated column can't
    -- read another one, so the quality rule is repeated here.
    add column counted boolean generated always as (
        coalesce(status = 'live' and dofollow and spam_score < 40 and site_us_traffic >= 1000
                 and not pre_existing and type is distinct from 'paid', false)
    ) stored;

create table authority.placement_reading (
    placement_id    uuid not null references authority.placement (placement_id) on delete cascade,
    checkpoint      text not null check (checkpoint in ('first', '30d', '90d')),
    read_at         date not null,
    spam_score      integer check (spam_score is null or spam_score between 0 and 100),
    site_us_traffic integer check (site_us_traffic is null or site_us_traffic >= 0),
    source          text not null default 'dataforseo',
    created_at      timestamptz not null default now(),
    primary key (placement_id, checkpoint)
);
create index placement_reading_by_date on authority.placement_reading (read_at);

grant select, insert, update, delete on authority.placement_reading to adam, pipeline_bot;
grant select on authority.placement_reading to dashboard_ro;
create trigger log_change after update or delete on authority.placement_reading
    for each row execute function pipeline.log_change();

-- The readings already on placement become each link's first reading.
insert into authority.placement_reading (placement_id, checkpoint, read_at, spam_score, site_us_traffic)
select placement_id, 'first', metrics_read_at, spam_score, site_us_traffic
  from authority.placement
 where metrics_read_at is not null;

