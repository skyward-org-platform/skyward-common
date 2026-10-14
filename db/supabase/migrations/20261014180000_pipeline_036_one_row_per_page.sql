-- pipeline_036: one row per page per reading, which 035 broke.
--
-- v_page_snapshot_reading left-joins work_item_url to let a page's OWN
-- expectation beat its action's. That join is on the normalised url, and
-- an action can legitimately list TWO forms of one page: a redirect map
-- names busbank.com/charter-bus-pricing as redirected_away AND
-- www.busbank.com/charter-bus-pricing as receives_redirect. Both
-- normalise to the same key, so every snapshot row for that page came
-- back TWICE, and the page view showed one reading as two columns with
-- different numbers in each.
--
-- Caught on BusBank 2026-10-14, by Adam, as "it's now saying there are
-- two separate actions". The duplicate is not a cosmetic problem: a count
-- over this view is wrong by however many URL forms an action lists, and
-- nothing about the duplicate looks wrong on its own.
--
-- The fix is a lateral taking ONE work_item_url row, preferring the row
-- whose url is spelled exactly as the snapshot's, then the one that
-- carries an expectation, then any. A LEFT JOIN that can match more than
-- once is a row multiplier wherever it appears, and the only safe shapes
-- are a unique key or an explicit pick.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

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
