-- pipeline_052: the reading view reads the expectation table.
--
-- UNFINISHED WORK OF MINE, and the reason Paul saw nothing on the call.
-- pipeline_041 moved what an action promises into
-- pipeline.work_item_expectation, one row per metric per side, and P1-P2
-- was told to write ONLY there. The judge was moved onto it. THE VIEW WAS
-- NOT. It still reads coalesce(work_item_url.x, work_item.x), and both
-- are empty on every Phase 1 row -- all 4,168 work_item_url rows and all
-- 8 Phase 1 actions on busbank. So every Phase 1 reading served a null
-- expectation, `expectation_is_the_pages_own` was false everywhere, and
-- the dashboard's "Expected of this page" rendered "clicks null" or
-- nothing at all. Paul: "nothing like expected to change because it
-- hasn't been set yet." It had been set. The view could not see it.
--
-- ONE SCALAR CANNOT HOLD THREE PROMISES, which is why this adds an array
-- rather than only repointing the existing columns. p1-redirect-map
-- promises status_code, redirect_hops and impressions on its subject
-- side; p1-canonicalize promises two. The scalar columns stay, carrying
-- the FIRST applying promise so that every existing reader keeps working
-- unchanged, and the new `expectations` column carries all of them so a
-- reader that wants the whole promise can have it.
--
-- THE RESOLUTION RULE IS THE JUDGE'S, COPIED NOT REINVENTED (see
-- verdict.py:_for_page). Three parts, in order:
--
--   1. A PAGE'S OWN expectation on work_item_url REPLACES the action's
--      promises for that page. It does not add to them. The case it
--      exists for is Phase 2's 645 breadcrumb-only pages, sitting on an
--      item that promises rich-result impressions they cannot move:
--      judging them against it as well would report the work as failed
--      on exactly the pages the override was written to excuse.
--   2. Otherwise, the promises whose side matches -- a promise naming a
--      side applies only to pages whose expected_change is that side,
--      one naming no side applies to every page, and where both exist
--      the side-specific one wins. "This page loses impressions" is more
--      specific than "every page gains clicks".
--   3. Otherwise, the item's own single expectation column, which is
--      where Phase 2's thirteen live items still carry theirs. The
--      fallback is deliberate and stays until nothing holds an
--      expectation there.
--
-- CREATE OR REPLACE, NOT DROP AND RECREATE. A dropped view loses its
-- ACL, including the explicit dashboard_ro grant, and pipeline_043 and
-- 044 both had to re-issue it after dropping. Replacing keeps it. The
-- price is that existing columns must keep their names, types and order,
-- so `expectations` is appended at the very end of the select list and
-- the four scalar columns change only their expressions.
--
-- Grants: re-issued anyway, so this file states the view's access
-- whether or not the replace preserved it.

