-- 20261001190000_meta_023_site_white_label.sql
-- Do we appear as Skyward to this client's audience, or as somebody else?
--
-- Asked for by Adam 2026-10-01 to unblock Phases 1 and 2, which need the
-- label. One column and no more.
--
-- White-labelled means the work ships under a partner's brand rather than
-- ours. It changes what a deliverable may say and who it may name, so it is
-- a fact a phase has to be able to read before it writes anything
-- client-facing. busbank.com is not white-labelled; insofast.com is.
--
-- NULLABLE ON PURPOSE, and the default is NOT false.
--
-- A `not null default false` would have been the easy shape and it would
-- assert, of every site already in the table, that we have confirmed it is
-- not white-labelled. We have confirmed that for exactly one of them. The
-- three states are real and they are different things:
--
--   true   -- confirmed white-labelled
--   false  -- confirmed NOT white-labelled
--   null   -- nobody has been asked yet
--
-- Phase 0's start-engagement now asks every time, the same way it asks for
-- engagement_status, so null is a backlog of sites onboarded before this
-- column existed rather than a shrug. A phase that cannot proceed safely
-- without knowing should treat null as "ask", never as "no": getting this
-- wrong in the false direction puts Skyward's name on a partner's
-- deliverable, which is the expensive mistake and the one that cannot be
-- taken back once it has been sent.
--
-- It is a CLIENT-level fact on a SITE row, like crm_deal_task_id before it:
-- a client with two sites will hold the same answer twice. Left that way
-- deliberately, because meta.site is what every phase already reads and a
-- client-level table does not exist yet. If one lands, this moves.

alter table meta.site
    add column if not exists white_label boolean;

comment on column meta.site.white_label is
    'Does our work for this site ship under a partner''s brand rather than '
    'Skyward''s? true = confirmed white-labelled, false = confirmed not, '
    'NULL = nobody has been asked yet. NULL is not "no": a phase that '
    'cannot proceed safely without knowing must treat it as "ask", because '
    'the false-direction mistake puts Skyward''s name on somebody else''s '
    'deliverable and cannot be undone once sent. Phase 0 start-engagement '
    'asks every time.';
