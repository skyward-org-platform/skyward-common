-- 20261001223000_pipeline_018_work_item_url_held.sql
-- A URL we are deliberately not starting yet, and what it waits on.
--
-- The 10-01 InSoFast review: 60 Canadian-market URLs, 51 of them Optimize,
-- should be "held pending the Canada decision" -- a client question that has
-- not come back yet. Adam chose to hold them on the work item's URLs rather
-- than in the verdict.
--
-- WHY NOT IN wqa.page_verdict. A held Canadian page still has the same right
-- answer: Optimize. The hold is not what should happen to the page, it is
-- whether we are allowed to do it yet, and those are different questions.
-- Putting it in the action set would make every downstream reader handle a
-- value that is not an instruction.
--
-- WHY A NEW reason_kind RATHER THAN REUSING ONE. The two existing values both
-- say the work is finished with:
--     no_issue  -- we looked and there was nothing wrong
--     not_done  -- somebody did not do it
-- A page held pending a client answer is neither. It is work we have not
-- started on purpose, and it must come BACK when the answer arrives. Filed
-- under either existing value it would read as closed and nobody would
-- revisit it.
--
-- waits_on IS THE POINT, not decoration. A hold with no stated trigger is
-- indistinguishable from a page somebody forgot: the review found the Canada
-- decision sitting in an open CLIENT TASK, so the hold can name it and be
-- released when it closes. Required whenever reason_kind is 'held', because a
-- hold nobody can release is just an exclusion with better manners.
--
-- Phase 2 asked for the exact field and value before switching its exclusion
-- filter on, so for the record: a held URL is
--     state       = 'excluded'
--     reason_kind = 'held'
--     reason      -- the human sentence to show
--     waits_on    -- what releases it

begin;

alter table pipeline.work_item_url
    add column if not exists waits_on text;

alter table pipeline.work_item_url
    drop constraint if exists work_item_url_reason_kind_check;

alter table pipeline.work_item_url
    add constraint work_item_url_reason_kind_check
    check (reason_kind is null
           or reason_kind = any (array['no_issue', 'not_done', 'held']));

-- A hold has to say what releases it. Nothing else requires waits_on, and
-- nothing else may carry it: a "waits_on" on a closed exclusion would read as
-- a hold to anybody filtering on the column rather than on the kind.
alter table pipeline.work_item_url
    drop constraint if exists work_item_url_hold_says_what_it_waits_on;

alter table pipeline.work_item_url
    add constraint work_item_url_hold_says_what_it_waits_on
    check ((reason_kind = 'held')
           = (waits_on is not null and length(btrim(waits_on)) > 0));

comment on column pipeline.work_item_url.waits_on is
    'What releases this hold -- an open question, a client decision, a '
    'ClickUp task. Required when reason_kind = ''held'' and forbidden '
    'otherwise, because a hold nobody can release is just an exclusion, and '
    'a waits_on on a closed exclusion reads as a hold to anybody filtering '
    'on the column instead of the kind.';

comment on column pipeline.work_item_url.reason_kind is
    'Why this URL is excluded from its work item. no_issue = we looked and '
    'there was nothing wrong. not_done = somebody did not do it. held = we '
    'are deliberately not starting yet and it must come back when waits_on '
    'is answered. The first two are finished with; the third is not, which '
    'is why it could not reuse either.';

commit;
