-- 20260929220000_pipeline_017_work_item_tasks.sql
--
-- The ClickUp tasks a Phase 1 audit pushed, and what has happened to them.
--
-- Phase 1 has never created a task. It only ever READ an id, so InsoFast's
-- five work items sat in pipeline.work_item and nobody had been given the
-- work (Adam, 2026-09-29: "i want to make sure this is built into the P1
-- lane").
--
-- pipeline.work_item.clickup_task_id can hold a PARENT's id. A child task
-- maps to a filtered SLICE of a work item's page list -- "fix the redirects
-- landing on a 404", "ship the ones that already resolve to a live 200",
-- "choose destinations for the ones with no chain data" -- and a slice has
-- no row of its own, so there was nowhere to put its id.
--
-- WHY name_at_push AND url_count_at_push ARE THE POINT, as much as the id.
--
-- Adam's task-check design is bidirectional. A status that simply moved is
-- applied automatically. But if somebody added scope, renamed the task or
-- left a comment that changes the ask, that is NOT applied -- because they
-- may have found something and made the task MORE ACCURATE, in which case
-- the stored action item is the thing that is out of date.
--
-- That only works if we know what we ASKED for. Without these two columns a
-- task whose scope somebody doubled is indistinguishable from one nobody
-- touched, and the whole distinction collapses into "the status changed".
--
-- A TASK THAT VANISHED IS NOT A TASK THAT WAS FINISHED, which is why
-- clickup_status is nullable and last_seen_at is separate from it: never
-- seen and seen-and-gone are different facts, and only one of them means
-- somebody deleted it.

create table if not exists pipeline.work_item_task (
    -- uuid, matching work_item's own key. The dry run caught this as
    -- bigserial against a uuid foreign key, which is what a dry run is for.
    work_item_task_id     uuid        primary key default gen_random_uuid(),

    -- Scope, on every row, like every other table here.
    domain_id             bigint      not null,
    project_id            bigint      not null,

    -- The action this task came from. Deleting the action takes its tasks
    -- with it: a task row pointing at nothing cannot be read back onto
    -- anything.
    work_item_id          uuid        not null
                            references pipeline.work_item(work_item_id)
                            on delete cascade,

    -- Which rule this task is. 'parent' for the umbrella task, otherwise
    -- the slice: 'dead_destination', 'ready_to_ship', 'destination_tbd'.
    -- Free text rather than an enum, because the slices are per action type
    -- and a new one should not need a migration.
    slice_key             text        not null,

    clickup_task_id       text        not null,
    -- Null for a parent. Set for a child, so the tree can be rebuilt
    -- without asking ClickUp.
    clickup_parent_task_id text,

    -- WHAT WE ASKED FOR. Compared against what ClickUp now says, to tell a
    -- status change from a changed ask.
    name_at_push          text        not null,
    url_count_at_push     integer,

    -- WHAT CLICKUP LAST SAID. Null means we have not looked yet, which is
    -- not the same as the task having no status.
    clickup_status        text,
    clickup_status_at     timestamptz,
    -- When task-check last reached it. A row with a last_seen_at and a null
    -- status has been looked for and not found.
    last_seen_at          timestamptz,

    pushed_at             timestamptz not null default now(),
    updated_at            timestamptz not null default now(),
    job_id                text,

    -- One row per (action, slice): pushing twice updates rather than
    -- duplicating, so a re-push cannot leave two ids for one rule.
    constraint work_item_task_unique_slice
        unique (work_item_id, slice_key)
);

-- The read task-check makes: everything pushed for a site, newest first.
create index if not exists work_item_task_by_site
    on pipeline.work_item_task (domain_id, project_id, pushed_at desc);

-- The read the dashboard makes: the tasks for one action.
create index if not exists work_item_task_by_item
    on pipeline.work_item_task (work_item_id);

-- The read that answers "we have this ClickUp id, what is it ours for".
create index if not exists work_item_task_by_clickup_id
    on pipeline.work_item_task (clickup_task_id);

comment on table pipeline.work_item_task is
  'ClickUp tasks pushed from a Phase 1 action, with what was asked for at '
  'push time so a changed scope can be told from a changed status.';
comment on column pipeline.work_item_task.slice_key is
  'Which rule the task is: ''parent'', or the filtered slice of the action''s '
  'page list that it covers.';
comment on column pipeline.work_item_task.name_at_push is
  'The task name we created. Differs from ClickUp''s current name when '
  'somebody has changed the ask, which task-check raises rather than applies.';
comment on column pipeline.work_item_task.clickup_status is
  'Last status seen. Null means never looked; a null status with a '
  'last_seen_at means looked for and not found, which is not ''done''.';
