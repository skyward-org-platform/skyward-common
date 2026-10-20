-- 20261012210000_pipeline_030_work_item_expectation.sql
--
-- What a change is EXPECTED to do, so somebody can be scored on a
-- recommendation.
--
-- Paul's thread called this the core of what it needs and it was right:
-- the pipeline records what we found, what we asked for and, since the
-- ClickUp close became the ship date, when it went live -- and nothing
-- said what it was supposed to achieve. An outcome could be measured and
-- not judged.
--
-- ON BOTH TABLES, and that is a revision of the first design (Adam,
-- 2026-10-12). Columns on work_item alone cannot express a REDIRECT: the
-- source URL should stop answering 200 and lose its traffic while the
-- destination gains it, which is two opposite expectations on two URLs.
-- P1-P2 found it while documenting Phase 1's seven action types, and
-- work_item_url already had the grain -- its expected_change says what
-- each URL should LOOK like afterwards -- while the item-level columns did
-- not. So the same columns go on both, and they mean different things:
--
--   work_item      the SITE-LEVEL expectation. For a redirect that is
--                  "no site-level loss": the traffic moves rather than
--                  goes. For a rewrite it is the page's own gain.
--   work_item_url  the expectation for THAT URL, beside the end state
--                  expected_change already records for it.
--
-- Still one expectation per row, which is the trade Adam chose knowingly.
-- No new table: work_item_url already existed.
--
-- TWO CHECK OFFSETS, because three of Phase 1's types want two dates and
-- one date would force a choice between them. A redirect's status code is
-- checkable at 14 days; whether it consolidated is not fair to read before
-- 90. These are the same two questions the action page separates:
--
--   expected_state_check_after_days   DELIVERY: did the change land
--   expected_check_after_days         EFFECT:   did it do anything
--
-- FOUR THINGS THAT ARE NOT OBVIOUS, each otherwise a silent wrong answer:
--
-- 1. THE CHECK DATES CANNOT BE ABSOLUTE AT CREATION. Phase 2 counts from
--    completed_at, and at creation nothing has shipped, so a date written
--    then is wrong by however long the work takes. The offsets are stored;
--    the dates are derived once the close is known.
--
-- 2. DIRECTION IS SEMANTIC, NOT ARITHMETIC. Phase 3 wrote "median rank up
--    5 positions", meaning better, and for rank better is a LOWER number.
--    Storing 'up' would make every rank improvement read as a miss to
--    anyone comparing raw values. The values say what happened to the
--    page, not to the integer: improves, declines, reaches, none.
--
-- 3. "NO EXPECTED IMPACT" IS AN ANSWER AND MUST NOT LOOK LIKE A BLANK.
--    Five of the twenty documented types expect no traffic change and say
--    why -- a verification that nothing happened, a scope gate whose
--    traffic belongs to the work it releases, a decision rather than an
--    edit, a broken-link fix, a schema fix below a traffic floor.
--    direction = 'none' with a required note is that answer; NULL means
--    nobody has filled it in.
--
-- 4. NO CHECK ON THE METRIC NAME. Phase 2 asked for eight new metrics in
--    one afternoon. A constraint would mean a migration every time a phase
--    names one, so the validation belongs in code, against what the
--    snapshot can actually read, where it can fail loudly and name the
--    alternatives.
--
-- MAGNITUDES ARE FIRST GUESSES, labelled as such by every phase that
-- wrote one, and Phase 1 deliberately wrote none at all on the grounds
-- that a made-up percentage stops looking made up once it is in a column.
-- expected_note carries the reasoning so a recalibration can see what the
-- guess rested on.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

