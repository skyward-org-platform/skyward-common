-- 20261009000000_pipeline_023_action_rollup.sql
--
-- One list of things somebody has to do, across both tables that hold
-- them. Decided with Adam on 2026-10-08, for the dashboard's new
-- Actions page under Strategy & Planning.
--
-- Today a site's outstanding work lives in two places that a reader has
-- to know about separately:
--
--   pipeline.work_item      something to DO -- fix these 40 pages, write
--                           this content, rebuild this template.
--   pipeline.open_question  something to ANSWER -- a gap in what we hold
--                           about the client, or two sources that
--                           disagree.
--
-- Both are "an operator has to act on this before the next phase is
-- safe", both carry who owns it and whether it is settled, and every
-- phase writes to both. So the page should show one list, and the
-- distinction becomes a column you can filter on rather than two tabs
-- a reader has to visit in turn.
--
-- NOTE on pipeline_001's own comment, which argued AGAINST "a table per
-- phase and a union view over them later." That still holds and this
-- does not reverse it: open_question is still ONE table for every
-- phase, and so is work_item. The union here is across the two KINDS of
-- outstanding item, not across phases, and it is a read-only view over
-- tables that each keep their own shape and their own writers.
--
-- Two changes:
--
--   1. open_question gains `priority`, with work_item's exact
--      vocabulary. It is the only field work_item has that a question
--      plausibly needs, and without it every question sorts below every
--      work item or above it, with no way to say that one unanswered
--      question is what is actually blocking Phase 3. Nullable: a
--      question raised without a priority is the normal case and must
--      stay cheap to raise.
--
--   2. pipeline.action, a view. Read-only on purpose -- writes keep
--      going to whichever table owns the row, through the existing
--      verbs, so the change_log triggers and the natural keys are
--      untouched. Nothing about either table's behaviour changes.

alter table pipeline.open_question
    add column priority text
        check (priority in ('P0','P1','P2','P3'));

comment on column pipeline.open_question.priority is
    'How much this one blocks, same vocabulary as pipeline.work_item.priority. '
    'Null is the normal case: a question raised in passing has no priority '
    'until somebody judges it. Added 20261009 for the unified action list.';

-- The two status vocabularies differ and BOTH are kept:
--
--   status  the row''s own value, in its own table''s words. A reader
--           who knows they are looking at a question still sees
--           "answered", not a flattened synonym.
--   state   the same thing in one vocabulary, so a mixed list can sort
--           and filter without the caller learning both.
--
-- open_question.no_longer_detected maps to 'dropped' beside 'dismissed'
-- on purpose, and they are NOT the same event: dismissed means somebody
-- decided it did not matter, no_longer_detected means the gap it was
-- raised about has since been filled, so nobody ever answered it. The
-- difference survives in `status`; `state` only says it is off the list.
create or replace view pipeline.action as
select
    'work_item'::text               as source,
    w.work_item_id                  as action_id,
    w.domain_id,
    w.project_id,
    w.module,
    w.slug,
    w.title,
    w.description                   as detail,
    w.action                        as category,
    w.priority,
    w.owner,
    w.status,
    case w.status
        when 'wont_do' then 'dropped'
        else w.status
    end                             as state,
    w.url_count,
    w.estimate_hours,
    w.blocks_phase,
    w.waits_on_phase,
    w.clickup_task_id,
    null::text                      as subject_table,
    null::text                      as subject_column,
    null::text                      as subject_key,
    null::jsonb                     as findings,
    null::text                      as best_guess,
    null::text                      as confidence,
    w.completed_note                as outcome,
    w.completed_by                  as resolved_by,
    w.completed_at                  as resolved_at,
    w.version,
    w.job_id,
    w.sources,
    w.notes,
    w.created_at,
    w.updated_at
from pipeline.work_item w
union all
select
    'open_question'::text           as source,
    q.question_id                   as action_id,
    q.domain_id,
    null::bigint                    as project_id,
    q.module,
    null::text                      as slug,
    q.question                      as title,
    q.detail,
    q.kind                          as category,
    q.priority,
    q.answerable_by                 as owner,
    q.status,
    case q.status
        when 'answered'            then 'done'
        when 'dismissed'           then 'dropped'
        when 'no_longer_detected'  then 'dropped'
        else q.status
    end                             as state,
    null::integer                   as url_count,
    null::numeric                   as estimate_hours,
    null::text                      as blocks_phase,
    null::text                      as waits_on_phase,
    null::text                      as clickup_task_id,
    q.subject_table,
    q.subject_column,
    q.subject_key,
    q.findings,
    q.best_guess,
    q.confidence,
    q.resolution                    as outcome,
    q.answered_by                   as resolved_by,
    q.answered_at                   as resolved_at,
    null::integer                   as version,
    null::text                      as job_id,
    q.sources,
    q.notes,
    q.created_at,
    q.updated_at
from pipeline.open_question q;

comment on view pipeline.action is
    'Every outstanding item for a site, from both tables that hold one: '
    'pipeline.work_item (something to do) and pipeline.open_question '
    '(something to answer). `source` says which table a row came from and '
    '`action_id` is its primary key there. READ ONLY -- write through the '
    'owning table''s verbs, never through this view. Columns only one side '
    'has are null on the other: url_count, estimate_hours, blocks_phase, '
    'waits_on_phase, slug, version, job_id and clickup_task_id are work '
    'items only; subject_table/column/key, findings, best_guess and '
    'confidence are questions only.';

comment on column pipeline.action.state is
    'status in one vocabulary across both sources: open, in_progress, done, '
    'dropped. Filter and sort on this; read `status` to see what the row '
    'itself says.';

comment on column pipeline.action.category is
    'work_item.action (what kind of work) or open_question.kind (gap, '
    'contradiction, question, other). What sub-divides the list within a '
    'source, not what distinguishes the sources -- that is `source`.';
