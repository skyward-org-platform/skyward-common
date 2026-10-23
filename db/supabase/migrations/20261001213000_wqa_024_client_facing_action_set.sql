-- 20261001213000_wqa_024_client_facing_action_set.sql
-- The actions a client reads, instead of the words we used internally.
--
-- Paul's rule (2026-09-29), reaffirmed in the 10-01 deliverable review: a
-- client-facing action is one of Optimize, Redirect, Canonicalize,
-- Consolidate, Evaluate, Restore, Remove, Investigate or Leave as is. Never
-- "Non-addressable", "Non-indexable" or "Leave as 404". Those three were our
-- bookkeeping and they reached a client workbook.
--
-- WHY THIS IS A DATA MIGRATION AND NOT JUST A CONSTRAINT CHANGE. Adam,
-- 2026-10-01, chose to migrate the historical rows rather than keep both
-- vocabularies alive: one set of words everywhere, so a workbook rebuilt
-- from an older version does not still say "Non-addressable", and so no
-- reader has to know thirteen words to understand nine.
--
-- THIS IS A RENAME, NOT A RE-DECISION, and the distinction matters because
-- this repo has a standing rule against rewriting rows a run did not
-- compute. Nothing here changes what the audit FOUND: every branch below is
-- decided by columns already on the row, and the same mapping is implemented
-- in triage_rules.py so a re-run produces the same answer. What changes is
-- the word, and the sentence in `logic` that has to agree with it.
--
-- "LEAVE AS IS" IS ONE ACTION. The review proposed a "Leave as is (404
-- correct)" variant; a parenthetical reason is not a different instruction.
-- The action is leave it alone, and WHY is the logic column's job.
--
-- THE BIG ONE IS CANONICALIZE. "Non-addressable" meant "not a real page, no
-- action" and on insofast.com it covered 931 URLs, a quarter of the audit.
-- 690 of those answer 200 with NO canonical tag -- live, indexable addresses
-- with nothing telling Google which form to prefer. That is a concrete
-- instruction that was being reported as nothing to do.
--
-- Measured on insofast.com before writing this, the 931 break down as:
--     690  200, no canonical       -> Canonicalize
--       6  200, has canonical      -> Leave as is   (already handled)
--      45  404                     -> Leave as is   (correct answer)
--                                     except with referring domains, which
--                                     is link equity hitting an error page
--     190  no crawl record         -> Leave as is, or Investigate where
--                                     there are value signals to chase

begin;

-- The constraint has to go before the data can move through it.
alter table wqa.page_verdict
    drop constraint if exists page_verdict_action_check;

-- 1. Non-addressable at 2xx with no canonical: the instruction is concrete.
update wqa.page_verdict
   set action = 'Canonicalize',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: it answers '
                || status_code::text
                || ' with no canonical tag, so nothing tells Google which'
                   ' address to prefer. Point it at its canonical form.'
 where action = 'Non-addressable'
   and status_code between 200 and 299
   and coalesce(nullif(trim(canonical_link_element), ''), null) is null;

-- 2. Non-addressable at 2xx that already declares a canonical: handled.
update wqa.page_verdict
   set action = 'Leave as is',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: it answers '
                || status_code::text
                || ' and already declares a canonical, so the duplicate is'
                   ' handled.'
 where action = 'Non-addressable'
   and status_code between 200 and 299
   and coalesce(nullif(trim(canonical_link_element), ''), null) is not null;

-- 3. Non-addressable 404 that something still links to: equity at an error
--    page is a redirect, whatever the URL looks like.
update wqa.page_verdict
   set action = 'Redirect',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: it 404s but still carries'
                   ' referring domains, so the links pointing at it arrive'
                   ' at an error page. 301 to the closest live equivalent.'
 where action = 'Non-addressable'
   and status_code = 404
   and (coalesce(referring_domains, 0) > 0 or coalesce(backlinks, 0) > 0);

-- 4. Non-addressable 404 with nothing linking to it: the 404 is correct.
update wqa.page_verdict
   set action = 'Leave as is',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: it 404s with nothing linking to'
                   ' it, which is the correct answer for a URL like this.'
 where action = 'Non-addressable'
   and status_code = 404;

-- 5. Non-addressable with no crawl record but value signals: we genuinely do
--    not know what is there, which is what Investigate means.
update wqa.page_verdict
   set action = 'Investigate',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: our crawl has no record of it yet'
                   ' it carries value signals, so we cannot say what is'
                   ' there. Re-crawl before deciding.'
 where action = 'Non-addressable'
   and status_code is null
   and (coalesce(referring_domains, 0) > 0
        or coalesce(backlinks, 0) > 0
        or coalesce(sessions, 0) > 0);

-- 6. Everything else that was Non-addressable: nothing to act on.
update wqa.page_verdict
   set action = 'Leave as is',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: nothing to act on.'
 where action = 'Non-addressable';

-- 7. Already excluded from the index, and confirmed as having nothing worth
--    recovering. Confirm rather than change, which is what Leave as is says.
update wqa.page_verdict
   set action = 'Leave as is',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: already excluded from the index'
                   ' and confirmed as having nothing worth recovering, so'
                   ' confirm rather than change.'
 where action = 'Non-indexable';

-- 8. A 404 that is the right answer.
update wqa.page_verdict
   set action = 'Leave as is',
       logic  = coalesce(logic, '')
                || ' Relabelled 2026-10-01: the 404 is correct and adds no'
                   ' crawl overhead.'
 where action = 'Leave as 404';

-- Now the new set, and only the new set: a row carrying a retired word is a
-- row this migration missed, and the constraint is what says so rather than
-- it surfacing in a client workbook.
alter table wqa.page_verdict
    add constraint page_verdict_action_check
    check (action = any (array[
        'Optimize', 'Restore', 'Redirect', 'Canonicalize', 'Consolidate',
        'Remove', 'Evaluate', 'Investigate', 'Leave as is'
    ]));

-- `canonicalized` joins the expected-change set for the same reason.
--
-- NOT "other". In this pipeline "other" means "names no measurable end
-- state, so a deferral carrying it can never be re-checked" -- the outcomes
-- verb reads it that way. Mapping Canonicalize to it would make the audit's
-- largest group permanently unverifiable, and a canonical appearing is
-- plainly measurable: canonical_link_element goes from empty to populated
-- and the next audit can see it.
alter table pipeline.work_item_url
    drop constraint if exists work_item_url_expected_change_check;

alter table pipeline.work_item_url
    add constraint work_item_url_expected_change_check
    check (expected_change = any (array[
        'redirected_away', 'receives_redirect', 'content_rewritten',
        'removed', 'noindexed', 'canonicalized', 'other'
    ]));

comment on column wqa.page_verdict.action is
    'The client-facing action for this URL: Optimize, Restore, Redirect, '
    'Canonicalize, Consolidate, Remove, Evaluate, Investigate or Leave as '
    'is. "Non-addressable", "Non-indexable" and "Leave as 404" were retired '
    '2026-10-01 -- they were our internal words and reached a client '
    'workbook. Leave as is is ONE action covering four states; which one a '
    'row is, is in the logic column.';

commit;
