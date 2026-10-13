-- 20261013140000_pipeline_032_snapshot_exclusion_and_prior.sql
--
-- Two things a reading could not record, both found by testing rather
-- than by reading the code (P0-Testing, 2026-10-13).
--
-- AN EXCLUDED PAGE WAS STORED INDISTINGUISHABLY FROM AN INCLUDED ONE
-- (finding 10). pipeline.work_item_url.state says planned, added or
-- excluded, and reason_kind says which of the two kinds of exclusion it
-- is: no_issue, meaning our finding was wrong, or not_done, meaning the
-- work is outstanding. Those mean opposite things downstream. The
-- snapshot's reader selected both columns and the stored row had nowhere
-- to put them, so the rows survived and the fact that they were excluded
-- did not -- which is precisely the evidence db.py's own docstring says
-- must not be lost, because an exclusion is a deferral and the next audit
-- has to know whether deferring was right.
--
-- THE PRIOR WINDOW HAD NO COMPLETENESS GUARD (finding 9). settled checked
-- only that a source covered the CURRENT window's end. A source whose
-- history begins inside the prior window -- a site whose Jepto sync
-- started mid-window -- was aggregated and labelled exactly like a full
-- one: 28 days of current against 3 days of prior reads as +9233% year on
-- year, and the year-on-year comparison is the whole reason the prior
-- window exists. The per-source earliest date now sits on the header
-- beside the latest, so "these periods are not comparable" is a stored
-- fact rather than an inference nobody makes.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

alter table pipeline.page_snapshot_row
    -- Copied from work_item_url at reading time, so the reading stands
    -- alone. Same vocabulary as the source column, deliberately: a second
    -- set of words for one fact is how the two drift.
    add column if not exists url_state text
        check (url_state is null
               or url_state in ('planned', 'added', 'excluded')),
    add column if not exists url_reason_kind text
        check (url_reason_kind is null
               or url_reason_kind in ('no_issue', 'not_done'));

alter table pipeline.page_snapshot_row
    -- An exclusion says which kind it is, exactly as work_item_url
    -- requires. A control has no state at all.
    add constraint page_snapshot_row_exclusion_says_which_kind
        check ((url_state = 'excluded') = (url_reason_kind is not null));

alter table pipeline.page_snapshot
    -- Per source, the EARLIEST date it has data for, so a prior window the
    -- source does not cover is visible rather than compared against.
    add column if not exists sources_complete_from jsonb
        not null default '{}'::jsonb;

comment on column pipeline.page_snapshot_row.url_state is
    'Whether this URL was planned, added or excluded on the action at '
    'reading time. An exclusion is a DEFERRAL, so whether deferring was '
    'right is a question the next audit asks of this row.';
comment on column pipeline.page_snapshot_row.url_reason_kind is
    'Which kind of exclusion: no_issue means our finding was wrong and '
    'the next audit must not raise it again; not_done means the work is '
    'outstanding. Opposite meanings downstream.';
comment on column pipeline.page_snapshot.sources_complete_from is
    'Per source, the earliest date it has data for. A prior window that '
    'begins before this is not a comparable period, and a year-on-year '
    'figure built on it is wrong rather than merely noisy.';
