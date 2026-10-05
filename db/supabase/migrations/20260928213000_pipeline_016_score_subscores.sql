-- 20260928213000_pipeline_016_score_subscores.sql
--
-- The five subscores, stored so they can be ordered by.
--
-- Phase 1 stopped reporting one flat score on 2026-09-28. A page is now
-- scored on five dimensions -- Crawlable, Content, Connected, Found,
-- Earning -- and the page score is the average of the ones that could be
-- measured. A dimension whose SOURCE is missing comes back null rather
-- than dragging the page down: busbank's crawl reached 936 of 2,042 URLs,
-- and under the old flat score its unfetched pages were judged on two
-- checks out of eleven and averaged in beside pages judged on all eleven.
--
-- WHY A COLUMN AND NOT A COMPUTATION. Each check already carries the
-- subscore it belongs to inside `components`, which is enough to DISPLAY a
-- page and not enough to ORDER BY one. Adam asked for a Pages table sorted
-- by Content (2026-09-28). The alternatives were to recompute 2,746 pages
-- by 11 checks on every request, or to re-derive the subscore formula in
-- SQL -- and re-deriving a definition in SQL is precisely what cost the
-- Phase 1 workbook two entire sheets and half of every keyword pair when
-- the dashboard reimplemented it. One definition, in the pipeline, written
-- down here.
--
-- Shape: one key per subscore, null where nothing in it was measurable.
--
--   {"crawlable": 96.4, "content": null, "connected": null,
--    "found": 42.3, "earning": 57.2}
--
-- NULL IS NOT ZERO and the distinction is the whole point of the column.
-- Zero says the page failed what we checked; null says we could not look.
-- A reader, a sort and an average must all keep them apart.
--
-- Null on the COLUMN itself means something different again: a score row
-- written before subscores existed. Not a page we could not measure -- a
-- run that predates the measurement.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

alter table pipeline.score
    add column if not exists subscores jsonb;

comment on column pipeline.score.subscores is
    'Crawlable/Content/Connected/Found/Earning for this subject. A null '
    'VALUE means nothing in that subscore could be measured, which is not '
    'zero. A null COLUMN means the row predates subscores entirely.';

-- Sorting a page list by one dimension is the reason this column exists,
-- so it gets an index per dimension rather than one over the whole blob.
create index if not exists score_subscore_crawlable
    on pipeline.score (((subscores ->> 'crawlable')::numeric));
create index if not exists score_subscore_content
    on pipeline.score (((subscores ->> 'content')::numeric));
create index if not exists score_subscore_connected
    on pipeline.score (((subscores ->> 'connected')::numeric));
create index if not exists score_subscore_found
    on pipeline.score (((subscores ->> 'found')::numeric));
create index if not exists score_subscore_earning
    on pipeline.score (((subscores ->> 'earning')::numeric));
