-- pipeline_034: one definition of what a stored reading MEANS.
--
-- The operator dashboard reads Supabase through its own API and cannot
-- import this pipeline's Python, so putting the read rules in a dashboard
-- endpoint would make a second copy of them -- and series.py's own
-- docstring says why that is the wrong answer: the rules below are the
-- same rules the writer enforces, and two copies drift. The dashboard
-- repo already has the pattern for this and states the reason better than
-- I would: it reads the Library through v_* views, "a contract the
-- Library maintains for us", so a schema change underneath cannot
-- silently empty a page.
--
-- So the same contract, for readings. Two views:
--
--   v_page_snapshot_reading   one row per page per reading, with its
--                             header, its action, the expectation that
--                             applies to it, and its measures LABELLED
--   v_page_snapshot_moment    one row per reading, with its page and
--                             control counts
--
-- THE LABELLING IS THE WHOLE POINT, and it is the module's first promise:
-- a number nobody computed must never be shown as a number. Three
-- outcomes per measure, matching store.py exactly:
--
--   stored            settled, or a cached reading nobody can recompute.
--                     Its values are real and are kept.
--   unavailable       unread. It says why; there is nothing to show.
--   needs_recompute   a lagging source. Its values are REMOVED here, so
--                     a consumer cannot render them by accident, and the
--                     window is right there to recompute from.
--
-- WHY VALUES ARE STRIPPED RATHER THAN LEFT FOR THE CALLER TO IGNORE: a
-- provisional measure rendered as "0 clicks" is the failure the three
-- states exist to prevent, it has happened twice in this codebase, and
-- the second time it was one view labelling and another not.
--
-- Normalised url: scheme, a leading www. and a trailing slash stripped,
-- lowercased. WQA keeps every form of a URL apart on purpose because they
-- are different things to AUDIT; for "is this the same page" they are not,
-- and one page held four spellings across six actions on busbank. Joining
-- on this is how a page gets ONE timeline.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. The dashboard's reader role is granted select on the
-- pipeline schema already, which is why this is a view and not a table.

create or replace view pipeline.v_page_snapshot_reading as
select
    r.page_snapshot_row_id,
    h.page_snapshot_id,
    h.domain_id,
    h.project_id,
    h.work_item_id,
    wi.slug                           as action_slug,
    wi.title                          as action_title,
    wi.module                         as action_module,
    wi.status                         as action_status,
    wi.completed_at                   as action_completed_at,
    h.moment,
    h.as_of,
    h.window_start,
    h.window_end,
    h.prior_window_start,
    h.prior_window_end,
    h.settled_at,
    h.trustworthy,
    h.spend_usd,
    h.note,
    h.sources_complete_through,
    h.sources_complete_from,
    h.audit_module,
    h.audit_version,
    r.url,
    -- The join key for "the same page, however it is spelled".
    regexp_replace(regexp_replace(lower(r.url), '^https?://(www\.)?', ''),
                   '/$', '')          as url_key,
    r.is_control,
    r.controls_urls,
    r.url_state,
    r.url_reason_kind,
    r.expected_change,
    r.page_type,
    r.service_category,
    r.market,
    r.cluster_id,
    r.cluster_keywords,
    r.status_code,
    r.indexability,
    r.google_indexed,
    r.redirect_final_url,
    r.redirect_final_status,
    r.canonical_mismatch,
    r.in_sitemap,
    r.current_title,
    r.h1,
    r.meta_description,
    r.word_count,
    r.inlinks,
    r.outlinks,
    r.page_depth,
    r.action,
    r.keyword_ranks,
    r.earning_queries,
    r.map_rank,
    r.ai_citations,
    r.unread_reason,
    -- THE PAGE'S OWN EXPECTATION WINS over its action's, where it has
    -- one. work_item_url carries its own expectation columns and a page
    -- promised something different from its action was being shown the
    -- action's promise.
    coalesce(u.expected_metric, wi.expected_metric)   as expected_metric,
    coalesce(u.expected_direction, wi.expected_direction)
                                                      as expected_direction,
    coalesce(u.expected_magnitude, wi.expected_magnitude)
                                                      as expected_magnitude,
    coalesce(u.expected_magnitude_unit, wi.expected_magnitude_unit)
                                                  as expected_magnitude_unit,
    (u.expected_metric is not null) as expectation_is_the_pages_own,
    r.measures                        as measures_raw,
    coalesce((
        select jsonb_object_agg(src, labelled)
          from jsonb_each(coalesce(r.measures, '{}'::jsonb)) as m(src, env)
          cross join lateral (
              select case
                  when m.env->>'state' = 'unread'
                      then m.env || jsonb_build_object(
                          'availability', 'unavailable')
                  when m.env->>'state' = 'settled'
                       or m.env->>'provenance' = 'cached'
                      then m.env || jsonb_build_object(
                          'availability', 'stored')
                  else (m.env - 'values') || jsonb_build_object(
                          'availability', 'needs_recompute')
              end as labelled
          ) as lab
    ), '{}'::jsonb)                   as measures
  from pipeline.page_snapshot_row r
  join pipeline.page_snapshot h using (page_snapshot_id)
  join pipeline.work_item wi on wi.work_item_id = h.work_item_id
  left join pipeline.work_item_url u
         on u.work_item_id = h.work_item_id
        and regexp_replace(regexp_replace(lower(u.url),
                           '^https?://(www\.)?', ''), '/$', '')
          = regexp_replace(regexp_replace(lower(r.url),
                           '^https?://(www\.)?', ''), '/$', '');

comment on view pipeline.v_page_snapshot_reading is
    'One row per page per reading, with its header, its action, the '
    'expectation that applies to it, and its measures labelled stored / '
    'unavailable / needs_recompute. A needs_recompute measure has its '
    'values REMOVED: the window is stored so a reader recomputes, and a '
    'lagging number rendered as a measurement is the failure the three '
    'states exist to prevent. Join on url_key to get one page''s whole '
    'timeline across every spelling of it.';

create or replace view pipeline.v_page_snapshot_moment as
select
    h.page_snapshot_id,
    h.domain_id,
    h.work_item_id,
    wi.slug                           as action_slug,
    wi.title                          as action_title,
    wi.completed_at                   as action_completed_at,
    h.moment,
    h.as_of,
    h.window_start,
    h.window_end,
    h.settled_at,
    h.trustworthy,
    h.spend_usd,
    h.note,
    h.job_id,
    h.fronts_measured,
    h.sources_complete_through,
    h.sources_complete_from,
    count(*) filter (where not r.is_control)                 as pages,
    count(*) filter (where r.is_control)                     as controls,
    count(*) filter (where r.url_state = 'excluded')         as excluded,
    count(*) filter (where r.url_reason_kind = 'held')       as held
  from pipeline.page_snapshot h
  join pipeline.work_item wi on wi.work_item_id = h.work_item_id
  left join pipeline.page_snapshot_row r using (page_snapshot_id)
 group by h.page_snapshot_id, h.domain_id, h.work_item_id, wi.slug,
          wi.title, wi.completed_at, h.moment, h.as_of, h.window_start,
          h.window_end, h.settled_at, h.trustworthy, h.spend_usd, h.note,
          h.job_id, h.fronts_measured, h.sources_complete_through,
          h.sources_complete_from;

comment on view pipeline.v_page_snapshot_moment is
    'One row per reading: its action, its window, whether it settled, '
    'what it spent, and how many pages, controls, exclusions and holds '
    'it covered.';
