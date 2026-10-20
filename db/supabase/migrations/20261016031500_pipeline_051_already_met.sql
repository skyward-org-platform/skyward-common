-- pipeline_051: `already_met`, for a target that was met before the work.
--
-- BusBank's first judged action produced 1,339 verdicts, of which 592
-- read `as_expected`. Every one of those 592 was a page whose target was
-- ALREADY met at the baseline: 298 already answered 301, 294 already had
-- one hop, and not one of them moved. The verdict credited the action
-- with a state it never created, and 592 successes would have been the
-- headline number on a screen.
--
-- `already_met` is not a judgement either way. The page is where it
-- should be and nobody put it there, so what it says is that the page
-- did not need this change -- a finding about the AUDIT rather than
-- about the work.
--
-- A reaches target is still judged on the END state, which is why a page
-- moved TO its target is still as_expected and a page that left it is
-- still against_expectation. The distinction only exists because the
-- baseline reading is read as well as the later one.
--
-- APPLIED WITHOUT WAITING, and the reason is not impatience. The judge
-- already emits this outcome, and every verdict for an action is written
-- in one batch: until the check accepts it, the next re-judge of
-- p1-redirect-map fails the whole statement and that action loses all
-- 1,339 verdicts rather than one row. That is the third time tonight
-- that adding a value to one end of a chain has nearly cost a whole
-- batch (pipeline_049, pipeline_050), so the constraint goes in with the
-- code rather than after it.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.page_verdict_snapshot
    drop constraint if exists page_verdict_snapshot_outcome_check;

alter table pipeline.page_verdict_snapshot
    add constraint page_verdict_snapshot_outcome_check check (
        outcome in ('as_expected', 'against_expectation', 'too_small',
                    'too_early', 'cannot_attribute', 'not_measurable',
                    'no_expectation', 'no_claim', 'already_met'));

comment on column pipeline.page_verdict_snapshot.outcome is
    'One of nine. Only as_expected and against_expectation judge the '
    'work. The other seven are reasons a reading cannot say: no_claim '
    '(the action claimed no effect), no_expectation (nobody recorded '
    'one), already_met (the target was met before the change, so the '
    'page did not need it), too_early, too_small, cannot_attribute and '
    'not_measurable.';
