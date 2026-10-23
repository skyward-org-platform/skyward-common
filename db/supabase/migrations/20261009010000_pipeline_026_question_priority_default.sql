-- 20261009010000_pipeline_026_question_priority_default.sql
--
-- Every action gets a priority. Adam, 2026-10-09, looking at the Actions
-- list: "EVERY action should have a priority even if its very low (im
-- assuming P0 old questions have no priorities from before but should be
-- migrated to all ahev a prikority)."
--
-- He was right about the cause. `priority` only reached
-- pipeline.open_question earlier today (pipeline_023), and nothing has
-- ever written it, so every question ever raised carries null. On plasry
-- that was 26 of 34 OPEN actions with no priority at all -- the column
-- was mostly empty on the surface it exists to order.
--
-- `normal` RATHER THAN A DERIVED GUESS. The alternative on the table was
-- to infer from `kind` -- a contradiction is high, a gap is normal --
-- which reads as more thoughtful and is a guess dressed as a rule: a
-- trivial contradiction would outrank a gap that blocks Phase 3. Nobody
-- has judged these, so they sit in the middle and say so. Anyone who
-- disagrees with one re-prioritises it, and that is then a judgement
-- somebody made rather than one we invented.
--
-- `low` was the other option and is worse in the direction that matters:
-- it would read as "probably not urgent" for 26 rows nobody has looked
-- at, and a genuinely blocking question would sit at the bottom until
-- somebody noticed.
--
-- THE DEFAULT IS THE HALF THAT STOPS IT RECURRING. A backfill alone
-- fixes today and leaves the next question raised carrying null again,
-- which is how a column ends up mostly empty in the first place. The
-- upsert path omits `priority` when a caller does not set one, so the
-- column default applies.
--
-- pipeline.work_item is NOT touched. Its priority is written by both
-- phases from their canonical item lists, so a null there would mean a
-- writer forgot rather than nobody judged -- and defaulting it would
-- hide that. It currently has no nulls at all.

update pipeline.open_question
   set priority = 'normal'
 where priority is null;

alter table pipeline.open_question
    alter column priority set default 'normal';

comment on column pipeline.open_question.priority is
    'How much this blocks, same vocabulary as pipeline.work_item.priority. '
    'Defaults to ''normal'': nobody has judged a new question, so it sits in '
    'the middle rather than being guessed high or low, and every action has '
    'one. MID-RENAME -- P0-P3 and urgent/high/normal/low are both legal '
    'until every writer has moved; see pipeline_025.';
