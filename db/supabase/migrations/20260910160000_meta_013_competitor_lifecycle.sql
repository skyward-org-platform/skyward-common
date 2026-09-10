-- 20260910160000_meta_013_competitor_lifecycle.sql
-- A competitor can stop existing, or move, and neither is "rejected".
--
-- status was active | rejected, where rejected means "not really a
-- competitor" -- a claim about whether they compete with us. It has no
-- way to say "this company is gone" or "this company now lives at a
-- different domain", which are claims about THEM and are things a
-- research run establishes for itself rather than asking an operator.
--
-- The first real step 4 run hit the moved case twice in discovery:
-- usacoachbus.com 301s to chicagomotorcoachinc.com, viatrailways.com to
-- viacharter.com. It stored the destinations, which is right, but had
-- nowhere to record that the old domains were the same operation.
--
--   active      competes with us
--   rejected    does not actually compete with us
--   defunct     the business is gone
--   superseded  the business moved; superseded_by names where to
--
-- A reason becomes mandatory for anything that is not active. A row
-- taken out of play without one is a decision nobody can audit, and the
-- next run re-discovers the domain and puts it back.
--
-- Every existing row is active, so nothing is migrated and the new
-- constraint cannot fail on current data.
alter table meta.site_competitors
    add column superseded_by bigint references meta.domains(domain_id);

comment on column meta.site_competitors.superseded_by is
    'Where this competitor went. Set with status = superseded, e.g. a '
    '301 from the old domain to a new brand.';

alter table meta.site_competitors
    drop constraint site_competitors_status_check;

alter table meta.site_competitors
    add constraint site_competitors_status_check
        check (status in ('active', 'rejected', 'defunct', 'superseded'));

alter table meta.site_competitors
    add constraint site_competitors_inactive_needs_a_reason
        check (status = 'active' or notes is not null);

alter table meta.site_competitors
    add constraint site_competitors_superseded_names_a_successor
        check (status <> 'superseded' or superseded_by is not null);
