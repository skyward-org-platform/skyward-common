-- 20260924160000_meta_022_site_crm_deal_task_id.sql
-- Where this client's record lives in our own CRM.
--
-- Asked for by Adam 2026-09-24, one column and no more.
--
-- Skyward's ClickUp CRM (space 32301019: Deals, Companies, Contacts,
-- Agencies) turned out to be a Phase 0 SOURCE. Reading insofast's records
-- closed seven open questions in one pass -- a job title, the owners'
-- surname (which we had recorded wrongly for a week), who two named
-- people are, the approval chain, the contract value, and that there is
-- no parent company. Two of those had been queued to ask the client.
--
-- They were found by SEARCHING the space for the client's name, which is
-- a guess: filed as a trading name or a legal entity, the search misses
-- or finds the wrong company. Every other container we depend on -- the
-- Drive folder, the ClickUp task, the two Docs -- is recorded on the site
-- row precisely so nobody has to search twice. This is the one that was
-- not.
--
-- THE DEAL ID ONLY. The deal record links to the company, every contact
-- and the agency, so one id reaches the whole set; four ids would drift
-- apart from each other.
--
-- Two known limits, recorded rather than solved:
--
-- * It is a CLIENT-level fact on a SITE row. A client with two sites will
--   hold the same id twice. If a client-level table ever lands, it moves.
-- * It goes stale QUIETLY. A deal moves to renewal or is superseded, and
--   the stored id then points at last year's contract while reading
--   perfectly. It is a pointer to a record, not a source of truth about
--   the commercials.

alter table meta.site
    add column if not exists crm_deal_task_id text;

comment on column meta.site.crm_deal_task_id is
    'ClickUp task id of this client''s DEAL record in the Skyward CRM '
    '(space 32301019). The deal links to the company, contacts and '
    'agency, so this one id reaches all of them. A pointer to a record, '
    'not a source of truth: open the deal rather than trusting a stored '
    'id that may name last year''s contract.';
