-- db/supabase/migrations/0002_meta_missing_columns.sql
-- Real data columns present in BQ Meta.* but omitted from the initial DDL
-- (0001 was built from the columns MetaClient reads). The 12 derived/legacy
-- standardization columns on dataset_catalog/table_catalog are intentionally
-- NOT added — they are dropped at migration time (clean-schema decision).
alter table meta.domains         add column if not exists created_at timestamptz default now();
alter table meta.client_domains  add column if not exists notes text;
alter table meta.project_domains add column if not exists notes text;
