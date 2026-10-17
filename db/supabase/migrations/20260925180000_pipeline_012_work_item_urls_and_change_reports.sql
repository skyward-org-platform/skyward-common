-- 20260925180000_pipeline_012_work_item_urls_and_change_reports.sql
--
-- Which pages a piece of work covers, what an operator actually changed,
-- and what a re-run found had moved on the site.
--
-- Phase 1 and Phase 2 are going onto a schedule, and a scheduled audit has
-- to do two things at once: be the most up to date audit, and not churn
-- decisions that are still waiting on somebody. The only way to have both
-- is to know what changed -- measured, and declared. Spec:
-- docs/superpowers/specs/2026-09-25-change-detection-and-action-completion.md
-- in skyward-seo-pipeline.
--
-- THREE THINGS, AND THE VALUE IS IN COMPARING THEM
--
--   planned   the URLs the audit said an item covers, at creation
--   declared  what the operator says actually changed, at close
--   measured  what change detection observed
--
-- declared against measured is the verification loop: declared and changed
-- means it landed; declared and nothing changed means it did not land, or
-- landed somewhere else, or the crawl is stale; changed and not declared
-- means somebody edited the client's site without telling us.
--
-- WHO AND WHEN ARE COLUMNS, NEVER PROSE IN A NOTE (dashboard thread,
-- 2026-09-25). "Changed by Paul on Tuesday" inside a note cannot be
-- filtered, counted or trusted, and the whole point of these rows is that
-- somebody will one day count them.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

-- ---------------------------------------------------------------------
-- pipeline.work_item_url: one URL a work item covers.
--
-- work_item carries a url_count and no URLs, so "fix 23 thin pages" named
-- none of the 23. This is that list, plus the amendments an operator makes
-- when they close the item.
--
-- The PLANNED rows are immutable in practice: an amendment is a new row or
-- a state change with a reason, never an edit of the list. The audit's
-- original prediction is the only way to ever know how good our audits are
-- ("we said 40 pages, it took 62"), and an amendment with a reason is
-- evidence where a rewritten list is just a list.
--
-- EXPECTED CHANGE, not merely membership. A page can be affected without
-- being edited: in a redirect the old URL should start answering 301 while
-- the destination gains links and traffic and its own content stays put.
-- Those are opposite expectations, and without recording which applies,
-- verification cannot tell "the destination did not change, as expected"
-- from "nothing happened at all".
--
-- SUBTRACTION IS A DEFERRAL, NEVER A DELETION (Adam, 2026-09-25). An
-- excluded page keeps its row, keeps its reason, and re-enters the next
-- audit. Two reasons, recorded as distinct values because they mean
-- opposite things downstream:
--
--   no_issue  our finding was wrong. This has to reach the audit as an
--             operator judgement, or the next run raises the same false
--             finding and the operator subtracts it again, forever.
--   not_done  our finding was right and the work is outstanding. The next
--             audit says outstanding, with an age.
--
-- No second work item is created for the excluded pages. The item closes
-- without them.
-- ---------------------------------------------------------------------
create table pipeline.work_item_url (
    work_item_url_id uuid primary key default gen_random_uuid(),
    work_item_id    uuid not null
                        references pipeline.work_item(work_item_id)
                        on delete cascade,
    -- Denormalised from the item on purpose: every dashboard read and every
    -- policy filters by site, and a join to get there would be a join on
    -- every row.
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    url             text not null,

    -- What this URL is supposed to look like once the work is done. A
    -- vocabulary rather than free text, because verification reads it:
    -- Phase 2 will add its own values in its own migration.
    expected_change text not null
                        check (expected_change in (
                            'redirected_away',    -- should start 301ing
                            'receives_redirect',  -- gains links and traffic,
                                                  -- its own content unchanged
                            'content_rewritten',
                            'removed',
                            'noindexed',
                            'other')),

    state           text not null default 'planned'
                        check (state in ('planned', 'added', 'excluded')),
    -- Why this row was added or excluded. Required for both: an amendment
    -- without a reason is an untraceable change to what the audit said.
    reason          text,
    -- Only for an exclusion, and only these two.
    reason_kind     text
                        check (reason_kind is null
                               or reason_kind in ('no_issue', 'not_done')),

    -- Who and when, as columns.
    set_by          text,
    set_at          timestamptz not null default now(),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    constraint work_item_url_natural_key
        unique (work_item_id, url),

    -- An amendment says why, and an exclusion says which of the two kinds
    -- it is. A planned row needs neither: the audit is its reason.
    constraint work_item_url_amendment_has_a_reason
        check (state = 'planned'
               or (reason is not null and length(btrim(reason)) > 0)),
    constraint work_item_url_exclusion_says_which_kind
        check ((state = 'excluded') = (reason_kind is not null))
);

