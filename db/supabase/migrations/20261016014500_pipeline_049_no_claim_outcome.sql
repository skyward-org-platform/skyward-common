-- pipeline_049: the verdict table can store the outcome the judge emits.
--
-- pipeline_046 added `no_claim` as a DIRECTION an action may declare, and
-- the judge returns a `no_claim` OUTCOME for it. The outcome check on
-- page_verdict_snapshot still listed only the original seven, so the
-- outcome could be computed and not stored.
--
-- WHY THIS WAS ABOUT TO COST AN ENTIRE ACTION'S VERDICTS rather than one
-- row. judge_action writes every verdict for an action in a SINGLE batch
-- insert. One row failing the check fails the statement, the savepoint
-- added yesterday rolls the whole thing back, and the action ends up with
-- ZERO verdicts and a "judging failed" note -- not one bad verdict, all
-- of them. P0-Testing found it by inserting a no_claim verdict in a
-- rolled-back savepoint and watching it be rejected, and it landed four
-- minutes before I re-judged BusBank with two live `no_claim`
-- declarations already written by Phase 2.
--
-- The lesson worth keeping: adding a value to one end of a pipeline is
-- not adding it to the pipeline. The direction checks on four tables were
-- updated and the outcome check on the fifth was not, and nothing
-- connects them but a reader noticing.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.page_verdict_snapshot
    drop constraint if exists page_verdict_snapshot_outcome_check;

alter table pipeline.page_verdict_snapshot
    add constraint page_verdict_snapshot_outcome_check check (
        outcome in ('as_expected', 'against_expectation', 'too_small',
                    'too_early', 'cannot_attribute', 'not_measurable',
                    'no_expectation', 'no_claim'));

comment on column pipeline.page_verdict_snapshot.outcome is
    'One of eight. Only as_expected and against_expectation judge the '
    'work; the other six are reasons a reading cannot say. no_claim '
    'means the action claimed no effect on this metric, which is a '
    'different statement from no_expectation (nobody recorded one).';
