-- 20260909140000_brand_003_value_input_shape.sql
-- brand.value_input could not hold the commercial data clients give us.
--
-- Found on the first full BusBank run, which left the table empty and
-- said why. Its persona sheet carries, for each of four personas:
--
--   average_transaction_value   $5,000 - $20,000 per event
--   lifetime_value_(ltv)        $50,000+ over several years
--   customer_acquisition_cost   $500 - $2,000
--   repeat_purchase_rate        40%+
--
-- Four things stopped every one of those being stored.
--
-- 1. No persona_id. The figures are per-persona -- an event planner's
--    average booking is not an HR director's -- and with no column for
--    whose number it is, two personas' aov also collided on the key,
--    both being (domain_id, 'aov', null, null).
--
-- 2. metric allowed aov, value_per_lead and conversion_rate only, so
--    three of the four figures above had nowhere to go.
--
-- 3. value was a single NOT NULL numeric and every real answer is a
--    RANGE. Storing one meant inventing a midpoint.
--
-- 4. Nothing recorded what the number is per. "$8,000-$40,000+/month"
--    and "$20,000 per event" look comparable and are not.
--
-- The table is empty across every client, so nothing is migrated.
alter table brand.value_input
    add column persona_id uuid
        references brand.persona(persona_id),
    add column value_low  numeric,
    add column value_high numeric,
    add column basis text,
    alter column value drop not null;

comment on column brand.value_input.value_high is
    'Null with value_low set means an open-ended range: the source said '
    '"$50,000+" or "40%+". Null with value set means a point value.';

comment on column brand.value_input.persona_id is
    'Whose number this is. Null means it applies to the site as a whole.';

-- persona_id joins the key: without it, one aov per site is all that
-- can be stored. NULLS NOT DISTINCT is kept so a genuinely site-wide
-- figure still deduplicates against itself on a re-run.
alter table brand.value_input
    drop constraint value_input_natural_key;

alter table brand.value_input
    add constraint value_input_natural_key
        unique nulls not distinct
        (domain_id, metric, persona_id, market_id, offering_id);

alter table brand.value_input
    drop constraint value_input_metric_check;

alter table brand.value_input
    add constraint value_input_metric_check
        check (metric in ('aov', 'value_per_lead', 'conversion_rate',
                          'ltv', 'cac', 'repeat_rate'));

-- A row has to say something. Either a point value or the low end of a
-- range; an open-ended top is fine, an empty row is not.
alter table brand.value_input
    add constraint value_input_has_a_value
        check (value is not null or value_low is not null);

alter table brand.value_input
    add constraint value_input_basis_check
        check (basis is null or basis in
              ('per_event', 'per_trip', 'per_month', 'per_year',
               'per_lead', 'per_customer', 'percent', 'total'));

create index value_input_persona_idx
    on brand.value_input (persona_id);
