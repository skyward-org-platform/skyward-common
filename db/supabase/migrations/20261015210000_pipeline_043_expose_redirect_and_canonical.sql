-- pipeline_043: expose two columns the reading already stored.
--
-- `page_snapshot_row` has carried `redirect_hops` and
-- `canonical_link_element` since the module was written, populated from
-- Phase 1's audit -- 2,293 of BusBank's 2,513 rows hold a canonical tag.
-- The reading VIEW never selected either, so two of Phase 1's fourteen
-- declarations could not be judged at all: `redirect_hops reaches 1`,
-- which 87 live pages across three sites fail, and `canonical_present
-- reaches 1`. Both came back not_measurable for every page.
--
-- No new columns. This is the view catching up with the table, which is
-- why it is a replace and nothing is backfilled.
--
-- WHY canonical_present IS DERIVED FROM THE TAG AND NOT FROM THE
-- MISMATCH. They are two facts and our own data proves it: 78 rows carry
-- `canonical_mismatch = true` while holding no canonical tag, and 2,533
-- InSoFast rows hold a tag with a NULL mismatch, because mismatch is
-- only computed where there is something to compare. A derivation in
-- either direction would be wrong on live rows.
--
-- GRANTS ARE RE-ISSUED HERE, AND THAT IS NOT BOILERPLATE. `create or
-- replace view` cannot insert a column into the middle of the select
-- list -- it renames instead, and Postgres refuses -- so this drops and
-- recreates. A dropped view loses its ACL, and this one carries an
-- explicit `dashboard_ro=r`: without re-granting, the operator
-- dashboard's read-only role loses the view and every snapshot screen
-- goes blank. Checked against the live ACL before writing this
-- (postgres, adam=arwd, pipeline_bot=arwd, dashboard_ro=r).

drop view if exists pipeline.v_page_snapshot_reading;

create view pipeline.v_page_snapshot_reading as
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
        -- ALREADY STORED, NEVER EXPOSED. The row has carried both
        -- since the start and the view simply did not select them,
        -- so two of Phase 1's declarations -- redirect_hops reaches
        -- 1, canonical_present reaches 1 -- could never be judged.
        r.redirect_hops, r.canonical_link_element,
        -- PRESENCE IS THE TAG, NEVER THE MISMATCH. They are two
        -- facts: mismatch is only computed where there is something
        -- to compare, so a NULL mismatch is not absence of a tag,
        -- and a true mismatch is not presence of one either.
        --
        -- AND IT IS BLANK FAR MORE OFTEN THAN NULL, which is the
        -- trap. `is not null` would have called 1,327 empty BusBank
        -- strings a canonical tag and reported 925 InSoFast pages
        -- that answer 200 with no canonical as correctly
        -- canonicalised -- the exact population the Canonicalize
        -- action exists for (P1-P2 measured it, 2026-10-15, after
        -- first measuring it the wrong way). Phase 1's own triage
        -- has always been right here because it goes through a text
        -- helper that treats blank and null alike; this matches it.
        --
        -- NULL WHERE NOTHING WAS CRAWLED, because an absent tag on a
        -- page nobody fetched is not the same as a page with no
        -- canonical, and false would claim the second.
        case when r.status_code is null then null
             else coalesce(btrim(r.canonical_link_element), '') <> ''
        end as canonical_present,
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

grant select, insert, update, delete
    on pipeline.v_page_snapshot_reading to adam, pipeline_bot;
grant select on pipeline.v_page_snapshot_reading to dashboard_ro;
