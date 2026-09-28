-- 20260927180000_wqa_008_aggregate_crawl_provenance.sql
--
-- Which Screaming Frog crawls fed a run's measured audit.
--
-- Adam, 2026-09-27: "I just want to be able to see the screaming frog
-- job_id(s) -- the spider one and the list one(s) listed separately --
-- along with the date of those crawls."
--
-- A crawl is the most consequential input Phase 1 has and the easiest to
-- be wrong about: a spider from three weeks ago and a list crawl from
-- yesterday produce one aggregate, and nothing downstream says so. The
-- ids matter too -- they are how a crawl is looked up in the crawler's
-- own logs when a page's status looks implausible.
--
-- PROVENANCE BELONGS WITH THE MEASUREMENT, not with the config: the
-- config says what the run was TOLD to do, and this says what it
-- actually read. Stored on the snapshot the mirror already writes, so it
-- is versioned with the audit it describes and needs no second table.
--
-- Shape, one object per crawl, in the order the resolver returned them:
--
--   [{"job_id": "...", "mode": "spider", "started_at": "...",
--     "status": "...", "urls": 153}, ...]
--
-- `mode` is the crawler's own word, not ours, so a mode nobody
-- anticipated arrives intact rather than being bucketed into "other".

alter table wqa.aggregate_snapshot
    add column if not exists sf_crawls jsonb;

comment on column wqa.aggregate_snapshot.sf_crawls is
    'The Screaming Frog crawls that fed this aggregate: job_id, mode '
    '(spider/list), when it started, and how many URLs it contributed. '
    'Null where the run predates this being recorded -- which is not the '
    'same as a run that crawled nothing.';
