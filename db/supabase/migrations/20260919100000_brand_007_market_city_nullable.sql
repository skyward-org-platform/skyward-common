-- brand.market.city becomes nullable: a market can be statewide.
--
-- WHY
-- The column was NOT NULL, which encoded an assumption that every market
-- is a city. It is not. InSoFast has 291 state and province pages and no
-- city pages at all, so the client could not be recorded AT ALL -- not
-- partially, not with placeholder cities, not at all. `kb readiness` then
-- blocked Phase 3 on "no market recorded", which was true and unfixable.
--
-- Adam's call, 2026-09-19: "markets can just be statewide. They don't
-- need to have a city."
--
-- WHY THIS NEEDS NO INDEX CHANGE
-- The natural key is (domain_id, city, region, country) and its unique
-- index is already NULLS NOT DISTINCT:
--
--   CREATE UNIQUE INDEX market_natural_key ON brand.market
--     USING btree (domain_id, city, region, country) NULLS NOT DISTINCT
--
-- So two statewide rows for the same (domain_id, region, country) still
-- collide, and build_upsert's ON CONFLICT (domain_id, city, region,
-- country) still matches a null-city row. Under the default NULLS
-- DISTINCT this migration would have silently opened a duplicate-row
-- hole: every statewide upsert would insert a twin instead of updating.
-- It does not, because the index was already written the right way.
--
-- region and country were nullable before this and are unchanged.
--
-- WHAT THIS DOES NOT DO
-- A market with no city does not yet RESOLVE to a DataForSEO location
-- code: resolve_market matches on name, and COUNTRY_TIERS declares no
-- State or Province tier for any country, so Ohio (location 21168, type
-- State) is unreachable even though it exists. That is a separate code
-- change and a separate decision about cost. Until it lands, a statewide
-- market clears the market_codes blocker via `ruling`, which that check
-- already accepts in place of codes.
--
-- Existing rows: 188, of which 0 have a null city. Nothing is rewritten.

alter table brand.market
    alter column city drop not null;

comment on column brand.market.city is
    'City name, or NULL for a market that is a whole state, province or '
    'region. Part of the natural key, whose unique index is NULLS NOT '
    'DISTINCT so statewide rows still deduplicate.';
