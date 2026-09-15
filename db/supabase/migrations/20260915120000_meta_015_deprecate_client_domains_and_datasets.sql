-- 20260915120000_meta_015_deprecate_client_domains_and_datasets.sql
-- Soft-drop meta.client_domains and meta.client_datasets by renaming them.
--
-- Replaced by meta.site and meta.data_access (ClickUp 86bbwpuxy). Every
-- known consumer has moved: skyward-common, skyward-seo-pipeline and
-- skyward-knowledge-base. Renaming instead of dropping is deliberate:
-- anything we missed now fails loudly with "relation does not exist",
-- which names the old table, and the data is still here to recover from.
--
-- Checked before applying: no foreign keys, views, functions, triggers or
-- RLS policies reference either table, so the rename breaks nothing
-- inside the database. The only breakage is application SQL still using
-- the old names, which is the point.
--
-- KNOWN to break on apply: skyward-platform (api/routes/datasets.py,
-- api/routes/domains.py), which was scoped out of the cutover, and the
-- legacy MetaClient methods that still name these tables.
--
-- To restore either table:
--   alter table meta.client_domains_deprecated rename to client_domains;
--   alter table meta.client_datasets_deprecated rename to client_datasets;
--
-- Drop for real once nothing has broken for long enough to trust it.

alter table meta.client_domains  rename to client_domains_deprecated;
alter table meta.client_datasets rename to client_datasets_deprecated;

comment on table meta.client_domains_deprecated is
    'DEPRECATED 2026-09-15, renamed from client_domains. Replaced by '
    'meta.site. Kept so a missed consumer fails loudly and the data is '
    'recoverable. See migration meta_015.';

comment on table meta.client_datasets_deprecated is
    'DEPRECATED 2026-09-15, renamed from client_datasets. Replaced by '
    'meta.data_access. Kept so a missed consumer fails loudly and the '
    'data is recoverable. See migration meta_015.';
