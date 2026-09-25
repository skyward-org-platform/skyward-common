-- 20260925120000_wqa_004_proposed_destination.sql
--
-- Where a redirecting URL SHOULD point, beside where it currently goes.
--
-- Step 5 of the spec is three things: resolve every redirecting URL,
-- propose a destination, and verify the proposal is live. Only the first
-- was built. The `redirect_final_*` columns hold what the crawl MEASURED
-- -- the chain as it is today -- and a proposal must never be written
-- into them, or a suggestion becomes a measurement on the next read.
--
-- So four columns, all nullable, all empty on every row that needs
-- nothing: a redirect already landing on a live page is not a question.
--
-- `ship` is deliberately narrow: true only when the proposal is both live
-- and a close match. A live home page is still a guess about where a page
-- belongs, and redirecting to the home page is the soft-404 pattern we
-- most often find on a client's site, so it is proposed with its reason
-- and never marked shippable.
--
-- Grants: none written here. Default privileges on this schema grant adam
-- and pipeline_bot. Apply as postgres, or that default does not fire.

alter table wqa.page_verdict
    add column if not exists proposed_destination text,
    add column if not exists proposed_destination_reason text,
    add column if not exists proposed_destination_status integer,
    add column if not exists proposed_destination_ship boolean;

comment on column wqa.page_verdict.proposed_destination is
    'Where this redirecting URL should point, proposed by step 5 from the '
    'site''s own live pages. A suggestion, never a measurement: what the '
    'crawl found is in redirect_final_url.';

comment on column wqa.page_verdict.proposed_destination_reason is
    'Why that destination: the page''s own canonical, the closest live '
    'slug, the live parent section, or the home page as a last resort.';

comment on column wqa.page_verdict.proposed_destination_status is
    'The HTTP status the proposed destination actually returned when step '
    '5 fetched it. Null means it could not be checked.';

comment on column wqa.page_verdict.proposed_destination_ship is
    'True only when the proposal is live AND a close match. Anything else '
    'is for a person to confirm, with the reason beside it.';
