-- 20260910180000_meta_014_competitor_metrics.sql
-- How big a competitor actually is, so priority stops being a guess.
--
-- Per-market discovery took BusBank from 25 stored competitors to 125.
-- All 125 are real, and all 125 stay -- but they are not equally worth
-- spending a keyword-universe pull on, and nothing on the row said which
-- were serious. priority was a human's impression at intake.
--
-- These come from dataforseo_labs/google/domain_rank_overview, one
-- batched call for every competitor at once. Storing them makes the
-- ranking reproducible instead of a judgement somebody made in March.
--
-- metrics_checked_at exists because a stale traffic figure is worse than
-- none: without a date nobody can tell a competitor who shrank from one
-- nobody has measured recently.
--
-- No threshold is encoded here, deliberately. What counts as "big enough
-- to work on" depends on the client -- 1,000 visits a month is noise
-- next to a national marketplace and a serious rival next to a
-- single-location business -- so the cut is made per client against the
-- distribution, and recorded in in_scope.
alter table meta.site_competitors
    add column organic_etv        numeric,
    add column organic_keywords   integer,
    add column organic_top_10     integer,
    add column metrics_checked_at timestamptz;

comment on column meta.site_competitors.organic_etv is
    'DataForSEO estimated monthly organic traffic. Null means never '
    'measured, which is different from measured-and-small.';

comment on column meta.site_competitors.organic_top_10 is
    'Ranked keywords in positions 1-10. Often a better signal than '
    'traffic alone: fifteen page-one rankings in our markets matters '
    'more than one viral post.';

comment on column meta.site_competitors.metrics_checked_at is
    'When the figures above were pulled. A traffic number with no date '
    'cannot be told from a competitor who has shrunk.';
