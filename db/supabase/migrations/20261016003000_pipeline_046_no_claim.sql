-- pipeline_046: "we claim nothing" is not "we expect no movement".
--
-- NOTE ON THE VERSION. This was first written as 20261015234500 and that
-- timestamp was already taken by another lane's
-- wqa_025_verdict_rules_fingerprint, applied minutes earlier. The runner
-- keys on the VERSION alone, so it reported "already applied but the
-- file has since changed" and compared this file's checksum against that
-- unrelated migration's -- which reads exactly like having overwritten
-- somebody's applied migration. Nothing was overwritten; two lanes chose
-- the same minute. Worth knowing before the same error frightens the
-- next person.
--
-- `none` is doing two different jobs and the two have opposite meanings
-- when a metric moves. P2-Migration found it, 2026-10-15, after the
-- judge started reading `none` as a falsifiable promise.
--
--   EXPECT STEADY. "A removed page earning impressions means the audit
--   was wrong." Movement is a finding, and the promise is broken by it.
--   This is what pipeline_037 documented `none` to mean and what all
--   three of Phase 1's `none` declarations mean.
--
--   CLAIM NOTHING. "No impact claimed by design: this item hands the
--   findings to Phase 3 and changes no page." Movement is NOT a finding
--   and the action has promised nothing to break. Phase 2's p2-broken
--   and p2-content-phase-3 both mean this.
--
-- Read as the first, the second produces a false failure: a Phase 3
-- rewrite landing on p2-content-phase-3's pages would be reported as
-- Phase 2 breaking its word, on a item whose whole declaration is that
-- it changes nothing. BusBank already holds two readings of that action.
--
-- WHY NOT JUST OMIT THE METRIC, which was the other option on the table.
-- Then the verdict is `no_expectation`, whose wording is "nobody
-- recorded what this action expected to change ... a gap in the action":
-- it would read as an oversight somebody should go and fix, and it would
-- be indistinguishable from the real oversights we have spent today
-- closing. A deliberate silence and a forgotten declaration are
-- different facts, and the sentence "no impact claimed by design" is
-- worth keeping rather than expressing as an absence.
--
-- `none` KEEPS ITS DOCUMENTED MEANING and Phase 1's three rows are
-- untouched. Only the two Phase 2 declarations move, and that is
-- P2-Migration's own write to make.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

alter table pipeline.action_expectation
    drop constraint if exists action_expectation_expected_direction_check;
alter table pipeline.action_expectation
    add constraint action_expectation_expected_direction_check check (
        expected_direction in ('improves', 'declines', 'reaches', 'none',
                               'no_claim'));

alter table pipeline.work_item
    drop constraint if exists work_item_expectation_direction;
alter table pipeline.work_item
    add constraint work_item_expectation_direction check (
        expected_direction is null
        or expected_direction in ('improves', 'declines', 'reaches', 'none',
                                  'no_claim'));

alter table pipeline.work_item_url
    drop constraint if exists work_item_url_expectation_direction;
alter table pipeline.work_item_url
    add constraint work_item_url_expectation_direction check (
        expected_direction is null
        or expected_direction in ('improves', 'declines', 'reaches', 'none',
                                  'no_claim'));

alter table pipeline.work_item_expectation
    drop constraint if exists work_item_expectation_expected_direction_check;
alter table pipeline.work_item_expectation
    add constraint work_item_expectation_expected_direction_check check (
        expected_direction is null
        or expected_direction in ('improves', 'declines', 'reaches', 'none',
                                  'no_claim'));

comment on column pipeline.action_expectation.expected_direction is
    'improves / declines: the metric should move that way. reaches: it '
    'should end at expected_magnitude. none: it should NOT move, and a '
    'material move breaks the promise. no_claim: the action claims no '
    'effect on this metric at all, so a move is neither a success nor a '
    'failure -- recorded so that a deliberate silence is not mistaken '
    'for a forgotten declaration.';
