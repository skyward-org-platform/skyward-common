-- The NAP record and the listing profile, on the two tables that are
-- already the spine: site.business_location and site.gbp (site_001).
--
-- One file, deliberately: the location columns, the listing columns and
-- the composite key that local_001's tables reference are one idea (the
-- Local SEO workbook's NAP tab), and splitting them would land half of
-- it.
--
-- WHY THESE COLUMNS
-- The four homeless workbook fields Adam named on 2026-09-29 (GBP
-- categories, VOIP status, email, short description) and the rest of
-- the NAP tab's ~45 scalar rows, which today exist only in a workbook.
-- The existing columns are untouched and keep their meaning: name = the
-- operator's label ('Miami studio'), street_address = line 1, hours =
-- the client's own words, tenure = their own words. The machine forms
-- live beside them (opening_hours, founded_on) and the intake NEVER
-- copies between the two: they are different facts at different
-- precision, and copying is how two columns drift (meta_018, brand_008).
--
-- jsonb FOR THE REPEATING GROUPS, WITH PROVENANCE PER JSON PATH.
-- Hours (7 days x 2 slots), key profiles (10+), service areas (up to
-- 20), attributes (~45 yes/no), licensing, previous identities: one
-- jsonb each, because each is a per-listing group written and approved
-- as a group and never queried across sites. The per-cell source, tag
-- and approval state the workbook carries (its columns E and F) live in
-- field_sources keyed by path ('profiles.instagram',
-- 'opening_hours.saturday', 'service_areas[ChIJ...]'), so a client
-- correcting one URL approves that URL and nothing else.
--
-- gbp_cid: unique per site, digits only. The content and competitor
-- rows in local_001 key on the cid, because a caller names a listing by
-- cid and never by uuid, so a listing must be addressable by it.
--
-- A LISTING WITHOUT A CID CANNOT BE WORKED, AND THAT IS DELIBERATE.
-- site_003 established that a profile may be known by its cid, by its
-- place_id, or by both, and that neither is the natural key: the key
-- stays (domain_id, business_location_id). This migration does not
-- change that. But local_content and local_competitor require a cid, so
-- a listing we hold only a place_id for can carry no content and no
-- competitors until somebody supplies the cid. That is the intended
-- reading, not an oversight: the local lane matches identity cid-first,
-- the GBP API returns a mapsUri carrying the cid for every profile
-- data@ manages, and the readiness check 'local_listing' blocks a site
-- whose scope of work includes local SEO while no listing has one. A
-- profile discovered from the open web with only a place_id belongs in
-- local_competitor, which accepts a nocid: fallback.
--
-- Nulls are distinct in the per-site unique, so a listing known only by
-- place_id stays unconstrained and recordable.
--
-- PRECONDITION, run before applying (must return no rows; verified
-- empty against skyward-ops on 2026-10-21, when site.gbp held 1 row):
--     select domain_id, gbp_cid, count(*) from site.gbp
--      where gbp_cid is not null group by 1, 2 having count(*) > 1;
--
-- Designed by Abdullah Sajjad (skyward-local-seo), 2026-10-09.

alter table site.business_location
    add column locality             text,
    add column public_name          text,
    add column address_line_2       text,
    add column city                 text,
    add column region               text,
    add column postcode             text,
    add column country              char(2) references meta.country(iso2),
    add column email                text,
    add column citation_email       text,
    add column website_url          text,
    add column telephone_site       text,
    add column telephone_line_type  text
                                    constraint business_location_line_type
                                    check (telephone_line_type is null
                                           or telephone_line_type in
                                           ('landline', 'mobile', 'voip',
                                            'toll_free', 'unknown')),
    add column fax                  text,
    add column employee_count       integer
                                    constraint business_location_employees
                                    check (employee_count is null
                                           or employee_count >= 0),
    add column founded_on           date,
    add column opening_hours        jsonb,
    add column contact              jsonb,
    add column profiles             jsonb,
    add column payment_methods      text[],
    add column languages            text[],
    add column licensing            jsonb,
    add column brand_assets         jsonb,
    add column previous_identities  jsonb,
    add column citation_refs        jsonb,
    add column reviews_as_of        date,
    add column field_sources        jsonb not null default '{}'::jsonb;

