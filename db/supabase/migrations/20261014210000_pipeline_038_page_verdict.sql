-- pipeline_038: whether a change did what it said, stored with its inputs.
--
-- The verdict is computed from two readings and an expectation, and it is
-- stored rather than recomputed on sight for one reason: somebody quotes
-- it. A number on a client call, a line in a report, a decision to do
-- more of something. A judgement that silently changes when a rule is
-- edited is worse than no judgement, so each row carries the inputs it
-- was made from and says when it was made.
--
-- FIVE OUTCOMES, AND ONLY TWO ARE A JUDGEMENT ABOUT THE WORK. The split
-- is the reason this table exists at all: a report that collapses the
-- rest into "did not work" blames the team for a source that lags, a page
-- too small to measure, or a second change nobody controlled. On the
-- BusBank readings taken 2026-10-14, three of the five are already live.
--
--   as_expected          the metric moved the way the action said
--   against_expectation  it moved the other way, or fell short
--   too_small            less than the metric moves by itself, or under
--                        30 impressions on both readings (Phase 2's own
--                        rule, taken as-is rather than a second number)
--   too_early            the source cannot describe the change yet, which
--                        for Chrome's load time means inside 28 days
--   cannot_attribute     another action changed the page in this window
--   not_measurable       the source could not be read at all
--   no_expectation       nobody said what to expect: a gap in the action
--                        rather than in the page
--
-- better_direction IS STORED, not inferred at read time. It is the thing
-- most likely to be got wrong later -- crux_lcp_ms improves by FALLING,
-- as does average position -- so a verdict that says which way it counted
-- can be checked rather than trusted.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot.

create table if not exists pipeline.page_verdict_snapshot (
    page_verdict_id uuid primary key default gen_random_uuid(),

    domain_id    bigint not null,
    work_item_id uuid not null
        references pipeline.work_item (work_item_id) on delete cascade,
    -- Null for a verdict about the action's page set as a whole.
    url          text,

    -- The two readings compared, so the verdict can be traced to them.
    before_snapshot_id uuid not null
        references pipeline.page_snapshot (page_snapshot_id)
        on delete cascade,
    after_snapshot_id  uuid not null
        references pipeline.page_snapshot (page_snapshot_id)
        on delete cascade,

    outcome text not null
        check (outcome in ('as_expected', 'against_expectation',
                           'too_small', 'too_early', 'cannot_attribute',
                           'not_measurable', 'no_expectation')),
    -- The sentence a reader sees. Written by the judge, not assembled in
    -- a view, so the reason travels with the outcome.
    says text not null,

    -- WHAT IT WAS JUDGED AGAINST, copied at judging time.
    metric              text,
    expected_direction  text,
    expected_magnitude  numeric,
    -- Which way the RAW NUMBER had to move for this to count as better.
    better_direction    text
        check (better_direction is null
               or better_direction in ('up', 'down', 'none')),

    value_before numeric,
    value_after  numeric,
    pct_change   numeric,

    judged_at  timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    -- One verdict per page per metric per pair of readings. A re-judge
    -- after a rule change replaces it, and judged_at moves, which is how
    -- a changed verdict is visible against the inputs beside it.
    constraint page_verdict_natural_key
        unique (work_item_id, url, metric, after_snapshot_id)
);

comment on table pipeline.page_verdict_snapshot is
    'Whether a change did what its action said, computed from two '
    'readings and stored with the inputs it was judged from. Five of its '
    'seven outcomes are NOT a judgement about the work: too_small, '
    'too_early, cannot_attribute, not_measurable and no_expectation are '
    'the honest answers a reading often has, and collapsing them into '
    '"did not work" blames the team for a lagging source or somebody '
    'else''s change.';

create index page_verdict_by_item
    on pipeline.page_verdict_snapshot (work_item_id, outcome);
create index page_verdict_by_page
    on pipeline.page_verdict_snapshot (domain_id, url);
