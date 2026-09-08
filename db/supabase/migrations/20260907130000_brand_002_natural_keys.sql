-- 20260907130000_brand_002_natural_keys.sql
-- Natural keys, so populate scripts are safely re-runnable.
--
-- Every brand/site table has a bare uuid primary key, which makes a
-- second run insert a twin rather than update the original. The audit
-- requires scripts be "independently re-runnable"; without a natural
-- key that is not achievable, and an agent retrying after a timeout
-- silently doubles the client's data.
--
-- These constraints give each verb an ON CONFLICT target so it can
-- upsert instead of insert.
--
-- Not included: the five one-row-per-site tables (commercial_rules,
-- engagement_context, identity, site.structure, site.crawl_config).
-- Their primary key is already domain_id, so they upsert on it.
--
-- NULLS NOT DISTINCT is used where part of the key is nullable.
-- Postgres treats every null as unique by default, so a plain unique
-- constraint on a nullable column silently permits the duplicates it
-- was added to prevent.

-- Name-keyed: one per name, per site.
alter table brand.persona
    add constraint persona_natural_key unique (domain_id, name);

alter table brand.offering
    add constraint offering_natural_key unique (domain_id, name);

alter table brand.payment_rule
    add constraint payment_rule_natural_key unique (domain_id, name);

alter table brand.proof_asset
    add constraint proof_asset_natural_key unique (domain_id, title);

alter table site.business_location
    add constraint business_location_natural_key unique (domain_id, name);

alter table brand.intake_keyword
    add constraint intake_keyword_natural_key unique (domain_id, keyword);

alter table brand.term_exclusion
    add constraint term_exclusion_natural_key unique (domain_id, pattern);

alter table brand.goal
    add constraint goal_natural_key unique (domain_id, statement);

-- Qualified keys: the text alone is not unique by design.
--
-- lead_rule: the audit keeps the same boundary from two directions as
-- separate rows, so kind is part of the identity.
alter table brand.lead_rule
    add constraint lead_rule_natural_key unique (domain_id, rule, kind);

-- voice_rule: length_target repeats, one row per page type.
alter table brand.voice_rule
    add constraint voice_rule_natural_key
        unique (domain_id, rule, rule_type);

-- brand_term: the same pattern can be both a branded term and an
-- exception, and they must not collide.
alter table brand.brand_term
    add constraint brand_term_natural_key
        unique (domain_id, pattern, kind);

-- Nullable-component keys.
--
-- market: region and country are nullable -- a market whose country
-- cannot be resolved is left null rather than defaulted.
alter table brand.market
    add constraint market_natural_key
        unique nulls not distinct (domain_id, city, region, country);

-- value_input: the key IS the scope. Null market and null offering
-- mean the site-wide default; naming either narrows it. So the four
-- columns together are what make a row distinct.
alter table brand.value_input
    add constraint value_input_natural_key
        unique nulls not distinct
            (domain_id, metric, market_id, offering_id);

-- gbp: keyed on the real identifier, with DEFAULT null handling on
-- purpose. Deduplicates listings whose place_id is known, while
-- leaving rows without one unconstrained -- three KitchenGuard
-- territories legitimately need separate rows before anyone has
-- looked their place ids up. This is the one table where re-running
-- is not fully protected until place ids are filled in.
alter table site.gbp
    add constraint gbp_natural_key unique (domain_id, gbp_place_id);