create index business_location_locality_idx
    on site.business_location (domain_id, locality);

comment on column site.business_location.locality is
    'The town/city token the local lane keys a run on (''Brisbane''). '
    'P0 section 2 fills it; the intake fills it on create. name stays '
    'the human label.';

comment on column site.business_location.public_name is
    'The location name as published on citations (NAP tab ''Location '
    'Name''). name is the operator''s label; gbp.gbp_name the live '
    'listing title; gbp.proposed_name our proposal.';

comment on column site.business_location.street_address is
    'Line 1. address_line_2, city, region, postcode, country are the '
    'split form.';

comment on column site.business_location.hours is
    'The client''s own words (''8am-8pm Mon-Fri''). opening_hours is the '
    'machine form; neither is derived from the other.';

comment on column site.business_location.opening_hours is
    '{"monday":[["08:00","17:00"]],...,"sunday":[]}. [] = closed, '
    '[["00:00","24:00"]] = 24 hours, key absent = unknown. At most two '
    'slots per day. Approval per day in field_sources.';

comment on column site.business_location.telephone_site is
    'The number the website shows when it differs from telephone (the '
    'listing). The pair is the NAP-mismatch warning.';

comment on column site.business_location.telephone_line_type is
    'VOIP status. Null until a line-type lookup exists; raised as a '
    'question answerable_by skyward.';

comment on column site.business_location.citation_email is
    'The verification inbox used on citations. email is the public one.';

comment on column site.business_location.founded_on is
    'Date of company formation. A year-only answer is stored as 1 Jan '
    'with field_sources["founded_on"].precision = ''year''. tenure stays '
    'the client''s own words.';

comment on column site.business_location.contact is
    '{name, role, phone, email, mobile, fax}: the contact person rows.';

comment on column site.business_location.profiles is
    '{facebook, x, linkedin, pinterest, instagram, tiktok, youtube, '
    'apple_maps, yelp, bing_places, <industry-profile slug>...}: URLs '
    'only. Never a login (kb-ingest rule).';

comment on column site.business_location.licensing is
    '[{association, region, year, license_number}]';

comment on column site.business_location.brand_assets is
    '{logo_url, profile_photo_url, image_folder_url, video_url}';

comment on column site.business_location.previous_identities is
    '{names: [], addresses: [], phones: [], websites: []}';

comment on column site.business_location.citation_refs is
    '{client_reference, location_id, unique_location_ref, category_id}: '
    'the citation platform''s own references. gbp.store_code is '
    'Google''s.';

comment on column site.business_location.reviews_as_of is
    'When review_rating/review_count were observed. The series is '
    'BigQuery: jepto_gmb_<client> when connected, else '
    'SEOPipelinePlayground.local_gbp_reviews.';

comment on column site.business_location.field_sources is
    'Per-field provenance keyed by column or JSON path: {"telephone": '
    '{source, detail, tag, state, job_id, as_of, conflict}}. source is a '
    'sources[] token; detail the line the workbook prints; tag '
    'automatic|checkpoint|warning|human; state '
    'draft|checkpoint|approved|pushed; conflict {source: value, ...}. '
    'Replaced wholesale by upsert, so the writer reads and merges.';

