-- 20261009002000_site_003_gbp_identifier.sql
--
-- A Google Business Profile can be recorded by whichever identifier the
-- site actually publishes. Approved by Adam, 2026-10-08, after Phase 0
-- found a profile it could not write.
--
-- THE PROBLEM, and it is narrower than it first looked. Both columns
-- already exist: site_001 gave the table `gbp_cid` and `gbp_place_id`,
-- and `gbp_place_id` has always been nullable. What blocked the write
-- was the KEY -- brand_002 made it `unique (domain_id, gbp_place_id)`,
-- and the Brand DNA spec declares key_nulls_distinct, so upsert refuses
-- a row whose key carries a null rather than inserting a duplicate on
-- every run. That refusal is correct and stays; the key was wrong.
--
-- A profile has two identifiers for the same thing and they are not
-- convertible by computation:
--
--   Place ID   ChIJN1t_tDeuEmsRUsoyG83frY4   returned by the Places API
--   CID        10207658233119208591          carried in a maps?cid= link
--
-- Every client before plasry named their profile in the questionnaire or
-- was looked up through the Places API, and BOTH of those hand back a
-- Place ID -- so the key's assumption held every time and nobody noticed
-- it was an assumption. plasry is the first client whose profile was
-- found by reading the site's own JSON-LD, which publishes a maps?cid=
-- link because that is what Google's share button gives you. Nobody
-- publishes a Place ID; it is not user-facing.
--
-- So nothing differs structurally between clients. What differs is the
-- DISCOVERY ROUTE, and site-scraping is a newer Phase 0 capability.
--
-- There is no CID-to-Place-ID endpoint. Searching Places by name and
-- location and taking the top hit is a guess wearing a lookup's
-- clothes, and a wrong Place ID is worse than none because it looks
-- authoritative.
--
-- THE KEY BECOMES (domain_id, business_location_id): one profile per
-- place, which is what a profile is for, and independent of which
-- identifier we happen to hold.
--
-- business_location_id BECOMES REQUIRED, which is the point of the
-- change rather than a side effect. A nullable key part under
-- key_nulls_distinct reintroduces the bug being fixed -- and that exact
-- shape silently overwrote two site-wide brand.value_input rows earlier
-- today, reporting `inserted 2, updated 1`. A profile with no place is
-- not a profile, so requiring it is honest rather than restrictive.
--
-- SAFE BECAUSE THE TABLE IS EMPTY: 0 rows at the time of writing
-- (checked live), so there is nothing to backfill and no pair that
-- could already collide.

alter table site.gbp
    alter column business_location_id set not null;

alter table site.gbp
    drop constraint gbp_natural_key;

alter table site.gbp
    add constraint gbp_natural_key unique (domain_id, business_location_id);

comment on column site.gbp.business_location_id is
    'Which place this profile is for. REQUIRED and part of the natural key: '
    'one profile per business location. A nullable key part would let upsert '
    'insert a duplicate on every run instead of updating.';

comment on column site.gbp.gbp_cid is
    'The CID from a maps?cid= link, which is what a site publishes in its own '
    'schema markup. Hold this OR gbp_place_id or both; they name the same '
    'profile and are not convertible. Neither is the key.';

comment on column site.gbp.gbp_place_id is
    'The Places API identifier. Was the natural key until 20261009, which '
    'made a profile discovered from the site unrecordable: only a CID is '
    'published. Hold this OR gbp_cid or both.';
