-- 20260911210000_meta_015_metrics_attempted.sql
-- Record that we ASKED, so we stop buying the same empty answer forever.
--
-- competitor-metrics caches results. Ten of testbusbank's competitors
-- return HTTP 200 with no data at all: DataForSEO Labs simply has no
-- ranked-keyword rows for them, mostly small Canadian operators. Nothing is
-- stored, because there is nothing to store.
--
-- Which makes "we asked and there was nothing" indistinguishable from "we
-- never asked". The next run sees them as uncached, pulls them again, and
-- gets nothing again. Thread 2 confirmed it: straight after the run that
-- paid for them, --dry-run reported the same eleven as uncached. About
-- $0.60 a run, in perpetuity.
--
-- WHY A NEW COLUMN RATHER THAN REUSING metrics_checked_at
--
-- metrics_checked_at means "when we last got numbers", and readers rely on
-- that: a row with figures and a recent timestamp is measured. Widening it
-- to "when we last tried" would silently change what it tells every future
-- reader, and a competitor with no traffic would become indistinguishable
-- from one with traffic we happen to have refreshed.
--
-- So: two columns with two meanings. attempted_at drives the cache
-- decision; checked_at still means we have numbers.
alter table meta.site_competitors
    add column metrics_attempted_at timestamptz;

comment on column meta.site_competitors.metrics_attempted_at is
    'When this competitor was last sent to DataForSEO, whether or not data '
    'came back. Drives the cache decision. A row with attempted_at set and '
    'metrics_checked_at null is one DataForSEO has no data for, which is a '
    'fact worth keeping rather than a pull worth repeating.';

-- Everything measured so far was, by definition, also attempted. Backfilling
-- keeps the first run after this migration from re-pulling all 296.
update meta.site_competitors
   set metrics_attempted_at = metrics_checked_at
 where metrics_checked_at is not null;
