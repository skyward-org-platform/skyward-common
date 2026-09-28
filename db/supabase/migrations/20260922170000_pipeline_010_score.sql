-- 20260922170000_pipeline_010_score.sql
--
-- The score a phase gives a page, and the roll-ups over it. V1 asks for
-- "every page graded, sliced by any label, plus an overall site score",
-- for Phase 1 now and Phase 2's technical score on the same shape later.
--
-- One table, not one per phase: `module` says which phase graded, exactly
-- as pipeline.work_item and pipeline.open_question do. A union view per
-- phase would otherwise be needed forever.
--
-- Rows are one of two shapes, told apart by subject_type:
--   'page'  one row per URL, with its components (every check, its status
--           and the measured value behind it)
--   a label roll-up ('site', 'page_type', 'service_category', 'market')
--           with the group's mean, how many pages it covers, and the
--           checks that failed most
--
-- VERSIONED, and a full snapshot per version: a version holds a score for
-- every page the verdicts held, so the latest version is the whole
-- picture. That is why the natural key carries version -- re-grading a
-- later verdict version must not overwrite what the earlier one scored,
-- since the deliverable already filed quotes it.
--
-- score is NULLABLE on purpose. A page nobody would optimize (an image, a
-- redirecting URL form, a 404 left as a 404) is not graded, and `note`
-- says why. Zero would read as a measurement, and would drag every
-- roll-up down for pages behaving correctly.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

create table pipeline.score (
    score_id        uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    -- No action on delete, as on work_item: removing a project must not
    -- silently take the record of what was graded with it.
    project_id      bigint
                        references meta.projects(project_id),
    module          text not null,
    -- The version of the module's own output this grades: for Phase 1,
    -- the wqa.page_verdict version.
    version         integer not null,

    subject_type    text not null
                        check (subject_type in
                            ('page','site','page_type','service_category',
                             'market')),
    -- A URL for a page; the label's value for a roll-up; 'site' for the
    -- site row.
    subject_key     text not null,

    -- Null when the subject was not graded; `note` says why.
    score           numeric(5,2) check (score is null
                                        or score between 0 and 100),
    band            text not null
                        check (band in ('strong','good','needs work','poor',
                                        'not scored')),

    -- Roll-ups only.
    pages_scored     integer check (pages_scored is null or pages_scored >= 0),
    pages_not_scored integer check (pages_not_scored is null
                                    or pages_not_scored >= 0),

    -- Page rows: every check, its status and the measured value.
    -- Roll-ups: the checks that failed most, so a number says what to fix.
    components      jsonb not null default '[]'::jsonb,

    -- The labels this page carried when it was graded, so a roll-up can be
    -- rebuilt from the page rows without re-reading the verdicts.
    page_type       text,
    service_category text,
    market          text,

    note            text,
    job_id          text,
    sources         text[] not null default '{}',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    constraint score_natural_key
        unique (domain_id, project_id, module, version, subject_type,
                subject_key)
);

-- The read is always "this project's newest scores for this module".
create index score_project_module_version
    on pipeline.score (domain_id, project_id, module, version);

-- Updates and deletes only, as on work_item: recovery is about
-- overwrites, and a first write is reproducible from its run.
create trigger log_change after update or delete on pipeline.score
    for each row execute function pipeline.log_change();
