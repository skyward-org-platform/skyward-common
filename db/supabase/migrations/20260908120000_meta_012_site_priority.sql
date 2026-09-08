-- 20260908120000_meta_012_site_priority.sql
-- meta.site gains priority, and the backfill that dropped it is repaired.
--
-- meta.client_domains carries a priority per client-domain link and
-- meta.site had no column for it, so the first backfill silently lost
-- it: nine of the 51 client-owned rows are not NORMAL -- three VERY
-- HIGH, two HIGH, two LOW, two VERY LOW. The data was never at risk
-- because client_domains is untouched, but meta.site could not have
-- replaced it while unable to express the field, and
-- update_client_domains_priority_batch would have had nothing to write
-- to.
--
-- Same five values and same default as client_domains, so the two
-- tables agree and the eventual cutover is a copy rather than a
-- translation.
alter table meta.site
    add column priority text not null default 'NORMAL'
        check (priority in
            ('VERY LOW', 'LOW', 'NORMAL', 'HIGH', 'VERY HIGH'));

-- Repair the rows already migrated.
update meta.site s
   set priority = cd.priority
  from meta.client_domains cd
 where cd.domain_id = s.domain_id
   and not cd.is_competitor
   and s.source = 'client_domains_backfill';

comment on column meta.site.priority is
    'Carried from meta.client_domains. Same five values and default, so '
    'the tables agree until client_domains retires.';
