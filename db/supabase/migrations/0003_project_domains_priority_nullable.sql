-- db/supabase/migrations/0003_project_domains_priority_nullable.sql
-- BQ Meta.project_domains has a null priority on one legacy row. BQ never
-- enforced NOT NULL here; relax the constraint so the migration is faithful.
-- The 'NORMAL' default still applies to future inserts that omit the column.
alter table meta.project_domains alter column priority drop not null;
