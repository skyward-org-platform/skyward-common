-- 20260921140000_pipeline_008_no_longer_detected.sql
-- A gap the scanner stops seeing closes as no_longer_detected, not answered.
--
-- Ruled by Adam 2026-09-21. `kb gaps --write` closed every gap that had
-- disappeared since the last scan as status 'answered', resolution "filled
-- since this was raised". The scan cannot know that. A gap also disappears
-- when the RULE changes: a column is exempted (the three meta.data_access
-- columns WQA does not read yet), or a row leaves play (a market excluded by
-- the brand_008 backfill). Those closed as "answered" with nothing filled,
-- and status is the one field every reader filters on.
--
-- An edge case -- it shows up because the pipeline is changing so much, not
-- on ordinary runs -- so this is kept proportionate: one more status, the
-- same close guard, and the existing false rows re-marked.
--
-- Readers: every reader in skyward-seo-pipeline treats status = 'open' as
-- outstanding and anything else as closed, so none needs changing. No app
-- reads this table.

alter table pipeline.open_question
    drop constraint if exists open_question_status_check;
alter table pipeline.open_question
    add constraint open_question_status_check
    check (status in ('open', 'answered', 'dismissed', 'no_longer_detected'));

-- Same guard, one more closing status: it still needs answered_by (here
-- always the command, "kb gaps") and still gets answered_at stamped.
create or replace function pipeline.open_question_close_guard()
    returns trigger
    language plpgsql
as $$
begin
    if new.status in ('answered', 'dismissed', 'no_longer_detected')
       and (tg_op = 'INSERT' or old.status is distinct from new.status) then
        if new.answered_by is null or length(trim(new.answered_by)) = 0 then
            raise exception
                'pipeline.open_question: cannot mark a question % without answered_by. Say who or what closed it: a person, the client, or the command (e.g. "kb gaps").',
                new.status
                using errcode = 'check_violation';
        end if;
        if new.answered_at is null then
            new.answered_at := now();
        end if;
    end if;
    return new;
end;
$$;

comment on function pipeline.open_question_close_guard() is
    'On the transition into answered, dismissed or no_longer_detected: '
    'require answered_by, and stamp answered_at if missing. Rows already '
    'closed are not re-checked.';

-- Re-mark the rows the old auto-close wrote. Matched on the exact text only
-- that code path ever wrote, so no human answer can be caught by it.
-- answered_at is kept: it is still when the scan stopped seeing the gap.
update pipeline.open_question
   set status = 'no_longer_detected',
       resolution = 'gap no longer detected by a later scan'
 where status = 'answered'
   and answered_by = 'kb gaps'
   and resolution = 'filled since this was raised; closed by a later gap scan';
