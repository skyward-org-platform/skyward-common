-- 20261012180000_pipeline_029_page_snapshot.sql
--
-- A dated reading of the pages a change touched, taken when we raise the
-- work, when it goes live, and at any later date we want to ask again.
--
-- WHY IT EXISTS. pipeline_012 gave a work item its page list and
-- pipeline_028's sibling decision made `completed_at` the ClickUp close
-- date. The moment a change has a date, the next question is what the
-- pages looked like either side of it, and nothing answered that.
-- `pipeline.change_report` is the closest thing and it compares two audits
-- of a whole SITE, so it is dated by audit build rather than by the change.
--
-- WHY IT IS STORED, given most of it is reconstructable (Adam, 2026-10-12).
-- Search Console and Analytics history persists in BigQuery, so any past
-- 28-day window can be read again. We store the reading anyway, for two
-- reasons that have nothing to do with data loss: it is there for quick
-- reference without anybody writing a query, and a frozen dated reading
-- stops two people computing the same change differently. That second one
-- is not hypothetical in this codebase -- Redirect currently has four
-- counts, Investigate four, and pages in scope five.
--
-- The exceptions that genuinely cannot be recovered are map rank and AI
-- citations: nothing stores either as a dated series, so a past value is
-- gone. Their columns are here and stay NULL until a dated store exists.
--
-- TWO TABLES, THE HEADER-AND-ROWS SHAPE THIS SCHEMA ALREADY USES TWICE
-- (change_report/change_finding, work_item_check/work_item_outcome). The
-- facts that explain a whole wall of pages at once -- the window we read,
-- the audit we read it at, whether the reading is trustworthy -- are said
-- ONCE on the header. Repeating them on forty page rows buries them.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

-- ---------------------------------------------------------------------
-- pipeline.page_snapshot: one reading of one work item's pages.
--
-- A TRIGGER, NOT A STEP (Adam, 2026-10-12): it takes a site, a work item
-- and a window, and the same code runs at the close and again at four, six
-- or twelve weeks. `moment` records which occasion this was, so a reader
-- can find the before and the after without arithmetic on dates.
-- ---------------------------------------------------------------------
create table pipeline.page_snapshot (
    page_snapshot_id uuid primary key default gen_random_uuid(),
    domain_id       bigint not null
                        references meta.site(domain_id) on delete cascade,
    project_id      bigint references meta.projects(project_id),

    -- Whose pages these are. Deleting the work item takes its readings:
    -- a snapshot of nothing cannot be interpreted.
    work_item_id    uuid not null
                        references pipeline.work_item(work_item_id)
                        on delete cascade,

    -- Which occasion. 'created' is the before reading, taken when we raise
    -- the work rather than when somebody remembers; 'closed' is the state
    -- when the change went live; 'follow_up' is every later cadence.
    moment          text not null
                        check (moment in ('created', 'closed', 'follow_up')),
    -- The date this reading is ABOUT, which for a close is the operator's
    -- close date and not the day the trigger ran.
    as_of           date not null,
    taken_at        timestamptz not null default now(),

    -- Both windows, as dates, so the reading can be repeated exactly.
    -- The year-earlier pair is what stops a seasonal swing reading as a
    -- result (Paul, 2026-10-12: the city-page rebuild looked like a loss
    -- only because spring was set against summer).
    window_start    date not null,
    window_end      date not null,
    prior_window_start date,
    prior_window_end   date,

    -- Where the page state came from. Per URL at read time, never a
    -- project-wide max, but the version the run resolved is recorded so a
    -- later reader knows what it was looking at.
    audit_module    text,
    audit_version   integer,
    audit_job_id    text,

    -- False when the reading is not trustworthy as a fact about the site:
    -- a crawl older than the claim, a window with no data at all. The rows
    -- are still written and a reader must not treat them as measurements.
    trustworthy     boolean not null default true,
    note            text,

    -- The copy of the work item's own completion fact at reading time, so
    -- this row stands alone beside the dates above.
    completed_at    timestamptz,

    job_id          text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    -- One reading per occasion per date. Re-running the same occasion
    -- overwrites rather than accumulating, because it is derived from
    -- sources that do not change for a past window.
    constraint page_snapshot_natural_key
        unique (work_item_id, moment, as_of),

    constraint page_snapshot_window_is_ordered
        check (window_end >= window_start),
    constraint page_snapshot_prior_window_is_whole
        check ((prior_window_start is null) = (prior_window_end is null)),
    constraint page_snapshot_prior_window_is_ordered
        check (prior_window_end is null
               or prior_window_end >= prior_window_start)
);

create index page_snapshot_by_site
    on pipeline.page_snapshot (domain_id, as_of desc);
create index page_snapshot_by_item
    on pipeline.page_snapshot (work_item_id, moment, as_of desc);

