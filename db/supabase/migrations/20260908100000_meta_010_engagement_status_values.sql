-- 20260908100000_meta_010_engagement_status_values.sql
-- meta.site.engagement_status gains 'canceled' and 'prototype'.
--
-- The original pair, client/prospect, could not describe the domains
-- actually in the system. Classifying the 51 client-owned domains found
-- two shapes it had no word for:
--
--   canceled   a former client. Transport Network Australia is nine
--              domains whose engagement has ended, plus the three Sears
--              properties and Three Trees Delivery. Their pipeline data
--              stays; only the engagement is over.
--   prototype  a build that is not a live site. Seven .replit.app
--              domains, six of them TNA's, one Phil Lasry's.
--
-- Both matter because engagement_status is what anything future should
-- check before spending money on a run, and 'prospect' would have said
-- the opposite of the truth for a wound-down client.
alter table meta.site
    drop constraint site_engagement_status_check;

alter table meta.site
    add constraint site_engagement_status_check
        check (engagement_status in
            ('client', 'prospect', 'canceled', 'prototype'));
