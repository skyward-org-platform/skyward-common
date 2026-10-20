-- 20261012230000_pipeline_031_snapshot_freshness.sql
--
-- Which front a reading measured, how fresh each source was, and when the
-- numbers were allowed to stop moving.
--
-- pipeline_029 built the snapshot before three decisions were taken
-- (Adam, 2026-10-12). This adds what they need. The decisions:
--
-- STORE WHAT CANNOT BE RECOMPUTED, COMPUTE WHAT CAN. The crawl state,
-- SERP positions, shared URLs, entities and speed figures are perishable:
-- not captured on the day, gone. Search Console and Analytics are the
-- opposite -- they persist in BigQuery and only get MORE complete, so
-- freezing a two-day-short version stores a worse number than one
-- computed later. Those are computed at render.
--
-- WHAT THAT GIVES UP AND HOW IT IS KEPT. Storing the values was partly to
-- stop two people computing the same change differently. That survives if
-- the WINDOW DEFINITION is stored instead of the values, which
-- pipeline_029 already does: the bounds are columns, and the per-site
-- event names belong beside them. Two readers computing the same window
-- from the same definition land on the same number.
--
-- THREE STATES, so a number that moves is labelled rather than quietly
-- wrong. PROVISIONAL while a source is not yet complete through
-- window_end; SETTLED once it is, computed one final time and frozen;
-- UNREAD where there is nothing. Without this the same snapshot shows
-- different numbers on Tuesday and Friday, which is correct and is also
-- the kind of thing that destroys trust when a client sees it twice.
--
-- TWO FRONTS. Page-level metrics always; cluster-level and competitive
-- metrics only where a cluster is assigned. Phase 1 and Phase 2 both run
-- BEFORE any cluster exists, so the cluster front is empty for them by
-- construction. `fronts_measured` says which was attempted, because a
-- null cluster_id would otherwise mean both "this page has no cluster
-- yet" and "we tried and failed to read it" -- the same absence-versus-
-- zero problem one level up.
--
-- WHY THE PER-MEASURE DETAIL IS NOT COLUMNS. `measures` on the row is
-- already jsonb with one key per source. Each key carries its own
-- envelope: state, values, provenance (pulled today, or cached and how
-- old) and the date the source was complete through. Three parallel JSON
-- columns that must agree with each other is a worse shape than one that
-- cannot disagree with itself, and a snapshot is read whole rather than
-- queried measure by measure. The header carries only what a reader
-- FILTERS on: which readings are still provisional, and which front.
--
-- COST lives in pipeline.cost_event, which already has
-- estimate_expected_usd, estimate_upper_usd, actual_usd, measured_how,
-- provider, module, step and job_id. The snapshot writes a row there per
-- paid pull rather than growing cost columns of its own.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

alter table pipeline.page_snapshot
    -- Per source, the latest date that source had available when its
    -- values were last computed: {"search_console": "2026-10-09",
    -- "analytics": "2026-10-11"}. So "complete through 9 October" is a
    -- fact on the reading rather than an inference at display time.
    add column if not exists sources_complete_through jsonb
        not null default '{}'::jsonb,

    -- When every time-based source became complete through window_end and
    -- the computed values were written once and frozen. NULL means the
    -- reading is still provisional and its traffic figures will move.
    add column if not exists settled_at timestamptz,

    -- Which fronts were attempted. Page is always attempted; cluster only
    -- where a cluster is assigned.
    add column if not exists fronts_measured text[]
        not null default array['page'],

    -- What pulling fresh cost, if anything. The detail is in
    -- pipeline.cost_event; this is the total so a reader does not have to
    -- join to see whether a reading was paid for.
    add column if not exists spend_usd numeric;

alter table pipeline.page_snapshot
    add constraint page_snapshot_fronts_are_known
        check (fronts_measured <@ array['page', 'cluster']
               and 'page' = any(fronts_measured)),
    add constraint page_snapshot_spend_is_not_negative
        check (spend_usd is null or spend_usd >= 0);

-- The reader's own queue: which readings are due to be computed again
-- because they have not settled.
create index page_snapshot_unsettled
    on pipeline.page_snapshot (domain_id, window_end)
    where settled_at is null;

comment on column pipeline.page_snapshot.sources_complete_through is
    'Per source, the latest date it had available when last computed. '
    'Search Console runs two to three days behind and GA4''s most recent '
    'day is incomplete, so a window ending on the close date undercounts '
    'its final days. Recorded rather than smoothed.';
comment on column pipeline.page_snapshot.settled_at is
    'When the computed traffic figures were frozen, because every source '
    'was complete through window_end. NULL means provisional: the numbers '
    'will still move.';
comment on column pipeline.page_snapshot.fronts_measured is
    'Which fronts were attempted: page always, cluster only where one is '
    'assigned. Without it a null cluster_id means both "no cluster yet" '
    'and "we could not read it".';
comment on column pipeline.page_snapshot.spend_usd is
    'What this reading cost to take. Detail per pull in '
    'pipeline.cost_event; this is the total.';
