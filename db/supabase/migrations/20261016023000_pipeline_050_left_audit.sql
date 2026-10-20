-- pipeline_050: a page can leave the audit, which is not a finding.
--
-- When Phase 1 re-derives and stops keeping a page as a target, Phase 2
-- marks that page's row excluded. The three kinds available say the
-- wrong thing about it:
--
--   no_issue  our finding was wrong, the page is fine. NOT what happened
--             -- nobody looked at the page and found it fine, it left
--             the set being audited.
--   not_done  we did not do it. Also untrue.
--   held      waiting on something. Nothing is being waited for.
--
-- `no_issue` is the one in use today and it is the most misleading of
-- the three, because "our finding was wrong" and "this page is no
-- longer in scope" have OPPOSITE implications for whether the work
-- succeeded. A reader counting no_issue rows as false positives would
-- be counting re-derivations as mistakes.
--
-- `left_audit` names what happened rather than who decided, which is
-- the point: nobody decided. It is deliberately not `out_of_scope`,
-- because `in_scope` already means a deliberate human exclusion
-- elsewhere in this schema (markets, competitors) and reusing the word
-- would suggest somebody chose this.
--
-- BOTH CONSTRAINTS, AND THAT IS THE WHOLE REASON THIS IS ONE MIGRATION.
-- The vocabulary is checked in two places: on work_item_url, where
-- Phase 2 writes it, and on page_snapshot_row, where a reading copies
-- it. A value added to the first alone would be written happily and
-- then fail on the next reading of that action -- and because a reading
-- writes its rows in one batch, that is the whole reading lost, not one
-- row. Exactly the shape of pipeline_049 two hours earlier, where a
-- direction was added to four tables and the outcome check on a fifth
-- was missed.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.work_item_url
    drop constraint if exists work_item_url_reason_kind_check;
alter table pipeline.work_item_url
    add constraint work_item_url_reason_kind_check check (
        reason_kind is null
        or reason_kind in ('no_issue', 'not_done', 'held', 'left_audit'));

alter table pipeline.page_snapshot_row
    drop constraint if exists page_snapshot_row_url_reason_kind_check;
alter table pipeline.page_snapshot_row
    add constraint page_snapshot_row_url_reason_kind_check check (
        url_reason_kind is null
        or url_reason_kind in ('no_issue', 'not_done', 'held',
                               'left_audit'));

comment on column pipeline.work_item_url.reason_kind is
    'Why this page is excluded. no_issue: the finding was wrong and the '
    'page is fine. not_done: it was not done. held: it waits on '
    'something, which waits_on names. left_audit: the page is no longer '
    'in the audit this item came from, so nobody found it fine and '
    'nobody decided anything -- it left the set.';
