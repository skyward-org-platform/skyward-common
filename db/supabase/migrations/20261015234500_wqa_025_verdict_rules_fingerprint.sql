-- wqa_025: which rules produced this verdict.
--
-- THREE DEFECTS IN ONE DAY HAD THE SAME SHAPE (2026-10-15): a derivation
-- is fixed, a site is not re-derived, and nothing on the stored row says
-- which rule wrote it.
--
--   * The thin-page rule was corrected to require a 200 and a measured
--     word count. InSoFast was re-derived; BusBank was not.
--   * The Canonicalize rule was corrected to require that the crawl found
--     an actual document, after 690 InSoFast stylesheets were told to add
--     canonical tags. BusBank still carries 61 of those rows, 28 of them
--     .css files, and a `record` run turned them into a live client
--     instruction.
--   * The location rule was corrected so a state page whose slug repeats
--     its section reads as a location. 95 InSoFast pages moved; the other
--     three sites were measured and needed none.
--
-- The second one was caught only because its `logic` string happens to
-- carry the words "Relabelled 2026-10-01". That was luck. Nothing else on
-- the row distinguishes a verdict derived this morning from one derived
-- three weeks ago under rules that have since been found wrong, and the
-- stale answer is indistinguishable from a current one at every reader:
-- the workbook, the deck, the dashboard and Phase 2's target set.
--
-- TWO COLUMNS, NOT ONE, because the two halves go stale separately and
-- did. `triage` writes the action and the logic; `relabel` writes the
-- page type in place on an existing version, so one row can hold an
-- action from the old rules and a type from the new. A single column
-- would have to claim one of those about both.
--
-- A FINGERPRINT RATHER THAN A VERSION NUMBER, because a number has to be
-- remembered and this is exactly the discipline that failed. The value is
-- a short hash of the source of the modules that decide, so it changes
-- whenever they do, with no step for anybody to forget. The cost is that
-- an edit to a comment also changes it and a site reads as stale when its
-- answers would not have differed. That is the safe direction: it says
-- "re-derive to be sure" more often than strictly necessary, never less.
--
-- NULL MEANS DERIVED BEFORE THIS MIGRATION, which is every row that
-- exists today and is not a defect to backfill. Backfilling would have to
-- invent which rules ran, and the whole point is that nobody knows.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot, and wqa_005 granted the dashboard's read-only role.

alter table wqa.page_verdict
    add column if not exists triage_rules_fingerprint text;

alter table wqa.page_verdict
    add column if not exists label_rules_fingerprint text;

comment on column wqa.page_verdict.triage_rules_fingerprint is
    'Short hash of the source of the modules that decided this row''s '
    'action and logic, written by `p1-wqa triage`. Compared against the '
    'running code so a site whose verdicts predate a rule change says so '
    'instead of reading as current. NULL means the row was derived '
    'before wqa_025 and nothing knows which rules ran.';

comment on column wqa.page_verdict.label_rules_fingerprint is
    'The same, for the page type, service and market labels, written by '
    '`p1-wqa relabel`. Separate from the triage fingerprint because '
    'relabel rewrites labels in place on an existing version, so one row '
    'can carry an action from the old rules and a type from the new.';