create or replace view pipeline.v_page_snapshot_reading as
with labelled as (
    select
        r.page_snapshot_row_id, h.page_snapshot_id, h.domain_id,
        h.project_id, h.work_item_id,
        wi.slug as action_slug, wi.title as action_title,
        wi.module as action_module, wi.status as action_status,
        wi.completed_at as action_completed_at,
        h.moment, h.as_of, h.window_start, h.window_end,
        h.prior_window_start, h.prior_window_end,
        h.settled_at, h.trustworthy, h.spend_usd, h.note, h.is_sample,
        h.sources_complete_through, h.sources_complete_from,
        h.audit_module, h.audit_version,
        r.url,
        regexp_replace(regexp_replace(lower(r.url), '^https?://(www\.)?',
                       ''), '/$', '')  as url_key,
        r.is_control, r.controls_urls, r.url_state, r.url_reason_kind,
        r.expected_change, r.page_type, r.service_category, r.market,
        r.cluster_id, r.cluster_keywords, r.status_code, r.indexability,
        r.google_indexed, r.redirect_final_url, r.redirect_final_status,
        r.canonical_mismatch, r.in_sitemap, r.current_title, r.h1,
        r.redirect_hops, r.canonical_link_element,
        -- PRESENCE IS THE TAG, NEVER THE MISMATCH, and blank counts as
        -- absent. Unchanged from pipeline_044; see its note for the
        -- 1,327 empty strings `is not null` would have called a tag.
        case when r.status_code is null then null
             else coalesce(btrim(r.canonical_link_element), '') <> ''
        end as canonical_present,
        r.meta_description, r.word_count, r.inlinks, r.outlinks,
        r.page_depth, r.action, r.keyword_ranks, r.earning_queries,
        r.map_rank, r.ai_citations, r.unread_reason,
        -- THE PAGE'S OWN, THEN THE ACTION'S PROMISE, THEN THE ITEM'S
        -- COLUMN. Same order as the judge, and the middle term is what
        -- pipeline_041 added and this view never read.
        coalesce(u.expected_metric, p.expected_metric, wi.expected_metric)
            as expected_metric,
        coalesce(u.expected_direction, p.expected_direction,
                 wi.expected_direction) as expected_direction,
        coalesce(u.expected_magnitude, p.expected_magnitude,
                 wi.expected_magnitude) as expected_magnitude,
        coalesce(u.expected_magnitude_unit, p.expected_magnitude_unit,
                 wi.expected_magnitude_unit) as expected_magnitude_unit,
        (u.expected_metric is not null) as expectation_is_the_pages_own,
        r.measures as measures_raw,
        coalesce((
            select jsonb_object_agg(src, labelled)
              from jsonb_each(coalesce(r.measures, '{}'::jsonb))
                   as m(src, env)
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
        ), '{}'::jsonb) as measures
      from pipeline.page_snapshot_row r
      join pipeline.page_snapshot h using (page_snapshot_id)
      join pipeline.work_item wi on wi.work_item_id = h.work_item_id
      -- ONE row, chosen, never a join that can match twice.
      left join lateral (
          select u2.expected_metric, u2.expected_direction,
                 u2.expected_magnitude, u2.expected_magnitude_unit
            from pipeline.work_item_url u2
           where u2.work_item_id = h.work_item_id
             and regexp_replace(regexp_replace(lower(u2.url),
                                '^https?://(www\.)?', ''), '/$', '')
               = regexp_replace(regexp_replace(lower(r.url),
                                '^https?://(www\.)?', ''), '/$', '')
           order by (u2.url = r.url) desc,
                    (u2.expected_metric is not null) desc,
                    u2.url
           limit 1
      ) as u on true
      -- THE ONE the scalar columns carry: the first of the same set, in
      -- the same order, so the scalar is always a promise the array
      -- also holds rather than a fifth answer.
      left join lateral (
          select e.expected_metric, e.expected_direction,
                 e.expected_magnitude, e.expected_magnitude_unit
            from pipeline.work_item_expectation e
           where e.work_item_id = h.work_item_id
             and (e.expected_change is null
                  or e.expected_change = r.expected_change)
             and (e.expected_change is not null
                  or not exists (
                      select 1 from pipeline.work_item_expectation e2
                       where e2.work_item_id = h.work_item_id
                         and e2.expected_change = r.expected_change))
           order by e.expected_metric
           limit 1
      ) as p on true
)
select
    l.*,
    coalesce(c.confounded_by, '[]'::jsonb) as confounded_by,
    (c.confounded_by is not null)          as is_confounded,
    -- EVERY PROMISE THAT APPLIES, because one scalar cannot hold
    -- three. APPENDED HERE rather than beside the scalar columns
    -- for one reason: create-or-replace allows a new column only at
    -- the end of the select list, and keeping the ACL is worth more
    -- than having the expectation columns adjacent.
    --
    -- The page's own override is the whole array when it exists,
    -- since it REPLACES the action's promises rather than adding to
    -- them. With no promises recorded, the scalar came from the
    -- item's own column and the array says so, so the two can never
    -- disagree about what this page was judged against.
    case
        when l.expectation_is_the_pages_own then jsonb_build_array(
            jsonb_build_object(
                'metric',         l.expected_metric,
                'direction',      l.expected_direction,
                'magnitude',      l.expected_magnitude,
                'magnitude_unit', l.expected_magnitude_unit,
                'side',           l.expected_change,
                'is_the_pages_own', true))
        else coalesce(ex.promises, case
            when l.expected_metric is null then '[]'::jsonb
            else jsonb_build_array(jsonb_build_object(
                'metric',         l.expected_metric,
                'direction',      l.expected_direction,
                'magnitude',      l.expected_magnitude,
                'magnitude_unit', l.expected_magnitude_unit,
                'side',           null,
                'is_the_pages_own', false))
        end)
    end as expectations
  from labelled l
  -- THE PROMISES THAT APPLY TO THIS PAGE'S SIDE. A side-specific
  -- promise is kept whenever it matches; a promise naming no side is
  -- kept only where no side-specific promise matches this page, which
  -- is the judge's "specific or mine". Same predicate as the scalar
  -- lateral above, so the first element and the scalar agree.
  left join lateral (
      select jsonb_agg(jsonb_build_object(
                 'metric',         e.expected_metric,
                 'direction',      e.expected_direction,
                 'magnitude',      e.expected_magnitude,
                 'magnitude_unit', e.expected_magnitude_unit,
                 'side',           e.expected_change,
                 'is_the_pages_own', false)
             order by e.expected_metric) as promises
        from pipeline.work_item_expectation e
       where e.work_item_id = l.work_item_id
         and (e.expected_change is null
              or e.expected_change = l.expected_change)
         and (e.expected_change is not null
              or not exists (
                  select 1 from pipeline.work_item_expectation e2
                   where e2.work_item_id = l.work_item_id
                     and e2.expected_change = l.expected_change))
  ) as ex on true
  left join lateral (
      select jsonb_agg(jsonb_build_object(
                 'work_item_id', other.work_item_id,
                 'slug',         other.slug,
                 'title',        other.title,
                 'completed_at', other.completed_at,
                 'boundary_uncertain',
                 (other.completed_at at time zone 'UTC')
                     <> date_trunc('day', other.completed_at
                                   at time zone 'UTC')
                 and ((other.completed_at at time zone 'UTC')::date
                          between l.window_start and l.window_end)
                     <> ((other.completed_at at time zone 'America/New_York')
                          ::date between l.window_start and l.window_end)
             ) order by other.completed_at) as confounded_by
        from (
            select distinct wi2.work_item_id, wi2.slug, wi2.title,
                   wi2.completed_at
              from pipeline.work_item wi2
              join pipeline.work_item_url u2
                   on u2.work_item_id = wi2.work_item_id
             where wi2.domain_id = l.domain_id
               and wi2.work_item_id <> l.work_item_id
               and wi2.completed_at is not null
               and regexp_replace(regexp_replace(lower(u2.url),
                                  '^https?://(www\.)?', ''), '/$', '')
                 = l.url_key
        ) as other
       where l.window_start is not null
         and l.window_end is not null
         and case
               when (other.completed_at at time zone 'UTC')
                        = date_trunc('day', other.completed_at
                                     at time zone 'UTC')
                   then (other.completed_at at time zone 'UTC')::date
                            between l.window_start and l.window_end
               else ((other.completed_at at time zone 'UTC')::date
                         between l.window_start and l.window_end)
                 or ((other.completed_at at time zone 'America/New_York')
                         ::date between l.window_start and l.window_end)
             end
  ) as c on true;

comment on view pipeline.v_page_snapshot_reading is
    'One row per page per reading, with the expectation it is judged '
    'against resolved the way the judge resolves it: the page''s own '
    'override, else the action''s promises in '
    'pipeline.work_item_expectation whose side matches, else the '
    'item''s own column. The four expected_* columns carry the first '
    'applying promise; `expectations` carries every one, because an '
    'action that promises three metrics cannot be read from a scalar.';

grant select, insert, update, delete
    on pipeline.v_page_snapshot_reading to adam, pipeline_bot;
grant select on pipeline.v_page_snapshot_reading to dashboard_ro;
