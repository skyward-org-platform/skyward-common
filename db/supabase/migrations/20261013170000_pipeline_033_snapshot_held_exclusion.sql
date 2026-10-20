-- pipeline_033: a URL excluded as HELD can be stored in a reading.
--
-- pipeline_032 copied work_item_url's exclusion vocabulary onto
-- page_snapshot_row and copied the WRONG VERSION of it. The source column
-- allows three reason kinds and the copy allowed two:
--
--     work_item_url.reason_kind        no_issue, not_done, held
--     page_snapshot_row.url_reason_kind   no_issue, not_done
--
-- 'held' is not a rare third case. Phase 1's completion step writes it for
-- every page deliberately deferred and waiting on something -- usually
-- Phase 3 -- and pairs it with a non-blank waits_on. So a reading of any
-- action holding such a page raised a check violation and stored NOTHING:
-- not the held page, not the other 78 pages beside it, because the rows go
-- in one statement (P0-Testing, 2026-10-13, finding 12).
--
-- The lesson, written down because the same shape will come up again: when
-- a column exists to carry another column's value, the two vocabularies
-- have to be the same list, and copying one by hand at a point in time is
-- how they stop being. 032's own comment says "same vocabulary as the
-- source column, deliberately" directly above a narrower list.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

alter table pipeline.page_snapshot_row
    drop constraint if exists page_snapshot_row_url_reason_kind_check;

alter table pipeline.page_snapshot_row
    add constraint page_snapshot_row_url_reason_kind_check
        check (url_reason_kind is null
               or url_reason_kind in ('no_issue', 'not_done', 'held'));

comment on column pipeline.page_snapshot_row.url_reason_kind is
    'Why the URL was excluded from the action, copied from '
    'work_item_url.reason_kind at reading time. Same three values, and it '
    'has to stay the same three: no_issue (looked at, nothing wrong), '
    'not_done (right, still outstanding), held (right, deliberately '
    'deferred and waiting on something).';
