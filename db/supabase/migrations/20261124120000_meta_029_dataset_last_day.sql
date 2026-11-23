-- 20261124120000_meta_029_dataset_last_day.sql
-- The latest day each warehouse dataset holds, and when we last looked.
--
-- Asked for by Paul 2026-11-23, ClickUp 86bd62xvt, from pipeline.request
-- 61cb057d (data-access-freshness-reads-stale-gsc-date).
--
-- THE FAILURE THIS EXISTS TO NAME. The operator dashboard's Record page
-- flagged BusBank's Search Console as "broken, no data in 59 d". The feed
-- was fine: jepto_gsc_busbank held 20 Nov and had refreshed that morning.
-- The 25 Sep date came from a copy of the export stored in the dashboard
-- repo. The dashboard reads Supabase only, never BigQuery on page load, so
-- it had no way to see the real date. This puts the real date where it can
-- read it.
--
-- ON THE DATASET, NOT ON meta.data_access. The ask offered either. The
-- dataset is the thing that has a latest day; a data_access row only points
-- at one through dataset_id. One Search Console dataset can serve more than
-- one site, so a per-access-row date would be the same fact copied, and the
-- copies could disagree. Readers join data_access.dataset_id to
-- dataset_catalog.dataset, which is already a foreign key.
--
-- THREE STATES, as with shape:
--   last_day_checked_at NULL          nobody has looked. Not "fine", not
--                                     "broken": unknown.
--   checked, last_day NULL            we looked and the dataset held no
--                                     rows we could date. A finding.
--   checked, last_day set             the latest day held at checked_at.
-- A failed read is OUR outage and writes nothing, so it never turns a good
-- date into a null.
--
-- "BROKEN" IS THE READER'S CALL, NOT A COLUMN. Sources lag by different
-- amounts (Search Console runs two to three days behind and the delay
-- varies; GA4 and Ads about one), so this stores the observation and the
-- reader compares last_day with its own threshold. And it compares only
-- when last_day_checked_at is recent: an old check is a claim, not a fact.

alter table meta.dataset_catalog
    add column if not exists last_day date,
    add column if not exists last_day_checked_at timestamptz;

alter table meta.dataset_catalog
    drop constraint if exists dataset_catalog_last_day_needs_check;

alter table meta.dataset_catalog
    add constraint dataset_catalog_last_day_needs_check
    check (last_day is null or last_day_checked_at is not null);

comment on column meta.dataset_catalog.last_day is
    'The latest day this dataset held when last_day_checked_at was taken: '
    'MAX(date) for Search Console, the newest events_YYYYMMDD table for '
    'GA4, the newest segment date for Google Ads. NULL with a check time '
    'means we looked and found nothing to date. NULL without one means '
    'nobody has looked. Whether that is "broken" depends on the source''s '
    'normal lag, so the reader decides.';

comment on column meta.dataset_catalog.last_day_checked_at is
    'When last_day was read from BigQuery. Written on every successful '
    'read, even when last_day did not move, so a recent check with an old '
    'last_day means the feed has stopped, and an old check means our job '
    'has.';

-- The dashboard runs as dashboard_ro and needs to read these. Select on the
-- catalogue only; no write privilege in meta (meta_026). Idempotent if it
-- already has it.
grant select on meta.dataset_catalog to dashboard_ro;
