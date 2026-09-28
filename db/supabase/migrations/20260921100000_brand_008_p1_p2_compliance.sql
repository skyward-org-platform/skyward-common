-- The last two decided-but-unbuilt Phase 0 items against the V1 contract.
--
-- ============================================================
-- P2: a market is in scope unless somebody says otherwise
-- ============================================================
--
-- Adam, 2026-09-21: "Let's default yes."
--
-- This REVERSES the rule the p0-research skill followed until now, which
-- was to leave in_scope null ("undecided, because nobody has decided") and
-- raise a question. The two produce opposite behaviour. Under null, every
-- new market is an open question and Phase 3 waits on the answer. Under
-- default-yes, a market is worked unless the client limits or excludes it.
-- Adam's direction of 2026-09-15 was already default-yes; the skill had
-- drifted from it.
--
-- The trade: default-yes is faster, null is safer, because Phase 3 spends
-- money per market and a wrong "yes" gets paid for. Adam chose speed, and
-- the market_codes readiness check still gates on every in-scope market
-- having a location code, so an unexpected market still cannot reach a
-- paid pull without being coded first.
--
-- BACKFILL: the 59 existing nulls are all InSoFast's, written under the old
-- rule while scope was undecided. Nobody excluded them, so under the new
-- rule they are in scope. Their open "which are in scope?" gap question
-- closes itself on the next `kb gaps --write`, because the column is no
-- longer empty.
--
-- COUNTRIES are derived, not stored: a country is in scope if any of its
-- markets are. meta.country is a shared ISO reference table, not
-- per-client, so there is nowhere to record "this client does not do
-- Canada" without a new per-site table -- and deriving it needs none.

alter table brand.market
    alter column in_scope set default true;

update brand.market
   set in_scope = true
 where in_scope is null;

comment on column brand.market.in_scope is
    'In scope unless somebody excludes it (default true, Adam 2026-09-21). '
    'False records a deliberate exclusion the client confirmed. Countries '
    'are derived from this: in scope if any of their markets are.';

-- ============================================================
-- P1: the data access record carries history depth and GA4 events
-- ============================================================
--
-- The V1 contract, table row 1c: "Data access record per tool, incl. GA4
-- event names." And the spine: "data access record: per tool, account ids,
-- datasets, history depth." Neither existed.
--
-- GA4 EVENT NAMES ALREADY LIVE ELSEWHERE, which is why this is additive.
-- They are in the WQA configs today, per client:
--
--     wqa_configs/busbank.yaml     conversion_events: [completed_rfq]
--                                  ecom_events:       [purchase]
--
-- across 20 YAMLs. Adam, 2026-09-21: data_access becomes the source, but
-- BACKWARDS COMPATIBLE -- WQA keeps reading its YAML until it is moved to
-- this schema, and the V1 contract now tracks that move as its own item.
--
-- DELIBERATELY NOT BACKFILLED. Populating these from the YAMLs now would
-- create two live copies of the same fact with nothing reading one of
-- them, which is exactly the drift option A was chosen to avoid. They are
-- backfilled at the moment WQA switches over, so there is never a window
-- with two live sources.
--
-- HISTORY DEPTH IS A DATE, not a duration. "Data available from 2024-03-01"
-- stays true; "18 months" is wrong the day after it is written.
--
-- All nullable, so nothing that writes data_access today has to change.

alter table meta.data_access
    add column ga4_conversion_events text[],
    add column ga4_ecom_events text[],
    add column history_available_from date;

comment on column meta.data_access.ga4_conversion_events is
    'GA4 conversion event names for this property. Intended source of '
    'truth; WQA still reads wqa_configs/*.yaml until migrated.';
comment on column meta.data_access.ga4_ecom_events is
    'GA4 ecommerce event names. Same transition as ga4_conversion_events.';
comment on column meta.data_access.history_available_from is
    'Earliest date this tool holds data for. A date rather than a duration, '
    'because a duration goes stale the day after it is written.';
