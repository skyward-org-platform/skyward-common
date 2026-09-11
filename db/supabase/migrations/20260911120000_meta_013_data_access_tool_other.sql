-- 20260911120000_meta_013_data_access_tool_other.sql
-- Add 'other' to meta.data_access.tool.
--
-- The nine existing values name a specific product we pull data from.
-- Some registered datasets are not one of those: the first case is
-- analytics_tna_portfolio, a rollup table holding all nine Transport
-- Network Australia GA4 properties in one table keyed on Property_ID.
--
-- It was registered as tool='ga4' on every one of those nine sites,
-- alongside each site's OWN ga4 property. That gave every TNA site two
-- ga4 rows, and WQA's discovery treats anything other than exactly one
-- dataset as an ambiguity needing a human choice -- so every unattended
-- WQA run for those sites raised UnattendedApprovalError at discovery.
--
-- 'other' keeps the link without it masquerading as the site's GA4
-- source. A catch-all rather than a rollup-specific value, because the
-- next odd dataset should not need another migration.

alter table meta.data_access
    drop constraint data_access_tool_check;

alter table meta.data_access
    add constraint data_access_tool_check
        check (tool in
            ('ga4','gsc','google_ads','ahrefs','screaming_frog',
             'dataforseo','gbp','looker','facebook','other'));

comment on column meta.data_access.tool is
    'The product this access row is for. ''other'' covers registered '
    'datasets that are not one of the named tools -- e.g. a multi-site '
    'rollup table -- so they stay linked without being picked up as a '
    'site''s source for that tool.';
