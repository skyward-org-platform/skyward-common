-- 20260924150000_pipeline_011_deliverable_slug_and_audience.sql
-- A phase can leave more than one deliverable of the same KIND, and only
-- some of them are for the client.
--
-- Ruled by Adam 2026-09-24. Phase 0 leaves two documents -- the client
-- questionnaire and the open-questions Doc -- and both are kind='doc', so
-- the natural key (domain_id, project_id, module, version, kind) can hold
-- only one of them. `kind` was doing a job it cannot do: it says what a
-- file IS, not which deliverable it is. Phase 1 gets away with it today
-- only because its two files happen to be a workbook and a deck, and it
-- hits the same wall the day it writes two workbooks -- one per market,
-- say.
--
-- So a `slug` names the deliverable and carries the key. Existing rows
-- take their kind as their slug, which is exactly what distinguished them
-- before, so nothing moves.
--
-- AUDIENCE is the second half, and it is a safety property rather than a
-- nicety. Adam: "in this case the questionnaire is the only true
-- deliverable as it's client facing". The open-questions Doc is INTERNAL
-- and permanently so -- its Internal tab is precisely what must never be
-- shared, which is why both tabs live in one file and that file is never
-- sent. A deliverables list that shows the two side by side, with nothing
-- saying which is which, is an invitation to send the wrong one.
--
-- Nullable on purpose: existing Phase 1 rows are left alone rather than
-- guessed at, since whether a WQA workbook is sent to the client is that
-- lane's call, not this migration's. NULL reads as "nobody has said".
--
-- Readers: the operator dashboard lists deliverables per phase and will
-- show the audience where it is recorded. The Phase 1 writer upserts
-- through the registry spec for this table, so that spec's natural_key
-- moves from kind to slug in the same change (P1-P2 thread).

alter table pipeline.deliverable
    add column if not exists slug text;

-- What already distinguished the existing rows becomes their name.
update pipeline.deliverable
   set slug = kind
 where slug is null;

alter table pipeline.deliverable
    alter column slug set not null;

alter table pipeline.deliverable
    drop constraint if exists deliverable_natural_key;
alter table pipeline.deliverable
    add constraint deliverable_natural_key
    unique (domain_id, project_id, module, version, slug);

alter table pipeline.deliverable
    add column if not exists audience text;
alter table pipeline.deliverable
    drop constraint if exists deliverable_audience_check;
alter table pipeline.deliverable
    add constraint deliverable_audience_check
    check (audience is null or audience in ('client', 'internal'));

comment on column pipeline.deliverable.slug is
    'Stable name for this deliverable within its phase and version, e.g. '
    'questionnaire, open-questions-internal, wqa-workbook. Carries the '
    'natural key; kind says what the file is, slug says which one it is.';
comment on column pipeline.deliverable.audience is
    'client = the client receives it; internal = never shared. NULL means '
    'nobody has said, which is not the same as internal.';