alter table site.gbp
    add column proposed_name            text,
    add column primary_category         text,
    add column additional_categories    text[] not null default '{}',
    add column verified                 boolean,
    add column is_service_area_business boolean,
    add column service_area_type        text,
    add column service_areas            jsonb not null default '[]'::jsonb,
    add column attributes               jsonb not null default '{}'::jsonb,
    add column short_description        text
                                        constraint gbp_short_description_len
                                        check (short_description is null
                                               or char_length(
                                                   short_description) <= 300),
    add column description              text
                                        constraint gbp_description_len
                                        check (description is null
                                               or char_length(
                                                   description) <= 750),
    add column primary_keyword          text,
    add column secondary_keywords       text[] not null default '{}',
    add column keyword_evidence         jsonb,
    add column listing_as_of            timestamptz,
    add column listing_snapshot         jsonb,
    add column audit                    jsonb not null default '{}'::jsonb,
    add column pack_metrics             jsonb not null default '{}'::jsonb,
    add column pack_file_id             text,
    add column pack_revision_written    text,
    add column pack_revision_ingested   text,
    add column field_sources            jsonb not null default '{}'::jsonb;

alter table site.gbp
    add constraint gbp_cid_digits
        check (gbp_cid is null or gbp_cid ~ '^[0-9]+$'),
    add constraint gbp_cid_unique unique (domain_id, gbp_cid);

-- A Google cid names one listing globally. This says a listing lives on
-- exactly one meta.site row (a path-scoped territory's listing cannot
-- also hang off the root site) and makes a list_labels join on gbp_cid
-- sound. Adam ruled to keep it, 2026-10-21.
create unique index gbp_cid_global_uq on site.gbp (gbp_cid)
    where gbp_cid is not null;

comment on column site.gbp.gbp_cid is
    'The CID from a maps?cid= link, which is what a site publishes in '
    'its own schema markup. Hold this OR gbp_place_id or both; they '
    'name the same profile and are not convertible. NEITHER IS THE '
    'NATURAL KEY, which stays (domain_id, business_location_id) per '
    'site_003. Digits only, unique per site, and REQUIRED before a '
    'listing can hold local_content or local_competitor rows, which key '
    'on it. The synthetic nocid: ids never land here.';

comment on column site.gbp.proposed_name is
    'The GBP title we propose (SOP 1.4). gbp_name stays the live title.';

comment on column site.gbp.service_areas is
    '[{place_name, place_id}], up to 20. Approval per element in '
    'field_sources["service_areas[<place_id>]"].';

comment on column site.gbp.attributes is
    '{attribute_key: true|false} in GBP vocabulary '
    '(has_wheelchair_accessible_parking, pay_credit_card...). The raw '
    'API grouping stays inside listing_snapshot.';

comment on column site.gbp.keyword_evidence is
    '[{keyword, search_volume, rank_absolute, source}]: why these '
    'secondary keywords, from ranked_keywords/keyword_overview.';

comment on column site.gbp.listing_snapshot is
    'The normalised live listing as last observed (title, phone, '
    'address, hours, categories, description, rating, attributes raw, '
    'photos, source). The diff base the GBP write adapter needs; '
    'refreshed after each push.';

comment on column site.gbp.audit is
    '{check_key: {value, source, as_of}} for the GBP Audit tab rows '
    '(keyword_in_title, locality_in_title, has_localbusiness_markup, '
    'has_logo, has_cover_photo, interior_photos...).';

comment on column site.gbp.pack_metrics is
    'Keyed by DataForSEO location_code as text: {"1000567": '
    '{keywords_analyzed, pack_appearances, share_of_keywords, '
    'avg_pack_rank, best_rank, keywords_won, opportunity_keywords[], '
    'market_tokens[], keywords_dropped_other_market, pack_job_ids[], '
    'pack_max_version, pack_latest_ingest, as_of}}. Summary and pin '
    'only; per-keyword ranks are read from keyword_local_pack_rankings '
    'through the pin.';

comment on column site.gbp.pack_file_id is
    'Google Sheet id of this listing''s NAP pack (the template, '
    'converted to a native Sheet). Id, not URL (meta_018).';

comment on column site.gbp.pack_revision_written is
    'Drive revision we last wrote. Text, never arithmetic.';

comment on column site.gbp.pack_revision_ingested is
    'Drive revision we last read into the tables. "What changed" is '
    'measured from the later of written/ingested, so our own write is '
    'never read back as a client answer.';
