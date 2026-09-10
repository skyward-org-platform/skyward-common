-- 20260910120000_scope_006_in_scope_everywhere.sql
-- One way to say "we are not working on this".
--
-- brand.offering and site.business_location got in_scope in migration
-- 005. brand.market and meta.site_competitors expressed the same idea
-- inside their status columns, so three tables said it two ways.
--
-- WHAT THE CLIENT DOES vs WHAT WE WORK ON is the line. status is theirs;
-- in_scope is ours.
--
-- brand.market.status was active | target | excluded. active and target
-- are about the client -- they serve it, or want to enter it. `excluded`
-- was about us, and is exactly in_scope = false. It goes.
--
-- meta.site_competitors.status is active | rejected, and `rejected`
-- STAYS. It is not the same claim: rejected means this domain is not
-- actually a competitor, a statement of fact about them, while
-- in_scope = false means they are a competitor we are not working
-- against. Step 4 has to raise the first as a contradiction and the
-- second as a scope decision, so collapsing them would lose the
-- distinction it needs.
--
-- Safe to narrow the market enum: no row anywhere uses `excluded`
-- (128 rows: 124 active, 4 target), and nothing in either repo reads
-- brand.market.status or meta.site_competitors.status at all.
alter table brand.market            add column in_scope boolean;
alter table meta.site_competitors   add column in_scope boolean;

comment on column brand.market.in_scope is
    'Whether WE work this market. Null means undecided, which is '
    'different from false. status says what the CLIENT does with it.';

comment on column meta.site_competitors.in_scope is
    'Whether we work against them. Distinct from status = rejected, '
    'which says they are not really a competitor at all.';

alter table brand.market drop constraint market_status_check;

alter table brand.market
    add constraint market_status_check
        check (status in ('active', 'target'));
