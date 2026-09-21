-- 20260921160000_pipeline_009_run_progress.sql
--
-- Live progress for a module run: how far through it is, which stage is
-- running, and whether the process is still alive. Written while the run
-- is in flight, so an agent or the dashboard can answer "is it working
-- or hung" without reading a log.
--
-- One row per run, holding its LATEST state. The writer upserts on
-- progress_id every tick (coalesced to a few seconds) rather than
-- appending, because nothing reads the history and a four-hour crawl
-- polled every minute would otherwise be hundreds of rows per run.
--
-- progress_id, not job_id, is the key: a WQA run's discovery and gate
-- happen before its BigQuery Run row exists, so the progress row is
-- created first and job_id is filled in when the Run opens. A run that
-- quits at the gate never gets one.
--
-- Advisory only. runs.status in BigQuery stays the source of truth for
-- whether a run succeeded. A process that is killed leaves its row at
-- 'running'; updated_at is the heartbeat that shows it went quiet.
--
-- No change-log trigger, unlike work_item: this is machine state
-- rewritten every few seconds, not a record anyone edits, and logging it
-- would bury the real changes.
--
-- Grants: none written here. Default privileges on this schema grant
-- adam and pipeline_bot. Apply as postgres, or that default does not
-- fire.

create table pipeline.run_progress (
    progress_id     uuid primary key,
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    project_id      bigint
                        references meta.projects(project_id),
    -- The module's own name, as on its BigQuery run row.
    module          text not null,
    -- The BigQuery run's job_id, once the Run has opened.
    job_id          text,

    status          text not null default 'running'
                        check (status in
                            ('running','done','failed','cancelled')),
    -- Never decreases within a run; 100 exactly when status = 'done'.
    overall_pct     numeric(5,2) not null default 0
                        check (overall_pct between 0 and 100),

    -- The stage the latest event was about, and its own percentage.
    stage           text,
    stage_pct       numeric(5,2)
                        check (stage_pct is null
                               or stage_pct between 0 and 100),
    detail          text,
    -- Every stage in the module's declared order: id, label, weight,
    -- status, pct, detail and its timestamps.
    stages          jsonb not null default '[]'::jsonb,
    error           text,

    started_at      timestamptz not null default now(),
    -- The heartbeat.
    updated_at      timestamptz not null default now(),
    finished_at     timestamptz
);

-- The read is always "the newest run of this module on this site".
create index run_progress_site_module_started
    on pipeline.run_progress (domain_id, module, started_at desc);
