-- db/supabase/migrations/0004_projects_created_at_nullable.sql
-- BQ Meta.projects.created_at is null for all legacy rows (add_project never set
-- it and BQ had no default). Relax NOT NULL so the migration is faithful; the
-- now() default still applies to future inserts that omit the column.
alter table meta.projects alter column created_at drop not null;
