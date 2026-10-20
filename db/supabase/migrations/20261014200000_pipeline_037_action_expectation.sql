-- pipeline_037: what each KIND of action is expected to move, declared once.
--
-- Today every phase fills the expectation columns on every work item it
-- creates, so "a speed fix watches real-user load time and should get
-- faster" is copied onto every row, and two phases can disagree about the
-- same kind of change with nothing noticing. BusBank has 1,765 work item
-- rows and not one expectation on any of them, which is the other half of
-- the same problem: there is nowhere for a phase to say it ONCE, so it
-- mostly does not say it at all.
--
-- One row per action type per module. The phase declares; the item keeps
-- its own copy.
--
-- THE COPY IS THE POINT, and it is why this table does not simply replace
-- the columns on work_item. The default is copied onto an item when the
-- item is created, and never looked up at read time. If a verdict read
-- this table instead, editing a default next quarter would retroactively
-- rewrite what we promised about actions already measured and reported,
-- and a judgement somebody quoted would change underneath them. A stored
-- judgement keeps the inputs it was made with -- the same reason the
-- pipeline does not copy rows forward to tidy up a version.
--
-- Phase 2 is the worked example and the only phase that has written its
-- types down: six metrics across eight action types, with crux_lcp_ms
-- declared as improving while its number goes DOWN. Which way a metric is
-- good is NOT stored here for that reason: it is a property of the metric
-- rather than of the action, it lives in the pipeline's metric registry,
-- and a column here would let two action types disagree about a fact that
-- belongs to Chrome.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

create table if not exists pipeline.action_expectation (
    action_expectation_id uuid primary key default gen_random_uuid(),

    -- WHO DECLARES IT, and for which kind of action. module is the
    -- pipeline module (phase_1_wqa, phase_2_tsa, ...); action_type is
    -- that module's own name for the kind of change, which is the
    -- granularity a phase actually reasons in.
    module          text not null,
    action_type     text not null,

    expected_metric text not null,
    -- The phase's own word for the direction it wants. Deliberately NOT
    -- the authority on which way the raw number moves: crux_lcp_ms
    -- "improves" by falling, and the metric registry owns that.
    expected_direction text not null
        check (expected_direction in ('improves', 'declines', 'reaches',
                                      'none')),
    expected_magnitude      numeric,
    expected_magnitude_unit text
        check (expected_magnitude_unit is null
               or expected_magnitude_unit in ('percent', 'points',
                                              'positions', 'absolute',
                                              'absolute_target')),
    -- "up 10" and "up percent" are both unreadable, same rule as the
    -- columns on work_item.
    constraint action_expectation_magnitude_has_a_unit
        check ((expected_magnitude is null)
               = (expected_magnitude_unit is null)),
    -- 'none' means the action deliberately expects no movement, which is
    -- a real thing to declare and has to say why, or it is a shrug.
    constraint action_expectation_none_says_why
        check (expected_direction is distinct from 'none'
               or (note is not null and length(btrim(note)) > 0)),

    note        text,
    set_by      text,
    set_at      timestamptz not null default now(),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    -- An action type may watch SEVERAL metrics, so the key carries the
    -- metric. Phase 2's schema work watches two.
    constraint action_expectation_natural_key
        unique (module, action_type, expected_metric)
);

comment on table pipeline.action_expectation is
    'What each KIND of action is expected to move, declared once per '
    'module and action type instead of copied onto every work item. The '
    'values are copied onto an item when it is created and never looked '
    'up at read time, so editing a default cannot retroactively rewrite '
    'what was promised about actions already measured. Which way a metric '
    'is good is NOT here: that belongs to the metric, and lives in the '
    'pipeline''s metric registry.';

create index action_expectation_by_module
    on pipeline.action_expectation (module, action_type);
