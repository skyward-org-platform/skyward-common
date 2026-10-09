-- 20261009140000_pipeline_027_priority_narrow.sql
--
-- STEP 3 OF 3: the old priority spelling is retired. Adam approved the
-- rename on 2026-10-08 and the three-step sequence; this is the last one.
--
-- Step 1 (pipeline_025) widened both CHECKs to accept P0-P3 AND the
-- words, so neither phase had a flag day. Step 2 was each phase moving
-- its own writers behind that, with its own tests: Phase 1 in #94, Phase
-- 2 in #93, both now on main. Verified rather than assumed -- Phase 1's
-- task_bodies.py has 0 P-code writes and 15 word writes, Phase 2's
-- record.py has 8.
--
-- WHY THE RENAME. P0, P1, P2 and P3 are also this programme's phase
-- names, and they sit on the same row as `module`. An urgent work item
-- raised by the WQA phase read
--
--     module = 'phase_1_wqa'   priority = 'P1'
--
-- which means "raised by Phase 1, high priority" and scans as Phase 1
-- twice. pipeline.action put both columns side by side in one list,
-- which moved the collision from a column nobody read onto the screen.
--
-- WHY NARROWING IS THE SAFE DIRECTION, which is the opposite of how it
-- was first put to Adam. Local SEO and AEO pipelines will write work
-- items, and nobody here will know their backends. A narrow constraint
-- is how a future writer is forced onto the right vocabulary on day one,
-- loudly, while somebody is still building it. Left wide, a new pipeline
-- quietly reintroduces 'P1' and phase names reappear in the priority
-- column with nothing objecting.
--
-- WHAT WAS SWEPT BEFORE NARROWING, because a rejected write is loud only
-- where somebody is watching:
--
--   * 12 other repos: no mention of either table. (scope-builder has its
--     own P1/P2/P3 CHECK on its own `skus` table in a different Supabase
--     project -- a false positive worth naming so nobody re-finds it.)
--   * No database function or trigger writes priority on either table.
--   * No dashboard route updates or inserts it.
--   * Every SKILL.md: exactly ONE documented write carried a renamed
--     value, p2-maintain's copy-pasteable `--row '{"priority": "P2"}'`,
--     now fixed. The generic kb CLI makes `--row` an operator surface
--     rather than a code path, so a documented example IS an interface.
--   * Three branches carried the pre-rename task bodies; none MODIFIES
--     that file, so each picks the rename up by merging main.
--
-- Adam's call on what could not be swept: assume only this machine
-- writes these columns today, with other machines to come.
--
-- NOT IN SCOPE, and deliberately: tsa.check_result.priority and the
-- check registry also use P1/P2/P3. That is a per-CHECK priority, and
-- phase_2_tsa/score.py weights every Phase 2 score with it, so moving it
-- would move every client's score. It keeps the old spelling and needs
-- its own decision.

update pipeline.work_item
   set priority = case priority
                      when 'P0' then 'urgent'
                      when 'P1' then 'high'
                      when 'P2' then 'normal'
                      when 'P3' then 'low'
                      else priority
                  end
 where priority in ('P0', 'P1', 'P2', 'P3');

-- open_question was backfilled to 'normal' in pipeline_026 and has never
-- held a P-code, so this maps nothing. Here so the migration does not
-- depend on that remaining true.
update pipeline.open_question
   set priority = case priority
                      when 'P0' then 'urgent'
                      when 'P1' then 'high'
                      when 'P2' then 'normal'
                      when 'P3' then 'low'
                      else priority
                  end
 where priority in ('P0', 'P1', 'P2', 'P3');

alter table pipeline.work_item
    drop constraint work_item_priority_check;
alter table pipeline.work_item
    add constraint work_item_priority_check
        check (priority in ('urgent', 'high', 'normal', 'low'));

alter table pipeline.open_question
    drop constraint open_question_priority_check;
alter table pipeline.open_question
    add constraint open_question_priority_check
        check (priority in ('urgent', 'high', 'normal', 'low'));

comment on column pipeline.work_item.priority is
    'How much this blocks: urgent, high, normal or low. P0-P3 was the old '
    'spelling and is refused from 20261009 -- those four strings also name '
    'our phases, so `module = phase_1_wqa, priority = P1` read as Phase 1 '
    'twice. Do NOT sort it as text: alphabetically the words come out high, '
    'low, normal, urgent, which is exactly wrong. Use an explicit rank map.';

comment on column pipeline.open_question.priority is
    'How much this blocks, same vocabulary as pipeline.work_item.priority. '
    'Defaults to ''normal'': nobody has judged a new question, so it sits in '
    'the middle rather than being guessed, and every action has one.';
