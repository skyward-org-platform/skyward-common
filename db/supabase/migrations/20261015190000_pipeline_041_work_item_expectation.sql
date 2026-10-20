-- pipeline_041: an action can promise more than one thing.
--
-- THE PROBLEM, found by P1-P2 the moment Phase 1's declarations landed.
-- work_item and work_item_url each hold ONE expectation column set, and
-- three of Phase 1's action types declare several metrics for the same
-- pages:
--
--   p1-restore                      status_code, clicks, impressions
--   p1-redirect-map redirected_away status_code, redirect_hops, impressions
--   p1-canonicalize                 canonical_present, canonical_mismatch
--
-- One row cannot carry three, so copying the declaration onto the item at
-- creation silently dropped two thirds of what those actions promised,
-- and `record` had no rule for which one survived.
--
-- WHY NOT FAN OUT ONTO work_item_url. That was the obvious alternative
-- and it is the one thing this must not do. work_item_url is one row per
-- page per action, and pipeline_036 exists because a second row per page
-- multiplied every reading of it: a redirect map listing two URL forms
-- matched twice and doubled the rows in the snapshot view. Three metrics
-- per page would reintroduce that with a factor of three.
--
-- WHY NOT ONE ROW PER PAGE PER METRIC HERE EITHER. The declaration is
-- per SIDE, not per page, so a 611-page redirect map needs four rows and
-- not 1,833. This table therefore mirrors action_expectation's shape
-- exactly, with work_item_id in place of (module, action_type), and the
-- judge resolves side to pages the same way pipeline_036 already does.
--
-- THE COPY-AT-CREATION RULE IS UNCHANGED and is the reason this table
-- exists rather than the judge reading action_expectation live: editing a
-- default must not retroactively rewrite what was promised about actions
-- already measured (Adam, 2026-10-14). This is the frozen copy; the
-- declaration is the template.
--
-- The expectation columns on work_item and work_item_url are NOT dropped.
-- Phase 2's 13 live items carry their expectation there and must keep
-- working, so the judge reads this table first and falls back to them.
-- Dropping them belongs in a later migration, once nothing reads them.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

create table if not exists pipeline.work_item_expectation (
    work_item_expectation_id uuid primary key default gen_random_uuid(),

    work_item_id uuid not null
        references pipeline.work_item(work_item_id) on delete cascade,
    -- Carried so a read can scope by site without joining the item.
    domain_id bigint not null,

    -- WHICH SIDE OF THE CHANGE, in work_item_url.expected_change's own
    -- vocabulary. NULL means every page of the action. A redirect's
    -- subject loses impressions while its destination gains clicks, and
    -- that is two rows rather than one averaged claim.
    expected_change text,

    expected_metric text not null,
    expected_direction text
        check (expected_direction is null
               or expected_direction in ('improves', 'declines',
                                         'reaches', 'none')),
    expected_magnitude numeric,
    expected_magnitude_unit text
        check (expected_magnitude_unit is null
               or expected_magnitude_unit in ('percent', 'points',
                                              'positions', 'absolute',
                                              'absolute_target')),

    -- When it is fair to look. NULL means the metric registry decides,
    -- which is the normal case.
    expected_check_after_days integer
        check (expected_check_after_days is null
               or expected_check_after_days >= 0),
    expected_state_check_after_days integer
        check (expected_state_check_after_days is null
               or expected_state_check_after_days >= 0),

    expected_note text,
    set_by text,
    set_at timestamptz not null default now(),

    -- "up 10" and "up percent" are both unreadable. Same rule as every
    -- other table that holds a size.
    constraint work_item_expectation_magnitude_has_a_unit
        check ((expected_magnitude is null)
               = (expected_magnitude_unit is null)),

    -- A PROMISE THAT NOTHING WILL MOVE HAS TO SAY WHY, because on its own
    -- it is indistinguishable from nobody having thought about it. Same
    -- rule as pipeline_037's.
    constraint work_item_expectation_none_explains_itself
        check (expected_direction is distinct from 'none'
               or (expected_note is not null
                   and length(btrim(expected_note)) > 0)),

    -- NULLS NOT DISTINCT, so one action cannot carry two contradictory
    -- "every page" promises about the same metric. Without it Postgres
    -- treats each NULL side as its own key and allows both, which is the
    -- argument pipeline_039 made for action_expectation and pipeline_040
    -- made for the verdict key.
    constraint work_item_expectation_key
        unique nulls not distinct
        (work_item_id, expected_change, expected_metric)
);

comment on table pipeline.work_item_expectation is
    'What one action promised, one row per metric per side of the '
    'change. The frozen copy of pipeline.action_expectation, taken when '
    'the item was created, so editing a declaration never rewrites what '
    'was promised about work already measured. An action that promises '
    'three things has three rows here and three verdicts per page.';

comment on column pipeline.work_item_expectation.expected_change is
    'Which side of the change this promise is about, in '
    'work_item_url.expected_change''s vocabulary. NULL means every page '
    'of the action, which is what a single-sided action uses.';

create index if not exists work_item_expectation_by_item
    on pipeline.work_item_expectation (work_item_id);

create index if not exists work_item_expectation_by_domain
    on pipeline.work_item_expectation (domain_id, expected_metric);
