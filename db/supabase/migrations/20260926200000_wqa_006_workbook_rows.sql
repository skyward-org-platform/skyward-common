-- 20260926200000_wqa_006_workbook_rows.sql
--
-- The Phase 1 workbook, stored as the builder built it.
--
-- WHY THIS EXISTS
--
-- Phase 1 ships a 16-sheet .xlsx per SOP 7.1. It builds every sheet as a
-- pandas frame, writes the file, and throws the frames away. Everything
-- the builder computes while writing -- Severity, Owner, Cross-reference,
-- Ready to Ship, the row numbers, the category renames, the checklist --
-- exists nowhere afterwards.
--
-- So when the dashboard came to show the workbook, it re-derived all of it
-- in SQL from a second reading of workbook.py. Two implementations of one
-- document. They disagreed within a day: two sheets missing entirely
-- (Scorecard and Funnel Summary), the destination's measured-versus-
-- proposed source dropped from the Redirect Map, half of each keyword pair
-- dropped from URL Triage, and half the hand-over checklist gone
-- (Tester's diff against the file, 2026-09-26).
--
-- Patching those one at a time fixes today's disagreements and guarantees tomorrow's.
-- The builder is the only thing that knows what the workbook is, so the
-- builder stores what it made and everything downstream renders that.
-- Adam, 2026-09-26: "We need to make sure we're storing the data properly
-- and then displaying the data properly."
--
-- WHAT THIS IS NOT
--
-- Not a replacement for the file. The .xlsx keeps being produced and filed
-- to Drive; this is the same rows, where the rest of the audit already is.
--
-- Not a general "deliverable rows" table. A workbook tab has a heading
-- order, a row order and cells whose shape differs per tab, and that is
-- what is modelled. A different deliverable with a different shape gets
-- its own table rather than being bent into this one.
--
-- VERSIONED LIKE EVERY OTHER PHASE 1 OUTPUT. One row set per audit
-- version, never overwritten: an old workbook stays readable, and "what
-- did we hand them in September" has an answer. Readers take the newest
-- version per tab -- per THING, not one project-wide maximum, which is
-- this pipeline's standing rule.
--
-- Grants: wqa_005 granted dashboard_ro select on everything in this schema
-- and set the default for new tables, so applying this as postgres carries
-- that forward. The explicit grants below are belt and braces, and are
-- harmless if the default already fired.

-- ---------------------------------------------------------------------
-- wqa.workbook_tab: one tab of one build of the workbook.
--
-- The tab's identity is the builder's: its number as the spreadsheet
-- numbers it ('0', '5b', '14'), its name, and its position in the file.
-- `columns` is the heading order, as [{"key": ..., "label": ...}], so a
-- reader draws the tab without knowing anything about it -- and so a
-- column the builder adds appears downstream with no code change.
-- ---------------------------------------------------------------------
create table wqa.workbook_tab (
    workbook_tab_id uuid primary key default gen_random_uuid(),

    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,
    project_id      bigint not null,
    -- The page_verdict version this workbook was built from.
    version         integer not null,
    -- The run that built it, so a tab can be traced to its run row.
    job_id          text,

    -- Stable identifier used in a URL: 'url-triage', 'scorecard'.
    slug            text not null,
    -- The file's own numbering. TEXT, because '5b' is a tab number and
    -- sorting by it numerically is what put tab 1 last on the page.
    number          text not null,
    name            text not null,
    -- Where it sits in the file, which is the order it renders in.
    position        integer not null,

    -- One sentence saying what the tab IS, from the builder.
    what            text,
    -- Where a tab's own source needs stating on the tab: Index Status is
    -- our index check, not Search Console's.
    note            text,

    columns         jsonb not null default '[]'::jsonb,
    -- Denormalised so a tab list does not have to count rows per tab.
    row_count       integer not null default 0,

    created_at      timestamptz not null default now(),

    constraint workbook_tab_natural_key
        unique (domain_id, project_id, version, slug)
);

create index workbook_tab_latest
    on wqa.workbook_tab (domain_id, project_id, version desc);

-- ---------------------------------------------------------------------
-- wqa.workbook_row: one row of one tab, as the spreadsheet holds it.
--
-- `cells` is keyed by the column keys in its tab's `columns`. A jsonb
-- object rather than columns of their own because the tabs genuinely
-- differ: URL Triage is 36 columns of page facts, Action Plan is 7 of
-- roll-up, and the Legend is 3 of prose. Typing them separately would be
-- 16 tables that change whenever the SOP does.
--
-- `row_number` is the order the builder wrote them in, kept because it IS
-- the tab's meaning on several sheets: URL Optimization is sorted by
-- priority tier, and re-sorting it alphabetically would destroy the one
-- thing that tab says.
-- ---------------------------------------------------------------------
create table wqa.workbook_row (
    workbook_row_id uuid primary key default gen_random_uuid(),

    workbook_tab_id uuid not null
                        references wqa.workbook_tab(workbook_tab_id)
                        on delete cascade,
    -- Denormalised for the same reason the rest of this pipeline does it:
    -- a read scoped to a site should not need the join to be safe.
    domain_id       bigint not null
                        references meta.site(domain_id)
                        on delete cascade,

    row_number      integer not null,
    cells           jsonb not null,

    created_at      timestamptz not null default now(),

    constraint workbook_row_natural_key
        unique (workbook_tab_id, row_number)
);

create index workbook_row_by_tab
    on wqa.workbook_row (workbook_tab_id, row_number);

-- Searching a tab in the database rather than in the browser: the reader
-- types into one search box and the tab is 153 rows on a small site and
-- thousands on a large one.
create index workbook_row_cells
    on wqa.workbook_row using gin (cells jsonb_path_ops);

grant select on wqa.workbook_tab, wqa.workbook_row to dashboard_ro;
