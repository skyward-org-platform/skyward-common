-- pipeline_053: whether a page needed the work, and what the work was.
--
-- THE DENOMINATOR PROBLEM, and it is not a display one. On busbank's
-- redirect map, 298 of 366 subjects already answered 301 at baseline and
-- 294 already had one hop. So "6 against expectation" out of 843 checks
-- reads as nothing happening, when the truth is that only about 71 pages
-- needed the change at all (2 whose chain loops, 1 dead destination, 68
-- with no destination chosen). A number against 366 reads as near-total
-- success before anybody touches the site.
--
-- A VERDICT CANNOT RECOVER THIS. `already_met` is the closest it gets,
-- and it cannot tell "this page already answered 301" from "there was
-- nothing to do on this page", because by the time the judge runs both
-- look like a page that was compliant before the change. P1-P2
-- (2026-10-16) confirmed the distinction exists upstream and is simply
-- not stored: `redirect_slices` groups every subject by cause at the
-- moment the item is created, and then throws the grouping away.
--
-- TWO COLUMNS, AND THE SPLIT IS THE POINT.
--
--   needed_work   whether this page had anything to do at all. The only
--                 cross-action-type fact here, and the one the
--                 denominator needs. A boolean nobody has to agree a
--                 vocabulary for.
--   work_kind     what the work was, in the action type's OWN words. No
--                 check constraint, because the vocabulary is per action
--                 type and a constraint listing the redirect map's five
--                 values would refuse p1-optimize's.
--
-- Written in that order deliberately. A single `work_kind` with a
-- reserved 'already_correct' value would have made the denominator
-- depend on every action type spelling that one value the same way, and
-- the first type to spell it differently would overstate its own success
-- with nothing failing.
--
-- NULL MEANS NOBODY RECORDED IT, NOT "needed nothing". Phase 1's
-- existing 4,168 rows predate this and are all null; a reader counting a
-- denominator from it has to say how many it could not classify rather
-- than treating null as a page that needed work or as one that did not.
--
-- NOT YET ON THE SNAPSHOT. page_snapshot_row freezes url_state,
-- url_reason_kind and expected_change at reading time and this belongs
-- beside them, but that is a change to the writer as well as the schema
-- and pipeline_052 is still unapplied. This migration exists so the
-- Phase 1 side can start writing the values; carrying them onto the
-- reading is a follow-up.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.work_item_url
    add column if not exists needed_work boolean;

alter table pipeline.work_item_url
    add column if not exists work_kind text;

comment on column pipeline.work_item_url.needed_work is
    'Whether this page had anything to do at all when the action was '
    'raised. FALSE means the audit covered it and it was already as the '
    'action wanted -- 298 of busbank''s 366 redirect subjects already '
    'answered 301. NULL means nobody recorded it, which is not the same '
    'as false: a count of what an action was ever judged on has to '
    'report how many rows it could not classify. This is the only '
    'cross-action-type fact here, which is why it is separate from '
    'work_kind.';

comment on column pipeline.work_item_url.work_kind is
    'What the work on this page was, in the action type''s own '
    'vocabulary. The redirect map''s are redirect_loop, dead_destination, '
    'chain_to_collapse, ready_to_ship and destination_tbd (see '
    'phase_1_wqa/clickup_tasks.py:redirect_slices, which already groups '
    'by this and then discards it). Deliberately unconstrained: a check '
    'listing one action type''s values would refuse every other type''s. '
    'Read it for WHAT was done; read needed_work for WHETHER anything '
    'was.';

create index if not exists work_item_url_needed_work
    on pipeline.work_item_url (work_item_id)
    where needed_work;