-- ---------------------------------------------------------------------
-- pipeline.page_snapshot_row: one page, as it stood at that reading.
--
-- WIDE ON PURPOSE (Adam, 2026-10-12): everything we hold per page, so a
-- break nobody predicted is visible. The narrow expected-change check sits
-- on top of this and lives on the work item, not here.
--
-- CONTROLS CARRY THE LINK, AND IT IS AN ARRAY. A control is a page of the
-- same kind the change did not touch, and without one a before and after
-- cannot be told apart from the site's own trend. The link points from the
-- control to the pages it controls, rather than from the subject to its
-- controls, because a work item covering forty city pages will usually
-- share one untouched set across all forty: putting the link on the
-- subject would store each control's measures forty times. One row per
-- control per reading, listing every subject it controls (Adam,
-- 2026-10-12, "either works" -- this is the one that does not duplicate).
--
-- A subject row is simply one with controls_urls empty.
-- ---------------------------------------------------------------------
create table pipeline.page_snapshot_row (
    page_snapshot_row_id uuid primary key default gen_random_uuid(),
    page_snapshot_id uuid not null
                        references pipeline.page_snapshot(page_snapshot_id)
                        on delete cascade,
    -- Denormalised for the same reason as work_item_url.domain_id: every
    -- read is scoped by site, and a join to get there would be a join on
    -- every row.
    domain_id       bigint not null
                        references meta.site(domain_id) on delete cascade,

    url             text not null,

    is_control      boolean not null default false,
    -- The subjects this control stands for. Empty on a subject row.
    controls_urls   text[] not null default '{}',

    -- What this URL was supposed to look like once the work was done,
    -- copied from work_item_url so the reading stands beside the claim.
    -- NULL on a control, which was never expected to change.
    expected_change text,

    -- The page's own state. Columns rather than JSON because these are
    -- what a reader filters and sorts on, and what says whether something
    -- broke.
    status_code     integer,
    indexability    text,
    google_indexed  boolean,
    redirect_final_url text,
    redirect_final_status integer,
    redirect_hops   integer,
    redirect_loop   boolean,
    canonical_link_element text,
    canonical_mismatch boolean,
    in_sitemap      boolean,
    current_title   text,
    h1              text,
    meta_description text,
    word_count      integer,
    inlinks         integer,
    outlinks        integer,
    page_depth      integer,

    -- How the audit classified it, which is also how a control is matched
    -- to a subject.
    action          text,
    page_type       text,
    service_category text,
    market          text,

    -- The cluster we assigned, and its keywords. BOTH STAY NULL FOR NOW:
    -- cluster-to-page assignment does not exist. Reserved rather than
    -- omitted so the reading does not change shape when it lands, and
    -- NULL here means "we do not know", never "none".
    cluster_id      bigint,
    cluster_keywords text[],

    -- THE MEASURES, as JSON so a new measure needs no migration. One key
    -- per source, each carrying the current window and the year-earlier
    -- one. Search Console is read at QUERY level and summed to the page,
    -- with CTR and position impression-weighted and never averaged: page
    -- rows alone carry only about 35 to 40% of clicks (Paul, 2026-10-12).
    --
    -- An ABSENT window is recorded as absent, never as zero. A page with
    -- no Search Console rows may have had no clicks or may be below the
    -- privacy threshold, and only the first is a measurement.
    measures        jsonb not null default '{}'::jsonb,

    -- Per-keyword rank for this page, from the ranked-keywords table,
    -- which is keyed on URL. Movement falls out of two readings.
    keyword_ranks   jsonb not null default '[]'::jsonb,
    -- The queries this page actually earns clicks on. Set against
    -- cluster_keywords once that exists, this is a direct test of whether
    -- our clustering is right rather than whether traffic merely moved.
    earning_queries jsonb not null default '[]'::jsonb,

    -- Map rank and AI citations. Null until a dated store for them exists;
    -- unlike the above, a past value for these is unrecoverable.
    map_rank        jsonb,
    ai_citations    jsonb,

    -- Why this page has no reading, when it has none. A row always exists
    -- for a URL on the work item: never silently drop one.
    unread_reason   text,

    created_at      timestamptz not null default now(),

    constraint page_snapshot_row_natural_key
        unique (page_snapshot_id, url, is_control),

    -- The flag and the link have to agree, or a control with no subject
    -- looks like a subject and is counted as one.
    constraint page_snapshot_row_control_names_its_subjects
        check (is_control = (cardinality(controls_urls) > 0)),
    -- A control was never asked to change.
    constraint page_snapshot_row_control_expects_nothing
        check (not is_control or expected_change is null)
);

create index page_snapshot_row_by_snapshot
    on pipeline.page_snapshot_row (page_snapshot_id, is_control);
create index page_snapshot_row_by_url
    on pipeline.page_snapshot_row (domain_id, url);
create index page_snapshot_row_controls
    on pipeline.page_snapshot_row using gin (controls_urls);

-- Updates and deletes only, as on every other table here: a first write is
-- reproducible from the run that made it, and what needs recovering is an
-- overwrite.
create trigger log_change after update or delete on pipeline.page_snapshot
    for each row execute function pipeline.log_change();
create trigger log_change after update or delete on pipeline.page_snapshot_row
    for each row execute function pipeline.log_change();

comment on table pipeline.page_snapshot is
    'One dated reading of one work item''s pages: at creation, at the '
    'ClickUp close, and at any later cadence. A trigger, not a step.';
comment on table pipeline.page_snapshot_row is
    'One page as it stood at that reading. Wide on purpose, so a break '
    'nobody predicted is visible. Controls carry controls_urls naming the '
    'subjects they stand for.';
