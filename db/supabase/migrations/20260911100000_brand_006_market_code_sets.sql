-- 20260911100000_brand_006_market_code_sets.sql
-- A market gets TWO sets of SERP location codes, and Phase 0 stops
-- deciding which one a run should use.
--
-- WHY TWO
--
-- There is no single right granularity. It is a trade between cost and
-- precision, and the trade depends on things Phase 0 does not know:
--
--   general   The coarse set. Usually one DMA. Cheap, because one code
--             covers many markets -- BusBank's 128 markets collapse to
--             95 general codes, eight of them sharing New York. A SERP
--             task is (keyword, location, language), so that collapse
--             is the cost control for a client with many markets.
--
--   granular  The fine set. The codes that cover the service area and
--             ONLY the service area. More precise, and more expensive
--             in proportion to how many markets the client has.
--
-- The case that forces the distinction is Kitchen Guard's
-- Fairfield/Westchester market, #ops-seo-pipeline 2026-06-03. It was run
-- at the New York DMA precisely BECAUSE that DMA covers both counties.
-- Nikhil stopped it:
--
--   "Kitchen Guard does not operate in New York City, which the New York
--    DMA area includes. Need to filter that out. Hood cleaning rules are
--    different for NYC, and only qualified vendors are allowed to
--    operate. KG is not one of them"
--
-- The DMA was geographically correct and commercially wrong: it bundles a
-- mass of demand the client is barred by regulation from serving. With one
-- column there was nowhere to put both answers, so the general one was
-- simply lost and the system was run twice instead:
--
--   "There is no other region code that captures both within dataforseo.
--    If i wanted to capture bot fairfield and westchester I would have to
--    run the system twice, once for each location"
--
-- Kitchen Guard has one market and can afford the fine set. BusBank has
-- 128 and cannot. Same data, different choice, and the choice belongs to
-- whoever is paying for the run -- not to onboarding.
--
-- WHY BOTH ARE LISTS
--
-- granular is obviously plural: excluding the part you do not serve means
-- naming several smaller places.
--
-- general is plural too, though it will hold one code for almost every
-- market. A market that genuinely straddles two DMAs has nowhere else to
-- go, and a scalar that is right 99% of the time is the shape that makes
-- the 1% silently wrong.
--
-- Arrays rather than a primary column plus an overflow column, because a
-- consumer reading the primary and missing the overflow under-pulls
-- silently: the run succeeds, the output looks ordinary, and part of a
-- market was never searched. There is no half of an array to read.
--
-- MIGRATING THE EXISTING COLUMN
--
-- dfs_location_code holds 127 codes, all produced by the DMA/city lane,
-- which is the coarse answer. They become general_location_codes.
-- granular starts empty everywhere and is filled on the next run, so
-- nothing is invented here -- an empty granular set means "not worked out
-- yet", which is true, rather than a guess that looks like data.
alter table brand.market
    add column general_location_codes  integer[] not null default '{}',
    add column granular_location_codes integer[] not null default '{}';

update brand.market
   set general_location_codes = array[dfs_location_code]
 where dfs_location_code is not null;

alter table brand.market drop column dfs_location_code;

comment on column brand.market.general_location_codes is
    'The coarse SERP codes for this market, usually one DMA. What a '
    'client with many markets runs at: one code covers many markets, and '
    'that collapse is the cost control. A list because a market can '
    'straddle two DMAs.';

comment on column brand.market.granular_location_codes is
    'The fine SERP codes covering this market''s service area and only '
    'its service area. What a client with few markets runs at, and the '
    'only correct answer when the covering DMA includes territory the '
    'client cannot serve. Empty means not worked out yet, not none.';
