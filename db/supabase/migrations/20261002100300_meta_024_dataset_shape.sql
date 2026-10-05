-- 20261002100300_meta_024_dataset_shape.sql
-- Which SHAPE a warehouse dataset is in, and when we last looked.
--
-- Asked for by Adam 2026-10-02, from pipeline.request
-- no-verb-inspects-a-warehouse-dataset.
--
-- THE FAILURE THIS EXISTS TO NAME. A connector can be provisioned in more
-- than one shape, and the thin one still works -- quietly, on a weaker
-- source. Jepto Search Console comes as a base form (jepto_gsc_data alone)
-- and an expanded form that adds one table per report. Phase 1 needs
-- jepto_gsc_data_PAGE and jepto_gsc_data_SITEMAP specifically, and
-- loaders/gsc.py ALREADY catches NotFound on the sitemap table and falls
-- back to fetching the client's live site, with the comment "Jepto only
-- synced the base table". Of 23 jepto_gsc datasets, four are base-only:
-- desiccantdirect, partybusguru, plasry and sourceone. plasry is a site
-- Phase 1 has already audited, so that silent fallback HAS ALREADY
-- HAPPENED on real work and nothing recorded that it did.
--
-- A SHAPE IS AN OBSERVATION WITH A DATE, NOT A PROPERTY (Adam, 2026-10-02:
-- "sometimes these base only datasets get updated with the full deal. and
-- visa versa"). A base-only dataset gets expanded; an expanded one loses
-- tables. So shape_checked_at is not decoration: a stored shape with no
-- date, or an old one, is a claim nobody should act on. Re-reading is
-- expected, and the verb reports what was GAINED and what was LOST rather
-- than only noticing the improvement.
--
-- BASE-ONLY IS A FIRST-CLASS OUTCOME, not an error. It means "expanded
-- breakouts absent, Phase 1 will fall back to the live sitemap", which is
-- something onboarding should be able to say out loud.
--
-- THREE STATES, as everywhere else: a shape we matched, a shape we could
-- not match (recorded as unknown, which is a finding), and NULL meaning
-- nobody has looked. Null is not "fine".
--
-- DROPS is_standardized. It was reaching for this and never finished
-- (Adam, 2026-10-02: "use it or rebuild it, whatever works for this. If we
-- decide not to use is_standardized, let's get rid of it"). Rebuilt rather
-- than reused, for two reasons. A boolean cannot carry the answer: it
-- collapses base-only -- legitimate, useful, actionable -- into "not
-- standard", which is the very conflation this column is meant to end. And
-- its data carries no information: meta_001 declared it `default false`, so
-- the 79 false rows are the DEFAULT rather than anybody's judgement, beside
-- 25 nulls and a single true. Nothing in any repo reads the value; it is
-- selected into a dataframe in skyward-common and listed in two column
-- manifests, all updated alongside this migration.

alter table meta.dataset_catalog
    drop column if exists is_standardized;

alter table meta.dataset_catalog
    add column if not exists shape text,
    add column if not exists shape_checked_at timestamptz,
    add column if not exists shape_detail jsonb;

comment on column meta.dataset_catalog.shape is
    'Which provisioning shape this dataset matched when last inspected, '
    'e.g. gsc_expanded, gsc_base, ga4_events, gads_backfill. '
    '''unknown'' means we looked and matched nothing, which is a finding. '
    'NULL means nobody has looked, which is not the same as fine. Shapes '
    'change in BOTH directions, so read shape_checked_at before acting.';

comment on column meta.dataset_catalog.shape_checked_at is
    'When the shape was last read from BigQuery. A shape without a recent '
    'date is a claim, not a fact: a base-only dataset can be expanded and '
    'an expanded one can lose tables.';

comment on column meta.dataset_catalog.shape_detail is
    'What was actually seen: families_present, required_missing, '
    'optional_missing, table_count. Table FAMILIES rather than table '
    'names, so a GA4 export is events_* rather than four hundred daily '
    'partitions. Holds enough to diff one reading against the next and '
    'say which tables were gained and which were lost.';
