-- 20260910110000_brand_005_in_scope.sql
-- What the client does, and what we work on, are two different things.
--
-- brand.offering.status is current | retired. Both describe the CLIENT:
-- are they still selling it. Whether WE are working on it is a separate
-- axis, and there was no way to say "they still sell it and it is not in
-- our scope" -- which is a normal engagement, not an edge case. Writing
-- it as retired is a lie about their business.
--
-- site.business_location had no status column at all, so neither axis
-- could be expressed: not whether a location is still open, and not
-- whether we are working on it.
--
-- Also touches site.business_location despite the brand_ prefix; the two
-- changes are one idea and splitting them would land half of it.
--
-- in_scope is nullable on purpose. Null means nobody has decided yet,
-- which is the honest state for every row written before anyone asked
-- the client. False is a decision; null is the absence of one, and step
-- 4 needs to tell those apart to know what still has to be confirmed.
alter table brand.offering
    add column in_scope boolean;

comment on column brand.offering.in_scope is
    'Whether WE work on this, not whether the client still sells it -- '
    'that is status. Null means undecided, which is different from false.';

alter table site.business_location
    add column status text not null default 'active'
        check (status in ('active', 'closed')),
    add column in_scope boolean;

comment on column site.business_location.status is
    'Whether the location is still open. Distinct from in_scope, which '
    'is whether we work on it.';

comment on column site.business_location.in_scope is
    'Whether WE work on this location. Null means undecided.';
