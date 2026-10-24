-- 20261009003000_pipeline_025_priority_widen.sql
--
-- STEP 1 OF 3 in renaming `priority` from P0-P3 to words. Adam approved
-- the rename on 2026-10-08, and approved doing it in three steps rather
-- than one. This step alone changes no data and breaks nothing.
--
-- WHY RENAME. The four strings P0, P1, P2 and P3 are also this
-- programme's phase names -- Onboarding, Website Quality, Technical SEO,
-- Keyword and Prompt -- and they appear on the SAME ROW as `module`. An
-- urgent work item raised by the WQA phase reads
--
--     module = 'phase_1_wqa'   priority = 'P1'
--
-- which means "raised by Phase 1, high priority" and scans as Phase 1
-- twice. pipeline.action now puts both columns side by side in one list,
-- which moved the collision from a column nobody read onto the screen.
-- Found by the Phase 1 and 2 lane.
--
-- The new vocabulary follows precedent rather than inventing one:
-- meta.site.priority has always used words.
--
-- WHY THREE STEPS, AND WHY A SINGLE MIGRATION WOULD BREAK THINGS. The
-- literal P-strings are load-bearing in both phases' code:
--
--   * 15 hardcoded priorities in phase_1_wqa/task_bodies.py alone, plus
--     workbook.py and record.py, and the same shape in phase_2_tsa. These
--     WRITE the values, so narrowing the constraint while they still say
--     'P1' fails the next `p1-wqa clickup` push on its first task body.
--   * PRIORITY_RANK in phase_2_tsa/clickup_tasks.py and workbook.py, and
--     WEIGHT in phase_2_tsa/score.py. These READ them, and they fail
--     SILENTLY: an unknown value falls back to last place or contributes
--     nothing, so task order breaks and a client's Phase 2 score drops
--     with no error anywhere.
--   * deliver.py's sort uses a "P9" sentinel that works only because the
--     values sort lexically. The new words do NOT: alphabetically they
--     come out high, low, normal, urgent -- exactly wrong, and wrong in a
--     way that reads as data rather than as a bug.
--
-- So: widen here, let each phase move its own writers and rank maps with
-- its own tests, then narrow. Step 2 is where the sort-order bug gets
-- caught in a test instead of in a client deck.
--
-- NOT IN SCOPE: tsa.check_result.priority and the check registry, which
-- also use P1/P2/P3. That is a per-CHECK priority, not a per-work-item
-- one, and phase_2_tsa/score.py weights every Phase 2 score with it.
-- Moving it would move every score and needs its own decision.
--
-- These strings reach the client: deliver.py renders priority into the
-- workbook's task tab and the deck. Adam confirmed the rename knowing
-- that.

alter table pipeline.work_item
    drop constraint work_item_priority_check;
alter table pipeline.work_item
    add constraint work_item_priority_check
        check (priority in ('P0', 'P1', 'P2', 'P3',
                            'urgent', 'high', 'normal', 'low'));

alter table pipeline.open_question
    drop constraint open_question_priority_check;
alter table pipeline.open_question
    add constraint open_question_priority_check
        check (priority in ('P0', 'P1', 'P2', 'P3',
                            'urgent', 'high', 'normal', 'low'));

comment on column pipeline.work_item.priority is
    'How much this blocks. MID-RENAME: urgent / high / normal / low is the '
    'target vocabulary, P0-P3 the old one, and both are legal until every '
    'writer has moved (step 3 narrows this). Do NOT sort it as text -- '
    'neither vocabulary sorts lexically into priority order. Use an '
    'explicit rank map.';

comment on column pipeline.open_question.priority is
    'How much this blocks, same vocabulary as pipeline.work_item.priority. '
    'Null is the normal case: a question raised in passing has no priority '
    'until somebody judges it. MID-RENAME -- see that column''s comment.';
