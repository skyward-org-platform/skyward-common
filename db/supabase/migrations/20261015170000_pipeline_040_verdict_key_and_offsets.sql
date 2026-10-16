-- pipeline_040: three holes P0-Testing and P1-P2 measured on 2026-10-15.
--
-- Each one is small, and each one was producing a wrong answer rather
-- than a missing one, which is why they are worth a migration on the day
-- they were found.
--
-- 1. THE VERDICT KEY LETS A RE-JUDGE DUPLICATE EVERYTHING.
--
-- page_verdict_natural_key is unique (work_item_id, url, metric,
-- after_snapshot_id), and `metric` is NULL for exactly the outcome the
-- table holds most of: `no_expectation` carries no metric, because
-- nothing was declared. Postgres treats NULLs as distinct in a unique
-- constraint, so ON CONFLICT never matched and the upsert was an insert.
-- Measured on BusBank: 862 verdicts became 1,724, then 2,586, with one
-- URL carrying six. `url` is nullable too, so an action-level verdict
-- would have the same problem.
--
-- The judge now clears an action's verdicts before writing, so this is
-- no longer the only thing standing between us and duplicates -- but a
-- constraint that cannot enforce its own key is a trap for the next
-- writer, and this is the same argument 039 made for action_expectation
-- one day earlier.
--
-- 2. A STORED MAGNITUDE DOES NOT SAY 50 WHAT.
--
-- page_verdict_snapshot stores expected_magnitude and not its unit,
-- while the declaration it came from carries one of five: percent,
-- points, positions, absolute, absolute_target. Until today the judge
-- read every magnitude as a percentage, so "50 more clicks" was
-- satisfied by a 50% rise -- 12 of the 50 promised, recorded as
-- as_expected. The judge is fixed; the stored row still cannot repeat
-- what it judged against, which is the whole point of storing the
-- inputs beside the outcome.
--
-- 3. A DECLARATION CANNOT SAY WHEN IT IS FAIR TO LOOK.
--
-- expected_check_after_days and expected_state_check_after_days live on
-- work_item and work_item_url, and action_expectation has nowhere to put
-- them, so a backfill reading only the declaration cannot know the
-- offsets. Phase 1 is carrying them in each row's note as a workaround
-- (P1-P2, 2026-10-15).
--
-- NOTE ON WHAT IS **NOT** HERE. P0-Testing also raised that meta.site has
-- nowhere to record a site's timezone. That is deliberate: Adam settled
-- on 2026-10-14 that the pipeline uses ONE timezone, the agency's
-- America/New_York, with no per-site column, and the limit that follows
-- for a client east of UTC is documented at _days_of in series.py. A
-- column would reopen a closed decision.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

-- 1 ----------------------------------------------------------------------

alter table pipeline.page_verdict_snapshot
    drop constraint if exists page_verdict_natural_key;

alter table pipeline.page_verdict_snapshot
    add constraint page_verdict_natural_key
    unique nulls not distinct
    (work_item_id, url, metric, after_snapshot_id);

-- 2 ----------------------------------------------------------------------

alter table pipeline.page_verdict_snapshot
    add column if not exists expected_magnitude_unit text;

alter table pipeline.page_verdict_snapshot
    drop constraint if exists page_verdict_magnitude_unit;

alter table pipeline.page_verdict_snapshot
    add constraint page_verdict_magnitude_unit check (
        expected_magnitude_unit is null
        or expected_magnitude_unit in ('percent', 'points', 'positions',
                                       'absolute', 'absolute_target'));

comment on column pipeline.page_verdict_snapshot.expected_magnitude_unit is
    'What expected_magnitude was measured in, copied from the '
    'declaration. Same five values as work_item and action_expectation. '
    'Without it a stored 50 does not say 50 clicks or 50 percent, and '
    'the verdict beside it cannot be checked.';

-- 3 ----------------------------------------------------------------------

alter table pipeline.action_expectation
    add column if not exists expected_check_after_days integer;

alter table pipeline.action_expectation
    add column if not exists expected_state_check_after_days integer;

alter table pipeline.action_expectation
    drop constraint if exists action_expectation_offsets_are_positive;

alter table pipeline.action_expectation
    add constraint action_expectation_offsets_are_positive check (
        (expected_check_after_days is null
         or expected_check_after_days >= 0)
        and (expected_state_check_after_days is null
             or expected_state_check_after_days >= 0));

comment on column pipeline.action_expectation.expected_check_after_days is
    'How long after the close before this metric can describe the '
    'change, in days. Copied onto the work item at creation like the '
    'rest of the declaration. NULL means the metric registry decides, '
    'which is the normal case.';

comment on column pipeline.action_expectation.expected_state_check_after_days
    is
    'The same, for a state rather than a measurement: a status code or a '
    'canonical can be checked the day after the change, while a traffic '
    'figure cannot.';