create index work_item_url_by_item on pipeline.work_item_url (work_item_id);
create index work_item_url_by_site on pipeline.work_item_url (domain_id, url);

-- Updates and deletes only, as on work_item: recovery is about an
-- overwrite destroying somebody's reason, and a first write is reproducible
-- from the run that made it.
create trigger log_change after update or delete on pipeline.work_item_url
    for each row execute function pipeline.log_change();

-- ---------------------------------------------------------------------
-- The completion facts, on the item itself.
--
-- completed_at is the date the OPERATOR says the change landed, which is
-- theirs and not ours: they did the work on Tuesday and closed the task on
-- Friday, and measuring the outcome from Friday would attribute three days
-- of traffic to the wrong cause.
--
-- `status` already says whether an item is done. These say when, by whom,
-- and what they did, which is what verification and the outcome
-- measurement need.
-- ---------------------------------------------------------------------
alter table pipeline.work_item
    add column if not exists completed_at timestamptz,
    add column if not exists completed_by text,
    add column if not exists completed_note text;

comment on column pipeline.work_item.completed_at is
    'When the operator says the change actually landed on the site, which '
    'is not when they closed the task. Outcome measurement starts here.';
comment on column pipeline.work_item.completed_by is
    'Who did it. A column rather than prose in a note, so it can be '
    'counted.';
comment on column pipeline.work_item.completed_note is
    'What they did, in their words. Kept apart from `notes`, which the '
    'phase writes.';

-- ---------------------------------------------------------------------
-- pipeline.change_report: one comparison of two audits of one site.
--
-- Written by the phase, because the dashboard only reads Supabase and
-- never BigQuery, so anything it shows has to be put here by whoever
-- produced it.
--
-- One row per (site, project, module, pair of versions). Re-running the
-- same comparison overwrites it: it is derived from two audits that do not
-- change, so a second row would be a duplicate rather than a history.
-- ---------------------------------------------------------------------
create table pipeline.change_report (
    change_report_id uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    project_id      bigint
                        references meta.projects(project_id),
    module          text not null,

    -- The two audits compared, as the producing module numbers them.
    version_before  integer not null,
    version_after   integer not null,
    -- When each audit was BUILT, so a reader can see the period the
    -- comparison spans without joining to the runs table in BigQuery.
    built_before    timestamptz,
    built_after     timestamptz,

    -- False when the two reads are not comparable at all -- a crawl that
    -- reached a fraction of the site, a host that throttled us. The
    -- findings are still recorded, and a reader must not treat them as
    -- facts about the site.
    comparable      boolean not null default true,
    -- Coverage in full: what each read reached, what shrank, what was
    -- refused, and the warning in words.
    coverage        jsonb not null default '{}'::jsonb,
    -- The site-level summary: counts per pattern, the site-level events,
    -- what was out of scope.
    summary         jsonb not null default '{}'::jsonb,

    job_id          text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    constraint change_report_natural_key
        unique (domain_id, project_id, module, version_before, version_after)
);

create index change_report_by_site
    on pipeline.change_report (domain_id, module, version_after desc);

-- ---------------------------------------------------------------------
-- pipeline.change_finding: one named finding in a comparison.
--
-- A finding, not a diff: "the copy was rewritten and traffic is down in the
-- same period", with the signals that evidence it. The evidence carries the
-- window each number was measured over, because two window averages
-- compared is not the same claim as a dated change.
--
-- `look` is the operator's queue. A finding standing only on a read that
-- shrank is recorded with look false: it is not trustworthy this run.
-- ---------------------------------------------------------------------
create table pipeline.change_finding (
    change_finding_id uuid primary key default gen_random_uuid(),
    change_report_id uuid not null
                        references pipeline.change_report(change_report_id)
                        on delete cascade,
    -- Denormalised for the same reason as work_item_url.domain_id.
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,

    subject_type    text not null
                        check (subject_type in ('page', 'site')),
    -- The URL for a page finding, or the site-level event's subject.
    subject_key     text not null,
    -- Whether the page is in both audits, new, gone, or simply not read
    -- this time. 'gone' and 'not_seen' are different claims and only one
    -- of them is about the client.
    state           text
                        check (state is null
                               or state in ('present', 'appeared', 'gone',
                                            'not_seen')),

    pattern         text not null,
    note            text,
    look            boolean not null default false,
    -- The signals behind it: what moved, from what to what, over which
    -- window, and whether that signal is material or movement.
    evidence        jsonb not null default '[]'::jsonb,

    created_at      timestamptz not null default now(),

    constraint change_finding_natural_key
        unique (change_report_id, subject_type, subject_key, pattern)
);

create index change_finding_queue
    on pipeline.change_finding (change_report_id, look, subject_type);
create index change_finding_by_url
    on pipeline.change_finding (domain_id, subject_key);
