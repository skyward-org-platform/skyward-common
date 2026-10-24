-- 20261009001000_pipeline_024_group_check_ids.sql
--
-- Which checks a group covers, for groups whose membership is a
-- PREDICATE rather than a list. Approved by Adam, 2026-10-08, for
-- Phase 2's adoption of Phase 1's stored-grouping contract.
--
-- TWO KINDS OF GROUP now share this table, and the difference is why
-- this column exists rather than a join table:
--
--   A PARTITION's membership IS its definition. "Pages 1 to 500 of this
--   redirect set" has no rule that regenerates it, so each URL is
--   assigned to exactly one group via work_item_url.group_slice_key,
--   and Phase 1 raises if a URL lands in two. That raise is a
--   correctness guarantee and is deliberately not relaxed here.
--
--   A PREDICATE's membership is DERIVED. "Every page failing the title
--   check" is defined by the check; the page list is recomputed from
--   tsa.check_result. Overlap is normal rather than anomalous -- one
--   page can fail titles and H1 both -- so these groups cannot be
--   expressed as a partition of URLs at all. Forcing them into one
--   would silently drop work out of the pushed tasks.
--
-- WHY NOT A work_item_group_url JOIN TABLE, which was the alternative:
-- it would store the projection of a predicate over today's check
-- results. That looks safer and is worse. A page that stops failing
-- titles leaves the stored membership still claiming it does, and
-- nothing reconciles the two -- the exact drift that storing groups
-- exists to prevent, in the one case where membership is genuinely
-- derivable. A nullable column beats a new shared table.
--
-- WHY NOT `note` OR `split_reason`: `note` is free text, and a push
-- that parses a comment to decide which pages a task covers is a rule
-- hiding in a comment -- the next person to write a note breaks the
-- push. `split_reason` is a vocabulary for WHY a split happened, not a
-- slot for what it covers, and ids placed there would match anyone
-- filtering on it.
--
-- Nullable, no default, no backfill: Phase 1's partition rows leave it
-- null and are untouched.

alter table pipeline.work_item_group
    add column check_ids text[];

comment on column pipeline.work_item_group.check_ids is
    'The checks a predicate group covers. Its pages are those failing these '
    'checks at read time, so the count can legitimately move between pushes. '
    'Null for partition groups, whose membership is '
    'work_item_url.group_slice_key.';
