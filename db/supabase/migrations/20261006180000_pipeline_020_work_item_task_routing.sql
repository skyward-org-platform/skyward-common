-- 20261006180000_pipeline_020_work_item_task_routing.sql
--
-- Which ClickUp list each pushed implementation task went into, and why.
-- Spec: skyward-seo-pipeline docs/superpowers/specs/
-- 2026-10-05-clickup-list-routing.md (chain agreed with Adam 2026-10-01).
--
-- Implementation tasks route through a fixed chain of list names inside
-- the client's folder (Website SEO, SEO, web dev, Action List, SEO
-- Pipeline; first match wins). Recording the outcome per task lets a
-- decision be audited later, and lets task-check notice when today's
-- route differs from where a task was put.
--
-- Three nullable columns. Rows pushed before this have no route recorded
-- and stay null; nothing reads these columns as required. The natural key
-- stays (work_item_id, slice_key).

alter table pipeline.work_item_task
    add column if not exists list_id text,
    add column if not exists matched_step text,
    add column if not exists list_reason text;

comment on column pipeline.work_item_task.list_id is
    'The ClickUp list the task was created in, as chosen by the routing '
    'chain. Null for tasks pushed before routing was recorded.';

comment on column pipeline.work_item_task.matched_step is
    'The routing rung that chose the list, by NAME (e.g. "Website SEO", '
    '"web dev", "SEO Pipeline"), never by position, so a later change to '
    'the chain does not reinterpret old rows. "SEO Pipeline" means parked '
    'in Skyward''s own list, which no client looks at.';

comment on column pipeline.work_item_task.list_reason is
    'Why that list: the list name it matched and the names rejected on '
    'the way.';
