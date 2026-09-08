-- 20260908110000_meta_011_data_access_account_key.sql
-- meta.data_access is keyed on the ACCOUNT, not just the tool.
--
-- The original key, (domain_id, tool), allowed one access per site per
-- tool. That is wrong for a real case already in production: buscharter
-- is linked to two Google Ads accounts, gads_backfill_2720744565 (Bus
-- Charter AUS) and gads_backfill_3045558682 (TNA Bus Hire), which the
-- multi-property resolver unions into one corpus. Under the old key the
-- second account could not be recorded at all.
--
-- Keyed on account_identifier rather than dataset_id deliberately.
-- dataset_id is WHERE THE DATA LANDS; account_identifier is WHOSE
-- ACCESS IT IS. Keying on the dataset gets it backwards twice: two
-- accounts neither of which is exported yet both have a null dataset and
-- would collapse into one row, and re-exporting one account to a new
-- dataset would look like a second access rather than the same one
-- moving.
--
-- NULLS NOT DISTINCT because several tools have no account identifier
-- at all -- screaming_frog is a crawler we run, dataforseo is one API
-- account serving every client. Those are precisely the tools that can
-- only ever have ONE access per site, so the key correctly degenerates
-- to (domain_id, tool) for them. Without NULLS NOT DISTINCT every
-- unknown would read as a different account.
alter table meta.data_access
    drop constraint data_access_domain_id_tool_key;

alter table meta.data_access
    add constraint data_access_natural_key
        unique nulls not distinct (domain_id, tool, account_identifier);

comment on column meta.data_access.account_identifier is
    'Whatever identifies this account or property FOR THIS TOOL: a GA4 '
    'property id, a Google Ads customer id, an Ahrefs project, a '
    'Facebook ad account. For GSC it is the property string itself '
    '(sc-domain:example.com or https://www.example.com/), because one '
    'site legitimately has both a domain property and a url-prefix '
    'property and they must not collide. Null for tools that have no '
    'such thing -- screaming_frog, dataforseo -- which are also the '
    'tools that can only have one access per site.';
