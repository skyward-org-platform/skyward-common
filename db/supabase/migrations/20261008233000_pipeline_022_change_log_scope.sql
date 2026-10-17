-- 20261008233000_pipeline_022_change_log_scope.sql
--
-- Two changes to what pipeline.change_log records, decided by Adam on
-- 2026-10-08 (V1 rule: every edit an agent lane makes is in the change log,
-- with who and which verb).
--
-- 1. pipeline.work_item_task gains the log_change trigger. Recording or
--    clearing a pushed ClickUp task id (Phase 1 and Phase 2 `clickup
--    --record` / `--forget`) was the one edit path with no entry in the log.
--
-- 2. tsa.check_result logs HUMAN CORRECTIONS ONLY. Its log_change trigger
--    recorded every machine-written result: 322,000 of the log's 372,000
--    rows (787 MB) from two sites, and about 14 million per checks run on a
--    300,000-page site. The results are already versioned in their own
--    table, so logging them repeats them. What the log exists for is a
--    person's change: the reviewer status and note, which only the
--    `override` and `edit` verbs set, by UPDATE. Those keep being logged,
--    with the same generic function, so who and which verb are recorded as
--    before. A check result whose reviewer fields are carried into a new
--    version on insert is not re-logged: the person's change was logged when
--    they made it.
--
--    The existing machine-written rows already in the log are left as they
--    are (Adam's choice: no destructive cleanup).

drop trigger if exists log_change on pipeline.work_item_task;
create trigger log_change
    after insert or update or delete on pipeline.work_item_task
    for each row execute function pipeline.log_change();

drop trigger if exists log_change on tsa.check_result;
drop trigger if exists log_reviewer_change on tsa.check_result;
create trigger log_reviewer_change
    after update of reviewer_status, reviewer_note on tsa.check_result
    for each row
    when (old.reviewer_status is distinct from new.reviewer_status
          or old.reviewer_note is distinct from new.reviewer_note)
    execute function pipeline.log_change();

comment on trigger log_reviewer_change on tsa.check_result is
    'Logs a person''s correction (reviewer_status, reviewer_note) to '
    'pipeline.change_log. Machine-written results are versioned in this '
    'table and deliberately not logged (pipeline_022).';
