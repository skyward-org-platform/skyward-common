-- pipeline_045: the sample flag reaches the action view too.
--
-- pipeline_044 put `is_sample` on pipeline.page_snapshot and exposed it
-- on the reading view. The MOMENT view -- which is what the dashboard's
-- action page reads -- never selected it, so the action view would have
-- reported every fabricated reading as real while the page view marked
-- it. A flag that is right on one screen and wrong on the next is worse
-- than no flag, because the screen that is wrong is the one somebody
-- quotes.
--
-- Replaced rather than dropped: the column is appended at the end of the
-- select list, which is the one change `create or replace view` allows,
-- so this view keeps its grants without re-issuing them.

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
    count(*) filter (where r.url_reason_kind = 'held')       as held,
    -- LAST ON PURPOSE. `create or replace view` can only APPEND a
    -- column; inserting one renames the columns after it and Postgres
    -- refuses. Appending means this view keeps its ACL, unlike
    -- v_page_snapshot_reading, which had to be dropped and regranted.
    h.is_sample
  from pipeline.page_snapshot h
  join pipeline.work_item wi on wi.work_item_id = h.work_item_id
  left join pipeline.page_snapshot_row r using (page_snapshot_id)
 group by h.page_snapshot_id, h.domain_id, h.work_item_id, wi.slug,
          h.is_sample,
          wi.title, wi.completed_at, h.moment, h.as_of, h.window_start,
          h.window_end, h.settled_at, h.trustworthy, h.spend_usd, h.note,
          h.job_id, h.fronts_measured, h.sources_complete_through,
          h.sources_complete_from;;
