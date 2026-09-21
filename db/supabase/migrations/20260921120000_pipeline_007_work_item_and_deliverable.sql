-- 20260921120000_pipeline_007_work_item_and_deliverable.sql
--
-- What a phase leaves behind for people: the work somebody has to do,
-- and the artifacts that were handed over. Both live in `pipeline`, not
-- in a phase schema, for the reason open_question does: every phase
-- produces them in the same shape, and a table per phase would need a
-- union view forever.
--
-- Grants: none written here. The per-person logins migration set default
-- privileges on this schema, so a table created here by postgres is
-- readable and writable by adam and pipeline_bot automatically. Apply as
-- postgres, or that default does not fire.

-- ---------------------------------------------------------------------
-- pipeline.work_item: one unit of implementation work.
--
-- Keyed on (domain_id, module, slug) with NO version in the key, on
-- purpose. A work item is the current state of a piece of work -- "ship
-- the redirect map" -- not a snapshot of one run. A re-run updates the
-- same item, and its status, owner and history carry across runs, which
-- is what makes it closable with an audit trail.
--
-- Today this work exists only as ClickUp tasks, generated from a file
-- whose counts were typed by hand for one client. This is where it is
-- recorded instead; ClickUp becomes a view of it.
-- ---------------------------------------------------------------------
create table pipeline.work_item (
    work_item_id    uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    -- No action on delete: removing a project must not silently take
    -- the record of work already assigned with it. Same rule as
    -- wqa.page_verdict.project_id.
    project_id      bigint
                        references meta.projects(project_id),

    module          text not null,
    -- Stable across runs: "p1-redirect-map", not a generated title.
    slug            text not null,

    title           text not null,
    description     text,

    -- The verdict this work implements, when there is one. Free text
    -- rather than a CHECK: other modules have their own vocabularies.
    action          text,
    url_count       integer check (url_count is null or url_count >= 0),

    priority        text check (priority in ('P0','P1','P2','P3')),
    owner           text check (owner in ('skyward','client','dev')),
    estimate_hours  numeric check (estimate_hours is null
                                   or estimate_hours >= 0),

    status          text not null default 'open'
                        check (status in
                            ('open','in_progress','done','wont_do')),

    -- Which later phase cannot finish until this is done, if any. Text
    -- so a phase can be named the way the milestone names it.
    blocks_phase    text,

    -- Lineage: the verdict version and run that last wrote this item.
    version         integer,
    job_id          text,

    -- For the ClickUp sync, when it exists. Never the source of truth.
    clickup_task_id text,

    sources         text[] not null default '{}',
    notes           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    constraint work_item_natural_key unique (domain_id, module, slug)
);

-- ---------------------------------------------------------------------
-- pipeline.deliverable: one artifact per run.
--
-- The spec keys this on (domain_id, project_id, version, kind). Module
-- is added to the key deliberately: Phase 1 and Phase 2 version
-- independently under the SAME project, so without it a Phase 2 workbook
-- v1 would overwrite Phase 1's workbook v1 and nothing would say so.
-- ---------------------------------------------------------------------
create table pipeline.deliverable (
    deliverable_id  uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    project_id      bigint not null
                        references meta.projects(project_id),
    module          text not null,
    -- The version of the module's own output this artifact renders.
    version         integer not null,
    kind            text not null
                        check (kind in ('workbook','deck','doc','task_set')),

    title           text,
    -- Drive is the delivery channel. Both, because a file id survives a
    -- rename and a URL is what a person clicks.
    drive_file_id   text,
    url             text,

    status          text not null default 'draft'
                        check (status in ('draft','sent','superseded')),

    job_id          text,
    sources         text[] not null default '{}',
    notes           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    constraint deliverable_natural_key
        unique (domain_id, project_id, module, version, kind)
);

-- Updates and deletes only, as on wqa.page_verdict: recovery is about
-- overwrites, and a first write is reproducible from its run.
create trigger log_change after update or delete on pipeline.work_item
    for each row execute function pipeline.log_change();
create trigger log_change after update or delete on pipeline.deliverable
    for each row execute function pipeline.log_change();