-- ---------------------------------------------------------------------
-- The columns, identical on both tables.
-- ---------------------------------------------------------------------
do $$
declare t text;
begin
    foreach t in array array['work_item', 'work_item_url']
    loop
        execute format($f$
            alter table pipeline.%I
                add column if not exists expected_metric text,
                add column if not exists expected_direction text,
                add column if not exists expected_magnitude numeric,
                add column if not exists expected_magnitude_unit text,
                add column if not exists expected_check_after_days integer,
                add column if not exists expected_check_at date,
                add column if not exists expected_state_check_after_days integer,
                add column if not exists expected_state_check_at date,
                add column if not exists expected_note text,
                add column if not exists expected_set_by text,
                add column if not exists expected_set_at timestamptz
        $f$, t);

        execute format($f$
            alter table pipeline.%I
                add constraint %I check (
                    expected_direction is null
                    or expected_direction in ('improves','declines',
                                              'reaches','none')),
                add constraint %I check (
                    expected_magnitude_unit is null
                    or expected_magnitude_unit in (
                        'percent',           -- a relative move
                        'points',            -- percentage POINTS
                        'positions',         -- rank places
                        'absolute',          -- a delta in its own unit
                        'absolute_target')), -- reach this value
                add constraint %I check (
                    expected_check_after_days is null
                    or expected_check_after_days >= 0),
                add constraint %I check (
                    expected_state_check_after_days is null
                    or expected_state_check_after_days >= 0),
                -- An expectation is a metric AND a direction, or nothing.
                -- A magnitude with no metric is a number with no subject.
                add constraint %I check (
                    (expected_metric is null and expected_direction is null
                     and expected_magnitude is null
                     and expected_magnitude_unit is null)
                    or (expected_metric is not null
                        and expected_direction is not null)),
                -- 'none' carries no magnitude and has to say why, or it
                -- is indistinguishable from a shrug.
                add constraint %I check (
                    expected_direction is distinct from 'none'
                    or (expected_magnitude is null
                        and expected_magnitude_unit is null
                        and expected_note is not null
                        and length(btrim(expected_note)) > 0)),
                -- "up 10" and "up percent" are both unreadable.
                add constraint %I check (
                    (expected_magnitude is null)
                    = (expected_magnitude_unit is null)),
                -- A magnitude has to be measurable at some point, so it
                -- needs at least one of the two offsets.
                add constraint %I check (
                    expected_magnitude is null
                    or expected_check_after_days is not null
                    or expected_state_check_after_days is not null)
        $f$, t,
             t || '_expectation_direction', t || '_expectation_unit',
             t || '_expectation_offset_positive',
             t || '_expectation_state_offset_positive',
             t || '_expectation_is_whole', t || '_expectation_none_says_why',
             t || '_expectation_magnitude_has_a_unit',
             t || '_expectation_measured_has_a_when');
    end loop;
end $$;

create index work_item_expectation_due
    on pipeline.work_item (domain_id, expected_check_at)
    where expected_check_at is not null;
create index work_item_url_expectation_due
    on pipeline.work_item_url (domain_id, expected_check_at)
    where expected_check_at is not null;

comment on column pipeline.work_item.expected_metric is
    'The metric this change should move, at SITE level. Free text on '
    'purpose: the phases add metrics faster than a CHECK could be '
    'migrated, so validation is in code against what the snapshot reads.';
comment on column pipeline.work_item_url.expected_metric is
    'The metric expected to move for THIS URL, which can be the opposite '
    'of the item''s site-level one. A redirect''s source is expected to '
    'decline while its destination improves.';
comment on column pipeline.work_item.expected_direction is
    'What happens to the PAGE, not to the number: improves, declines, '
    'reaches, or none. Rank improves by going down, and storing ''up'' '
    'would make every rank win read as a miss.';
comment on column pipeline.work_item.expected_check_after_days is
    'EFFECT. Days after completed_at before the result is fair to read. '
    'Absolute dates cannot be set at creation: nothing has shipped yet.';
comment on column pipeline.work_item.expected_state_check_after_days is
    'DELIVERY. Days after completed_at before the change itself is '
    'checkable -- a status code at 14 days, where its consolidation is '
    'not fair to read before 90.';
comment on column pipeline.work_item.expected_note is
    'Why, and what the magnitude was a guess from. Required when the '
    'direction is ''none'', so a decision never looks like a blank.';
