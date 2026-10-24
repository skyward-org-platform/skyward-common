-- pipeline_056: when a work item was FIRST found, kept separate from the
-- run that last touched it.
--
-- THE HOLE THIS FILLS (Adam's plan, 2026-10-22, point 4 of
-- docs/handoff/next-builds/01-work-item-drift-and-reruns.md in
-- skyward-seo-pipeline). A work item is one cause and its row is the
-- current state of that cause, not a snapshot of a run: the slug is
-- stable across audits, so the same finding found twice updates the row
-- it wrote before rather than adding a second one. That is the right
-- shape, and it has one consequence nobody wanted.
--
-- `version` and `job_id` hold the run that LAST wrote the row. Every
-- re-run overwrites them. So "first found in audit 1, still open at
-- audit 4" survives only as `created_at`, a timestamp with no audit
-- behind it, and once Phase 1 and Phase 2 are on a schedule that is the
-- number an operator most wants: an item raised four audits ago and
-- never actioned reads very differently from one raised this morning.
--
-- WHY COLUMNS RATHER THAN NOTES. The alternative Adam's plan considered
-- and rejected was appending a sentence to `notes` on every re-run,
-- which by the fourth audit is a field nobody reads and which nothing
-- can sort, filter or count on.
--
-- WHY NOT change_log. The trigger on this table records what changed,
-- so the first version IS recoverable by walking every change back to
-- the insert. That is a reconstruction, not a fact, and it costs a scan
-- of the log per item to answer a question every list view asks.
--
-- NULL MEANS UNKNOWN, AND THE BACKFILL IS DELIBERATELY ABSENT. Every
-- row that exists today was written by some run, and we do not know
-- which run was its first. Copying today's `version` into
-- `first_version` would claim the current audit was the first one to
-- find it, which is false for every item that has ever been
-- re-confirmed -- and it is the same error as treating absence as
-- resolution, which this whole piece of work exists to stop. So the
-- existing rows say "unknown" and the next insert is the first one that
-- can honestly answer.
--
-- `confirmations` COUNTS AUDITS THAT FOUND IT, not writes. It is
-- nullable for the same reason: an item created before this migration
-- has been confirmed an unknown number of times, and 1 would be a
-- guess that reads as a fact. The writer sets it to 1 on insert and
-- increments it when a later audit finds the cause again.
--
-- The three columns are written ONCE and then left alone (first_version,
-- first_job_id) or only ever incremented (confirmations). `insert_only`
-- in the knowledge-base registry is what enforces the first two, the
-- same mechanism that already stops a re-run putting its suggested
-- `owner` back over a person's reassignment.
--
-- No trigger change: pipeline.log_change() reads the whole row rather
-- than a column list, so it picks these up unaided.

alter table pipeline.work_item
    add column if not exists first_version integer,
    add column if not exists first_job_id text,
    add column if not exists confirmations integer;

comment on column pipeline.work_item.first_version is
    'The audit version that FIRST raised this item. Set on insert and '
    'never updated; version holds the run that last wrote the row. NULL '
    'on any row created before pipeline_056, where the first run is '
    'genuinely unknown and was not guessed at.';

comment on column pipeline.work_item.first_job_id is
    'The run that FIRST raised this item. Set on insert and never '
    'updated. NULL where unknown, as above.';

comment on column pipeline.work_item.confirmations is
    'How many audits have found this cause, counting the one that raised '
    'it. 1 on insert, incremented by each later audit that finds it '
    'again. NULL where unknown, as above -- never defaulted to 1, which '
    'would read as "found once" for an item found every month since '
    'August.';

-- A count of audits cannot be negative, and zero would mean an item
-- nothing ever found.
alter table pipeline.work_item
    drop constraint if exists work_item_confirmations_positive;
alter table pipeline.work_item
    add constraint work_item_confirmations_positive
    check (confirmations is null or confirmations >= 1);

-- Enforces the first-found pair as a pair. An item that knows which
-- version first raised it and not which run, or the reverse, is a
-- half-written record, and the writer sets both from the same Run.
alter table pipeline.work_item
    drop constraint if exists work_item_first_found_is_whole;
alter table pipeline.work_item
    add constraint work_item_first_found_is_whole
    check ((first_version is null) = (first_job_id is null));
