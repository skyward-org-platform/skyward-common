-- pipeline_035: the confounding rule, in the one place both readers see.
--
-- pipeline_034 moved the measure labelling into a view so the dashboard
-- could not grow a second copy of it. This finishes the job: the rule that
-- decides whether a reading can be ATTRIBUTED to its own action was still
-- only in series.py, so a dashboard panel would have had to re-derive it
-- -- and getting it slightly different is worse than not having it, since
-- the whole point is to stop somebody claiming a movement two changes
-- could explain.
--
-- THE RULE. A reading measures a window. If ANOTHER action that named this
-- same page closed inside that window, the reading cannot be attributed to
-- either action, and it says which other action and when. Completion is
-- the event that matters: an action open but unshipped has not moved the
-- page, and an action with no completion date is not yet a confounder --
-- it becomes one when it closes, and the reading is re-read then.
--
-- THE SAME PAGE, HOWEVER SPELLED. Joined on the normalised url, because
-- busbank holds four spellings of /get-a-quote across six actions and an
-- exact match called those readings clean.
--
-- WHICH DAY A CLOSE FELL ON DEPENDS ON THE TIMEZONE, and nothing records
-- which one it was meant in. Three cases, matching the Python exactly:
--
--   a midnight-UTC timestamp is a DATE SOMEBODY TYPED -- `p1-wqa
--   --completed-at` takes a date from the operator, and that is every
--   close in this database today -- so it counts as that one day;
--
--   a real time of day is read BOTH in UTC and in the agency's timezone,
--   and lands if either day is in the window. The band where those differ
--   is 00:00:01 to 03:59:59 UTC, which is the Eastern evening, so a close
--   there from somebody working Eastern hours is a genuine evening close;
--
--   boundary_uncertain records a disagreement between the two, rather
--   than resolving it. Confounded is the cautious label and the one we
--   can stand behind.
--
-- America/New_York is the AGENCY's timezone, not the site's. meta.site has
-- no timezone column (2026-10-14), so a client east of UTC can have a
-- close whose local day is the UTC day PLUS one, which neither reading
-- produces. Decided with Adam: one timezone for everything, documented,
-- rather than a column nobody can fill today.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

create or replace view pipeline.v_page_snapshot_reading as
with labelled as (
    select
        r.page_snapshot_row_id,
        h.page_snapshot_id,
        h.domain_id,
        h.project_id,
        h.work_item_id,
        wi.slug                       as action_slug,
        wi.title                      as action_title,
        wi.module                     as action_module,
        wi.status                     as action_status,
        wi.completed_at               as action_completed_at,
        h.moment, h.as_of, h.window_start, h.window_end,
        h.prior_window_start, h.prior_window_end,
        h.settled_at, h.trustworthy, h.spend_usd, h.note,
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
        r.meta_description, r.word_count, r.inlinks, r.outlinks,
        r.page_depth, r.action, r.keyword_ranks, r.earning_queries,
        r.map_rank, r.ai_citations, r.unread_reason,
        coalesce(u.expected_metric, wi.expected_metric) as expected_metric,
        coalesce(u.expected_direction, wi.expected_direction)
                                                       as expected_direction,
        coalesce(u.expected_magnitude, wi.expected_magnitude)
                                                       as expected_magnitude,
        coalesce(u.expected_magnitude_unit, wi.expected_magnitude_unit)
                                                  as expected_magnitude_unit,
        (u.expected_metric is not null) as expectation_is_the_pages_own,
        r.measures                    as measures_raw,
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
        ), '{}'::jsonb)               as measures
      from pipeline.page_snapshot_row r
      join pipeline.page_snapshot h using (page_snapshot_id)
      join pipeline.work_item wi on wi.work_item_id = h.work_item_id
      left join pipeline.work_item_url u
             on u.work_item_id = h.work_item_id
            and regexp_replace(regexp_replace(lower(u.url),
                               '^https?://(www\.)?', ''), '/$', '')
              = regexp_replace(regexp_replace(lower(r.url),
                               '^https?://(www\.)?', ''), '/$', '')
)
select
    l.*,
    coalesce(c.confounded_by, '[]'::jsonb) as confounded_by,
    (c.confounded_by is not null)          as is_confounded
  from labelled l
  left join lateral (
      select jsonb_agg(jsonb_build_object(
                 'work_item_id', other.work_item_id,
                 'slug',         other.slug,
                 'title',        other.title,
                 'completed_at', other.completed_at,
                 -- True when the two readings of the day disagree, which
                 -- only a real time of day in the Eastern evening can do.
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
         -- A midnight-UTC timestamp is a typed date: that one day only.
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
    'One row per page per reading: the header, the action, the expectation '
    'that applies to that page (the page''s own beats its action''s), the '
    'measures labelled stored / unavailable / needs_recompute with a '
    'needs_recompute measure''s values REMOVED, and confounded_by -- the '
    'other actions that changed this same page inside this reading''s '
    'window, which is what stops a movement being claimed for one action '
    'when two could explain it. Join on url_key for one page''s whole '
    'timeline across every spelling of it.';
